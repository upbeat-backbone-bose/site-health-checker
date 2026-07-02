"""
主循环 - 定时调度 + 状态持久化 + 优雅退出
"""

import os
import sys
import time
import signal
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

# 全局退出标志
_shutdown = threading.Event()


def _handle_signal(signum, frame):
    sig_name = {2: "SIGINT", 15: "SIGTERM"}.get(signum, str(signum))
    logger.info(f"收到 {sig_name}, 优雅退出中...")
    _shutdown.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """结构化日志配置"""
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
    """从文件加载上次状态 (用于恢复)"""
    try:
        if os.path.exists(state_file):
            with open(state_file) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"加载状态文件失败: {e}")
    return {}


def save_state(state_file: str, results: list):
    """持久化检测结果"""
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
            print(f"   {res_icon} {check_type.upper()}: {result.message}", end="")
            if result.latency_ms > 0:
                print(f" ({result.latency_ms:.0f}ms)", end="")
            print()

    failed = [s for s in results if s.checks and not all(r.ok for r in s.checks.values())]
    print("\n" + "-" * 60)
    print(f"总计: {len(results)} 个目标, {len(failed)} 个异常")
    if failed:
        print(f"❌ 异常: {[s.name for s in failed]}")
    print()


# ════════════════════════════════════════════════════════════
# 健康检查接口 (可选 HTTP Server)
# ════════════════════════════════════════════════════════════

def start_health_server(state_ref: dict, port: int = 8080):
    """启动轻量健康检查 HTTP Server"""
    import http.server
    import json as _json

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                body = _json.dumps({
                    "status": "ok",
                    "last_check": state_ref.get("last_check", ""),
                    "results": state_ref.get("results", []),
                })
                self.wfile.write(body.encode())
            elif self.path == "/metrics":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                # Prometheus 格式指标
                lines = []
                for r in state_ref.get("results", []):
                    site_name = r.get("name", "unknown").replace(" ", "_")
                    for ct, cr in r.get("checks", {}).items():
                        ok_val = 1 if cr.get("ok") else 0
                        latency = cr.get("latency_ms", 0)
                        lines.append(f'site_monitor_check{{site="{site_name}",type="{ct}"}} {ok_val}')
                        lines.append(f'site_monitor_latency_ms{{site="{site_name}",type="{ct}"}} {latency}')
                self.wfile.write("\n".join(lines).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # 静默日志

    srv = http.server.HTTPServer(("", port), HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    logger.info(f"健康检查接口启动: http://0.0.0.0:{port}/health")
    logger.info(f"Prometheus 指标: http://0.0.0.0:{port}/metrics")
    return srv


# ════════════════════════════════════════════════════════════
# 主循环
# ════════════════════════════════════════════════════════════

def main():
    global logger

    from config import config, SITES

    logger = setup_logging(config.log_level)
    logger.info("=" * 50)
    logger.info("Site Monitor 启动")
    logger.info(f"监控目标: {len([s for s in SITES if s.get('enabled', True)])} 个")
    logger.info(f"检测间隔: {config.check_interval}s")
    logger.info(f"告警: {'启用' if config.alert.enabled else '禁用'}")
    logger.info("=" * 50)

    # 健康检查接口
    state_ref: dict = {}
    health_srv = start_health_server(state_ref, port=8080)

    # 加载上次状态 (恢复用)
    prev_state = load_state(config.state_file)

    logger.info("开始检测...")

    while not _shutdown.is_set():
        try:
            from monitor import check_all

            results = check_all()
            print_summary(results)

            # 更新共享状态 (健康检查接口用)
            state_ref["results"] = [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
            state_ref["last_check"] = datetime.now(timezone.utc).isoformat()

            # 持久化
            save_state(config.state_file, results)

            # 告警
            from alert import send_alert
            send_alert(results)

        except Exception as e:
            logger.error(f"检测循环异常: {type(e).__name__}: {e}", exc_info=True)

        # 等待下次检测或退出信号
        _shutdown.wait(timeout=config.check_interval)

    logger.info("Site Monitor 已停止")
