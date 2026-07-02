"""
核心监控引擎 - L4/L7 检测、证书解析、CDN 识别
"""

import ssl
import socket
import time
import logging
import struct
import json
import re
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse
from dataclasses import dataclass, field, asdict
from typing import Optional
import httpx

from config import config, SITES

logger = logging.getLogger("monitor")


# ════════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class CheckResult:
    """单次检测结果"""
    site_name: str
    check_type: str           # l4 / l7 / cert / cdn / docker
    ok: bool
    message: str
    latency_ms: float = 0.0   # 延迟
    details: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


@dataclass
class SiteStatus:
    """站点状态 (含历史告警计数)"""
    name: str
    url: str
    checks: dict              # check_type -> CheckResult
    consecutive_failures: int = 0
    last_ok: Optional[str] = None
    last_check: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
            "consecutive_failures": self.consecutive_failures,
            "last_ok": self.last_ok,
            "last_check": self.last_check,
        }


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def resolve_host(url_or_host: str) -> tuple[str, int]:
    """解析 URL 或 host:port，返回 (host, port)"""
    if url_or_host.startswith("http"):
        parsed = urlparse(url_or_host)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    elif ":" in url_or_host:
        host, port_s = url_or_host.rsplit(":", 1)
        host = host.strip("[]")  # 处理 IPv6
        port = int(port_s)
    else:
        host = url_or_host
        port = 443
    return host, port


def parse_host_port(url: str) -> tuple[str, int, str]:
    """解析 URL，返回 (host, port, scheme)"""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme or "https"
    return host, port, scheme


# ════════════════════════════════════════════════════════════
# L4 TCP 检测
# ════════════════════════════════════════════════════════════

def check_l4(host: str, port: int) -> CheckResult:
    """TCP 连通性 + 延迟检测"""
    start = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(config.l4.connect_timeout)
        sock.connect((host, port))
        latency = (time.perf_counter() - start) * 1000
        sock.close()
        return CheckResult(
            site_name="", check_type="l4", ok=True,
            message=f"TCP 连接成功 (port {port})",
            latency_ms=round(latency, 2),
            details={"host": host, "port": port}
        )
    except socket.timeout:
        return CheckResult(site_name="", check_type="l4", ok=False,
            message=f"TCP 连接超时 ({config.l4.connect_timeout}s)", details={"host": host, "port": port})
    except ConnectionRefusedError:
        return CheckResult(site_name="", check_type="l4", ok=False,
            message=f"连接被拒绝 (port {port})", details={"host": host, "port": port})
    except socket.gaierror as e:
        return CheckResult(site_name="", check_type="l4", ok=False,
            message=f"DNS 解析失败: {e}", details={"host": host})
    except Exception as e:
        return CheckResult(site_name="", check_type="l4", ok=False,
            message=f"TCP 错误: {type(e).__name__}: {e}", details={"host": host, "port": port})


# ════════════════════════════════════════════════════════════
# L7 HTTP 检测
# ════════════════════════════════════════════════════════════

