"""
loop.py - 兼容旧版本入口（实际功能已在 app.py）
新版本使用 app.py + gunicorn 架构（参考 prometheus exporter 模式）
"""

# 保持向后兼容: 如果有人直接 python3 loop.py, 也启动 web 服务
from app import app, logger

if __name__ == "__main__":
    logger.info("loop.py 已废弃，请使用 app.py + gunicorn")
    logger.info("当前为兼容模式，启动 Flask 开发服务器")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)