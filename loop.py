"""
主循环 - 定时调度 + 状态持久化 + 优雅退出
架构: 健康服务线程 + 业务检测线程 + 主线程只负责等待退出信号
"""

import os
import sys
import time
import signal
import json
import logging
import threading
import socket
from datetime import datetime, timezone
from typing import Optional

# ── 全局退出标志 ──────────────────────────────────────────────
_shutdown = threading.Event()


def _handle_signal(signum, frame):
    sig_name = {2: "SIGINT", 15: "SIGTERM"}.get(signum, str(signum))
    logger.info(f"收到 {sig_name}, 优雅退出中...")
    _shutdown.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("monitor")
    return logger


# ════════════════════════════════════════════════════════════
# 状态持久化
# ════════════════════════════════════════════════════════════

def load_state(state_file: str) -> dict:
    try:
        if os.path.exists(state_file):
            with open(state_file) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"加载状态文件失败: {e}")
    return {}


def save_state(state_file: str, results: list):
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    try:
        with open(state_file, "w") as f:
            json.dump({
                "last_check": datetime.now(timezone.utc).isoformat(),
                "results": [r.to_dict() if hasattr(r, "to_dict") else r for r in results],
            }, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"保存状态文件失败: {e}")


# ════════════════════════════════════════════════════════════
# 健康检查 HTTP 接口 (运行在独立线程)
# ════════════════════════════════════════════════════════════

def _run_health_server(state_ref: dict, port: int = 8080):
    """健康检查服务 (在独立线程运行，不受业务循环阻塞影响)"""
    import http.server
    import json as _json

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    body = _json.dumps({
                        "status": "ok",
                        "uptime_seconds": time.time() - state_ref.get("_start_time", time.time()),
                        "last_check": state_ref.get("last_check", ""),
                        "results": state_ref.get("results", []),
                    })
                    self.wfile.write(body.encode())
                elif self.path == "/metrics":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    lines = []
                    for r in state_ref.get("results", []):
                        site = r.get("name", "unknown").replace(" ", "_")
                        for ct, cr in r.get("checks", {}).items():
                            ok = 1 if cr.get("ok") else 0
                            lat = cr.get("latency_ms", 0)
                            lines.append(f'site_monitor_check{{site="{site}",type="{ct}"}} {ok}')
                            lines.append(f'site_monitor_latency_ms{{site="{site}",type="{ct}"}} {lat}')
                    self.wfile.write("\n".join(lines).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
            except Exception:
                pass  # 忽略请求处理异常

        def log_message(self, request, client_address, format, *args):
            pass  # 静默日志

    srv = http.server.HTTPServer(("", port), HealthHandler)
    logger.info(f"健康检查接口启动: http://0.0.0.0:{port}/health")
    logger.info(f"Prometheus 指标: http://0.0.0.0:{port}/metrics")
    srv.serve_forever()


def _wait_port(port: int, timeout: float = 30.0) -> bool:
    """等待端口变为可连接"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.5)
    return False


# ════════════════════════════════════════════════════════════
# 业务检测循环 (在独立线程运行)
# ════════════════════════════════════════════════════════════

def _run_monitor_loop(state_ref: dict, check_interval: int, state_file: str):
    """业务检测主循环 (在独立线程运行，不阻塞健康服务)"""
    from monitor import check_all
    from alert import send_alert

    while not _shutdown.is_set():
        try:
            results = check_all()

            # 打印摘要
            print_summary(results)

            # 更新共享状态
            state_ref["results"] = [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
            state_ref["last_check"] = datetime.now(timezone.utc).isoformat()

            # 持久化
            save_state(state_file, results)

            # 告警
            send_alert(results)

        except Exception as e:
            logger.error(f"检测循环异常: {type(e).__name__}: {e}", exc_info=True)

        # 在每次循环结束后等待，允许优雅退出检查
        # 使用短超时以便及时响应 shutdown 信号
        _shutdown.wait(timeout=check_interval)


# ════════════════════════════════════════════════════════════
# 状态展示
# ════════════════════════════════════════════════════════════

def print_summary(results: list):
    """在终端打印摘要"""
    print("\n" + "=" * 60)
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  检测完成")
    print("=" * 60)

    for status in results:
        all_ok = all(r.ok for r in status.checks.values()) if status.checks else True
        icon = "✅" if all_ok else "❌"
        print(f"\n{icon} {status.name}")
        print(f"   {status.url}")
        for check_type, result in status.checks.items():
            res_icon = "✅" if result.ok else "❌"
            line = f"   {res_icon} {check_type.upper()}: {result.message}"
            if result.latency_ms > 0:
                line += f" ({result.latency_ms:.0f}ms)"
            print(line)

    failed = [s for s in results if s.get("checks") and not all(r.get("ok", True) for r in s["checks"].values())]
    print("\n" + "-" * 60)
    print(f"总计: {len(results)} 个目标, {len(failed)} 个异常")
    if failed:
        print(f"❌ 异常: {[s['name'] for s in failed]}")
    print()


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def main():
    global logger

    from config import config, SITES

    logger = setup_logging(config.log_level)
    logger.info("=" * 50)
    logger.info("Site Monitor 启动")
    logger.info(f"监控目标: {len([s for s in SITES if s.get('enabled', True)])} 个")
    logger.info(f"检测间隔: {config.check_interval}s")
    logger.info("=" * 50)

    # 共享状态 (健康服务和业务线程都读写)
    state_ref: dict = {
        "_start_time": time.time(),
        "results": [],
        "last_check": "",
    }

    # ── 启动健康检查服务 (非 daemon 线程, 进程退出依赖它) ──
    health_thread = threading.Thread(target=_run_health_server, args=(state_ref, 8080), name="health-server")
    health_thread.start()

    # 等待健康服务就绪 (不影响业务线程)
    if not _wait_port(8080, timeout=10.0):
        logger.warning("健康检查服务启动超时，继续运行")

    # ── 启动业务检测线程 ──────────────────────────────────────
    monitor_thread = threading.Thread(
        target=_run_monitor_loop,
        args=(state_ref, config.check_interval, config.state_file),
        name="monitor-loop",
        daemon=False,  # 非 daemon，主循环结束后进程继续运行（等健康服务）
    )
    monitor_thread.start()

    # ── 主线程: 只负责等待退出信号 ──────────────────────────
    _shutdown.wait()

    logger.info("Site Monitor 已停止")