def check_l7(url: str, site_cfg: dict) -> CheckResult:
    """HTTP 七层检测: 状态码 + 关键词 + 延迟"""
    start = time.perf_counter()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(config.l7.request_timeout),
            follow_redirects=config.l7.follow_redirects,
            verify=True,
            http2=True,
        ) as client:
            resp = client.get(url, headers={
                "User-Agent": "SiteMonitor/1.0 (+https://github.com/site-monitor)",
                "Accept": "*/*",
            })

        latency = (time.perf_counter() - start) * 1000
        status_ok = resp.status_code in config.l7.expected_status_codes
        slow_threshold = site_cfg.get("l7_slow_threshold_ms", config.l7.slow_threshold_ms)
        is_slow = latency > slow_threshold

        # 关键词检测
        keyword_ok = True
        missing_kw = []
        expected = site_cfg.get("l7_expected_keywords", config.l7.expected_keywords)
        if expected:
            body_lower = resp.text.lower()
            for kw in expected:
                if kw.lower() not in body_lower:
                    keyword_ok = False
                    missing_kw.append(kw)

        ok = status_ok and keyword_ok
        msgs = []
        if not status_ok:
            msgs.append(f"状态码 {resp.status_code} 不在 {config.l7.expected_status_codes}")
        if missing_kw:
            msgs.append(f"缺少关键词: {missing_kw}")
        if is_slow:
            msgs.append(f"响应慢 ({latency:.0f}ms > {slow_threshold}ms)")
        if not msgs:
            msgs.append(f"HTTP OK [{resp.status_code}], {latency:.0f}ms")

        return CheckResult(
            site_name="", check_type="l7", ok=ok,
            message="; ".join(msgs),
            latency_ms=round(latency, 2),
            details={
                "status_code": resp.status_code,
                "content_length": len(resp.content),
                "content_type": resp.headers.get("content-type", ""),
                "server": resp.headers.get("server", ""),
                "response_time_ms": round(latency, 2),
                "is_slow": is_slow,
                "keywords_found": not bool(missing_kw),
            }
        )
    except httpx.TimeoutException:
        return CheckResult(site_name="", check_type="l7", ok=False,
            message=f"HTTP 请求超时 ({config.l7.request_timeout}s)", latency_ms=(time.perf_counter()-start)*1000)
    except httpx.TLSError as e:
        return CheckResult(site_name="", check_type="l7", ok=False,
            message=f"TLS 错误: {e}", latency_ms=(time.perf_counter()-start)*1000)
    except Exception as e:
        return CheckResult(site_name="", check_type="l7", ok=False,
            message=f"HTTP 错误: {type(e).__name__}: {e}", latency_ms=(time.perf_counter()-start)*1000)


# ════════════════════════════════════════════════════════════
# 证书检测
# ════════════════════════════════════════════════════════════

def check_cert(url: str) -> CheckResult:
    """TLS 证书检测: 过期时间、协议版本、SAN"""
    host, port, _ = parse_host_port(url)
    warnings = []
    errors = []
    details = {}

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        # 兼容 TLS 1.2/1.3
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=config.l4.connect_timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert(binary_form=True)
                proto = ssock.version()
                cipher = ssock.cipher()[0] if ssock.cipher() else "N/A"
        latency = (time.perf_counter() - start) * 1000

        # 解析证书
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        cert_obj = x509.load_der_x509_certificate(cert, default_backend())

        subject = cert_obj.subject.rfc4514_string()
        issuer = cert_obj.issuer.rfc4514_string()
        not_before = cert_obj.not_valid_before_utc
        not_after = cert_obj.not_valid_after_utc
        now = datetime.now(timezone.utc)
        days_left = (not_after - now).days

        # SAN 提取
        san_list = []
        try:
            for ext in cert_obj.extensions:
                if ext.oid == x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME:
                    san_list = [str(name.value) for name in ext.value]
        except Exception:
            pass

        # TLS 协议检查
        if config.cert.check_protocol:
            if proto not in ("TLSv1.3", "TLSv1.2"):
                errors.append(f"协议过旧: {proto} (建议 >= TLSv1.2)")

        # 过期检查
        ok = True
        if days_left < 0:
            ok = False
            errors.append(f"证书已过期 {abs(days_left)} 天!")
        elif days_left <= config.cert.critical_days:
            ok = False
            errors.append(f"证书即将过期: 仅剩 {days_left} 天")
        elif days_left <= config.cert.warn_days:
            warnings.append(f"证书快过期: {days_left} 天")

        # 域名匹配检查
        parsed = urlparse(url)
        expected_host = parsed.hostname
        san_matches = any(expected_host in s for s in san_list) if san_list else False
        if not san_matches and expected_host:
            warnings.append(f"域名 {expected_host} 不在证书 SAN 中: {san_list}")

        msg_parts = []
        if errors:
            msg_parts.append(f"❌ {'; '.join(errors)}")
        elif warnings:
            msg_parts.append(f"⚠️ {'; '.join(warnings)}")
        else:
            msg_parts.append(f"✅ 证书有效 ({days_left} 天)")
        msg_parts.append(f"{proto}/{cipher}")

        return CheckResult(
            site_name="", check_type="cert", ok=ok,
            message=" | ".join(msg_parts),
            latency_ms=round(latency, 2),
            details={
                "subject": subject,
                "issuer": issuer,
                "not_before": not_before.isoformat(),
                "not_after": not_after.isoformat(),
                "days_left": days_left,
                "protocol": proto,
                "cipher": cipher,
                "san": san_list,
                "is_expired": days_left < 0,
                "is_critical": days_left <= config.cert.critical_days,
                "is_warning": days_left <= config.cert.warn_days,
            }
        )
    except ssl.SSLError as e:
        return CheckResult(site_name="", check_type="cert", ok=False,
            message=f"SSL 错误: {e}", details={"host": host})
    except Exception as e:
        return CheckResult(site_name="", check_type="cert", ok=False,
            message=f"证书检测失败: {type(e).__name__}: {e}", details={"host": host})


