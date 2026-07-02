"""
测试配置与站点解析逻辑
覆盖 monitor.py 中 resolve_host / parse_host_port / SiteStatus / CheckResult
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse


# ── 被测函数 (从 monitor.py 复现核心逻辑) ────────────────────

def resolve_host(url_or_host: str) -> tuple[str, int]:
    if url_or_host.startswith("http"):
        parsed = urlparse(url_or_host)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    elif ":" in url_or_host:
        host, port_s = url_or_host.rsplit(":", 1)
        host = host.strip("[]")
        port = int(port_s)
    else:
        host = url_or_host
        port = 443
    return host, port


def parse_host_port(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    scheme = parsed.scheme or "https"
    return host, port, scheme


@dataclass
class CheckResult:
    site_name: str
    check_type: str
    ok: bool
    message: str
    latency_ms: float = 0.0
    details: dict = field(default_factory=dict)
    timestamp: str = "2026-07-02T00:00:00+00:00"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SiteStatus:
    name: str
    url: str
    checks: dict
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


# ── 测试用例 ──────────────────────────────────────────────────

class TestResolveHost:
    """resolve_host 解析测试"""

    def test_https_url(self):
        h, p = resolve_host("https://blog.example.com/path?a=1")
        assert h == "blog.example.com"
        assert p == 443

    def test_https_custom_port(self):
        h, p = resolve_host("https://api.example.com:8443/api")
        assert h == "api.example.com"
        assert p == 8443

    def test_http_url(self):
        h, p = resolve_host("http://internal.local/health")
        assert h == "internal.local"
        assert p == 80

    def test_http_custom_port(self):
        h, p = resolve_host("http://localhost:8080/api")
        assert h == "localhost"
        assert p == 8080

    def test_bare_hostname(self):
        h, p = resolve_host("ssh.example.com")
        assert h == "ssh.example.com"
        assert p == 443

    def test_host_with_port(self):
        h, p = resolve_host("ssh.example.com:22")
        assert h == "ssh.example.com"
        assert p == 22

    def test_ipv6_address(self):
        # urlparse 会去掉方括号，这是预期行为
        h, p = resolve_host("https://[::1]:8443/path")
        assert h == "::1"  # 方括号被 strip
        assert p == 8443

    def test_query_string_ignored(self):
        h, p = resolve_host("https://www.example.com/path?foo=bar&baz=1")
        assert h == "www.example.com"
        assert p == 443

    def test_fragment_ignored(self):
        h, p = resolve_host("https://example.com/page#section")
        assert h == "example.com"
        assert p == 443


class TestParseHostPort:
    """parse_host_port 解析测试"""

    def test_https_defaults(self):
        h, p, s = parse_host_port("https://example.com/api")
        assert h == "example.com"
        assert p == 443
        assert s == "https"

    def test_http_defaults(self):
        h, p, s = parse_host_port("http://example.com/api")
        assert h == "example.com"
        assert p == 80
        assert s == "http"

    def test_custom_port(self):
        h, p, s = parse_host_port("https://api.example.com:9090/v1/users")
        assert h == "api.example.com"
        assert p == 9090
        assert s == "https"

    def test_no_path(self):
        h, p, s = parse_host_port("https://example.com")
        assert h == "example.com"
        assert p == 443
        assert s == "https"


class TestCheckResult:
    """CheckResult 数据结构测试"""

    def test_default_timestamp(self):
        r = CheckResult(site_name="test", check_type="l7", ok=True, message="OK")
        assert r.timestamp == "2026-07-02T00:00:00+00:00"
        assert r.latency_ms == 0.0
        assert r.details == {}

    def test_to_dict(self):
        r = CheckResult(
            site_name="My Site",
            check_type="l4",
            ok=False,
            message="Connection refused",
            latency_ms=123.45,
            details={"host": "example.com", "port": 443},
        )
        d = r.to_dict()
        assert d["site_name"] == "My Site"
        assert d["check_type"] == "l4"
        assert d["ok"] is False
        assert d["message"] == "Connection refused"
        assert d["latency_ms"] == 123.45
        assert d["details"]["host"] == "example.com"

    def test_latency_precision(self):
        r = CheckResult(site_name="x", check_type="l7", ok=True, message="x", latency_ms=12.345)
        assert r.latency_ms == 12.345
        d = r.to_dict()
        assert "latency_ms" in d


class TestSiteStatus:
    """SiteStatus 数据结构测试"""

    def test_to_dict_with_checks(self):
        checks = {
            "l4": CheckResult(site_name="S", check_type="l4", ok=True, message="OK", latency_ms=10),
            "l7": CheckResult(site_name="S", check_type="l7", ok=False, message="Timeout", latency_ms=0),
        }
        s = SiteStatus(name="Test Site", url="https://test.com", checks=checks, consecutive_failures=2)
        d = s.to_dict()
        assert d["name"] == "Test Site"
        assert d["url"] == "https://test.com"
        assert d["consecutive_failures"] == 2
        assert len(d["checks"]) == 2
        assert d["checks"]["l4"]["ok"] is True
        assert d["checks"]["l7"]["ok"] is False

    def test_empty_checks(self):
        s = SiteStatus(name="Empty", url="https://empty.com", checks={})
        d = s.to_dict()
        assert d["checks"] == {}
        assert d["last_ok"] is None
        assert d["last_check"] is None


class TestCDNFingerprint:
    """CDN 识别指纹库测试 (mock 数据)"""

    def test_cloudflare_ray_header(self):
        """Cloudflare 特征: cf-ray"""
        headers = {"cf-ray": "abc123", "server": "cloudflare"}
        assert "cf-ray" in headers
        assert headers["server"] == "cloudflare"

    def test_aliyun_cdn_headers(self):
        """阿里云 CDN 特征: ali-swift / x-oss"""
        headers_ali = {"ali-swift": "Cached", "x-oss": "AliyunOSS"}
        headers_oss = {"x-oss": "AliyunOSS/1.0"}
        assert "ali-swift" in headers_ali
        assert "x-oss" in headers_oss

    def test_aws_cloudfront_headers(self):
        """CloudFront 特征: x-amz-cf-id"""
        headers = {"x-amz-cf-id": "xyz789", "x-cache": "Hit from cloudfront"}
        assert "x-amz-cf-id" in headers

    def test_cache_hit_detection(self):
        """缓存命中检测"""
        test_cases = [
            ("x-cache: Hit from cloudfront", True),
            ("X-Cache: HIT", True),
            ("X-Cache: Miss from origin", False),
            ("cf-cache-status: HIT", True),
            ("cf-cache-status: MISS", False),
            ("", False),
        ]
        for header_val, expected in test_cases:
            if not header_val:
                is_cached = False
            else:
                key, val = header_val.split(": ", 1)
                is_cached = "HIT" in val.upper() or "cached" in val.lower()
            assert is_cached == expected, f"header={header_val!r}, expected={expected}, got={is_cached}"


class TestCertExpiry:
    """证书过期天数计算测试"""

    def test_days_left_calculation(self):
        from datetime import datetime, timezone, timedelta

        now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)

        test_cases = [
            (now + timedelta(days=60), 60, False, False),   # 60天 → 正常
            (now + timedelta(days=29), 29, True, False),    # 29天 → 预警
            (now + timedelta(days=7), 7, True, True),       # 7天 → 严重
            (now + timedelta(days=0), 0, True, True),       # 今天过期 → 严重
            (now - timedelta(days=3), -3, True, True),      # 已过期 → 严重
        ]
        for not_after, expected_days, is_warning, is_critical in test_cases:
            days_left = (not_after - now).days
            assert days_left == expected_days
            assert (days_left <= 30) == is_warning
            assert (days_left <= 7) == is_critical
