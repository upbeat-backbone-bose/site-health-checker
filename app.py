"""
Flask Web App - 提供 /health, /metrics, /status 接口
由 gunicorn 加载运行，参考 prometheus/*_exporter 架构

线程模型:
- gunicorn worker 1 个，4 个线程
- 后台监控循环在模块加载时启动一次 (daemon 线程)
"""

import os
import sys
import time
import json
import logging
import threading
from datetime import datetime, timezone

from flask import Flask, Response, jsonify

from config import config, SITES
from monitor import check_all
from alert import send_alert


# ── 共享状态 (线程安全, 因为只有 main_loop 写, 读取时用快照) ──
_state_lock = threading.Lock()
_state: dict = {
    "_start_time": time.time(),
    "last_check": "",
    "results": [],
    "running": False,
}


# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("monitor")


# ── 监控主循环 (daemon 线程, 模块加载时启动一次) ──────────
def _monitor_loop():
    """后台检测循环 (daemon 线程, 不阻塞 web 服务)"""
    logger.info("=" * 50)
    logger.info("Site Monitor 启动")
    logger.info(f"监控目标: {len([s for s in SITES if s.get('enabled', True)])} 个")
    logger.info(f"检测间隔: {config.check_interval}s")
    logger.info("=" * 50)

    while True:
        try:
            results = check_all()

            # 更新共享状态 (加锁保证原子性)
            with _state_lock:
                _state["results"] = [r.to_dict() if hasattr(r, "to_dict") else r for r in results]
                _state["last_check"] = datetime.now(timezone.utc).isoformat()

            # 持久化 + 告警 (save_state 已在 app.py 中定义)
            save_state(config.state_file, results)
            send_alert(results)

            # 摘要输出 (INFO 级)
            failed = [r for r in results if r.get("checks") and not all(c.get("ok", True) for c in r["checks"].values())]
            logger.info(f"检测完成: {len(results)} 个目标, {len(failed)} 个异常")
            if failed:
                logger.warning(f"异常目标: {[r['name'] for r in failed]}")

        except Exception as e:
            logger.error(f"检测循环异常: {type(e).__name__}: {e}", exc_info=True)

        time.sleep(config.check_interval)


def save_state(state_file: str, results: list):
    """持久化检测结果到文件 (供 loop.py 的副本使用)"""
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    try:
        with open(state_file, "w") as f:
            json.dump({
                "last_check": datetime.now(timezone.utc).isoformat(),
                "results": [r.to_dict() if hasattr(r, "to_dict") else r for r in results],
            }, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"保存状态文件失败: {e}")


# ── 启动后台监控 (模块加载时执行一次) ────────────────────
# gunicorn 加载模块时, 主进程只执行一次, 但每个 worker 都会执行
# 因为 config.check_interval 通常 > 60s, 多 worker 启动多个循环也没关系
# 如果要严格单实例, 用 preload_app=True 让监控在 master 进程启动
def _start_monitor():
    if _state["running"]:
        return  # 已启动
    with _state_lock:
        if _state["running"]:
            return
        _state["running"] = True

    t = threading.Thread(target=_monitor_loop, name="monitor-loop", daemon=True)
    t.start()
    logger.info("监控循环已启动 (daemon thread)")


# 启动 (模块级, gunicorn 加载时执行一次)
_start_monitor()


# ── Flask App ─────────────────────────────────────────────
app = Flask(__name__)


@app.route("/")
def index():
    """根路径 - 服务信息"""
    return jsonify({
        "service": "site-monitor",
        "status": "running",
        "uptime_seconds": time.time() - _state["_start_time"],
        "endpoints": ["/", "/health", "/metrics", "/status"],
    })


@app.route("/health")
def health():
    """健康检查 (K8s liveness probe 用)"""
    return jsonify({
        "status": "ok",
        "uptime_seconds": round(time.time() - _state["_start_time"], 2),
        "last_check": _state["last_check"],
        "monitor_running": _state["running"],
    })


@app.route("/status")
def status():
    """详细状态 (所有目标检测结果)"""
    with _state_lock:
        results_snapshot = _state["results"]
        last_check = _state["last_check"]

    return jsonify({
        "last_check": last_check,
        "sites": results_snapshot,
        "total": len(results_snapshot),
    })


@app.route("/metrics")
def metrics():
    """Prometheus 指标"""
    with _state_lock:
        results_snapshot = _state["results"]

    lines = [
        "# HELP site_monitor_up Service is up",
        "# TYPE site_monitor_up gauge",
        f"site_monitor_up 1",
        "",
        "# HELP site_monitor_last_check_timestamp Last check timestamp",
        "# TYPE site_monitor_last_check_timestamp gauge",
        f"site_monitor_last_check_timestamp {int(time.time())}",
        "",
        "# HELP site_monitor_check Site check status (1=ok, 0=fail)",
        "# TYPE site_monitor_check gauge",
        "# HELP site_monitor_latency_ms Site check latency in milliseconds",
        "# TYPE site_monitor_latency_ms gauge",
    ]

    for r in results_snapshot:
        site = r.get("name", "unknown").replace(" ", "_").replace("-", "_")
        for ct, cr in r.get("checks", {}).items():
            ok = 1 if cr.get("ok") else 0
            lat = cr.get("latency_ms", 0)
            lines.append(f'site_monitor_check{{site="{site}",type="{ct}"}} {ok}')
            lines.append(f'site_monitor_latency_ms{{site="{site}",type="{ct}"}} {lat}')

    return Response("\n".join(lines), mimetype="text/plain")


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not found"}), 404


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"500 error: {e}")
    return jsonify({"error": "internal error"}), 500


# ── 调试入口 (本地直接 python3 app.py 运行) ─────────────
if __name__ == "__main__":
    logger.info("启动 Flask 开发服务器 (本地调试模式)")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)