# ════════════════════════════════════════════════════════════
# CDN 检测
# ════════════════════════════════════════════════════════════

def check_cdn(url: str) -> CheckResult:
    """通过 HTTP Header 识别 CDN 类型和状态"""
    host, _, _ = parse_host_port(url)
    try:
        with httpx.Client(timeout=httpx.Timeout(config.l7.request_timeout), follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "SiteMonitor-CDN/1.0"})

        headers = {k.lower(): v for k, v in resp.headers.items()}

        # CDN 指纹库
        cdn_patterns = [
            (["x-cache", "x-cdn"], "AWS CloudFront", "amazon"),
            (["x-amz-cf", "x-amz-id"], "AWS CloudFront", "amazon"),
            (["cf-ray"], "Cloudflare", "cloudflare"),
            (["x-cdn", "x-cdn-cache"], "Akamai", "akamai"),
            (["x-cdn-original-request-id"], "Fastly", "fastly"),
            (["server", "x-served-by"], "Cloudflare", "cloudflare"),  # Cloudflare 有时只返回 server
            (["ali-swift", "x-swift"], "阿里云 CDN", "aliyun"),
            (["x-oss"], "阿里云 OSS", "aliyun"),
            (["tencent", "tencent-cdn"], "腾讯云 CDN", "tencent"),
            (["baidu-cache"], "百度云加速", "baidu"),
            (["x-cache"], "Varnish/Squid", "varnish"),
            (["via", "x-varnish"], "Varnish", "varnish"),
            (["server", "powered-by"], "Nginx", "nginx"),
        ]

        detected_cdn = None
        for header_keys, cdn_name, _ in cdn_patterns:
            for hk in header_keys:
                if hk in headers:
                    detected_cdn = cdn_name
                    break
            if detected_cdn:
                break

        # 缓存命中检测
        cache_status = None
        for hk in ["x-cache", "x-cache-lookup", "cf-cache-status", "x-amz-cf-pop"]:
            if hk in headers:
                cache_status = headers[hk]
                break

        is_cached = cache_status and ("HIT" in str(cache_status).upper() or "cached" in str(cache_status).lower())

        details = {
            "cdn": detected_cdn or "直连/未知",
            "cache_status": cache_status,
            "is_cached": is_cached,
            "server": headers.get("server", ""),
            "via": headers.get("via", ""),
            "response_headers": dict(list(headers.items())[:20]),  # 限制大小
        }

        msg = f"CDN: {detected_cdn or '直连/未知'}"
        if cache_status:
            msg += f" | 缓存: {cache_status} ({'已缓存' if is_cached else 'MISS/回源'})"

        return CheckResult(
            site_name="", check_type="cdn", ok=True,
            message=msg, latency_ms=0,
            details=details
        )
    except Exception as e:
        return CheckResult(site_name="", check_type="cdn", ok=False,
            message=f"CDN 检测失败: {type(e).__name__}: {e}", details={"host": host})


# ════════════════════════════════════════════════════════════
# Docker Socket 检测
# ════════════════════════════════════════════════════════════

def check_docker_socket() -> list[CheckResult]:
    """通过 Docker Socket 查询容器状态"""
    if not config.docker.enabled:
        return []

    import urllib.request

    sock_path = config.docker.socket_path
    if not config.docker.watch_containers:
        # 查询所有容器
        api_path = "/containers/json?all=true"
    else:
        names = ",".join(config.docker.watch_containers)
        api_path = f"/containers/json?all=true&filters={{\"name\":[{','.join(repr(n) for n in config.docker.watch_containers)}]}}"

    results = []
    try:
        # Docker Socket API v1.41
        req = urllib.request.Request(
            f"http://unix://{sock_path}:{api_path}",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            containers = json.loads(resp.read().decode())

        if not containers:
            results.append(CheckResult(
                site_name="docker", check_type="docker", ok=True,
                message="没有找到正在监控的容器", details={}
            ))
            return results

        all_healthy = True
        for c in containers:
            name = c["Names"][0].lstrip("/")
            state = c["State"]
            status = c["Status"]
            image = c["Image"]
            health = c.get("Status", "").lower()

            is_running = state == "running"
            is_healthy = "healthy" in health or "running" in health
            if not is_running or not is_healthy:
                all_healthy = False

            ok = is_running  # 只要 running 就 OK, healthy 是额外加分项
            msg = f"[{'✅' if ok else '❌'}] {name}: {status}"
            if "health" in c:
                msg += f" (健康检查: {c['Health']['Status']})"

            results.append(CheckResult(
                site_name="docker", check_type="docker", ok=ok,
                message=msg,
                details={
                    "id": c["Id"][:12],
                    "name": name,
                    "image": image,
                    "state": state,
                    "status": status,
                    "created": c["Created"],
                }
            ))

        return results

    except FileNotFoundError:
        results.append(CheckResult(
            site_name="docker", check_type="docker", ok=False,
            message=f"Docker Socket 不存在: {sock_path}", details={}
        ))
    except urllib.error.URLError as e:
        results.append(CheckResult(
            site_name="docker", check_type="docker", ok=False,
            message=f"Docker Socket 连接失败: {e}", details={"socket": sock_path}
        ))
    except Exception as e:
        results.append(CheckResult(
            site_name="docker", check_type="docker", ok=False,
            message=f"Docker 检测异常: {type(e).__name__}: {e}", details={}
        ))

    return results


# ════════════════════════════════════════════════════════════
# 主检查调度器
# ════════════════════════════════════════════════════════════

def run_check(site_cfg: dict) -> SiteStatus:
    """执行单个站点的所有检测"""
    name = site_cfg["name"]
    url = site_cfg.get("url", "")
    status = SiteStatus(name=name, url=url, checks={})

    for check_type in site_cfg.get("checks", []):
        if check_type == "l4":
            host, port = resolve_host(site_cfg.get("host") or url)
            port = site_cfg.get("l4_port", port)
            result = check_l4(host, port)
        elif check_type == "l7":
            if not url:
                logger.warning(f"[{name}] L7 检测需要 URL")
                continue
            result = check_l7(url, site_cfg)
        elif check_type == "cert":
            if not url:
                logger.warning(f"[{name}] 证书检测需要 HTTPS URL")
                continue
            result = check_cert(url)
        elif check_type == "cdn":
            if not url:
                logger.warning(f"[{name}] CDN 检测需要 URL")
                continue
            result = check_cdn(url)
        else:
            logger.warning(f"[{name}] 未知检测类型: {check_type}")
            continue

        result.site_name = name
        status.checks[check_type] = result

    return status


def check_all() -> list[SiteStatus]:
    """检查所有启用的站点 + Docker"""
    results: list[SiteStatus] = []

    for site in SITES:
        if not site.get("enabled", True):
            continue
        try:
            status = run_check(site)
            results.append(status)
        except Exception as e:
            logger.error(f"[{site['name']}] 检查异常: {e}")

    # Docker Socket 检测
    if config.docker.enabled:
        docker_results = check_docker_socket()
        # 合并到特殊站点
        if docker_results:
            ds = SiteStatus(name="docker-socket", url="unix:///var/run/docker.sock", checks={})
            for r in docker_results:
                ds.checks[r.check_type + "_" + r.details.get("id", str(len(ds.checks)))] = r
            results.append(ds)

    return results
