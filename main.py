#!/usr/bin/env python3
"""
Site Monitor - 生产级网站监控
入口脚本: gunicorn app:app
直接运行: python3 main.py (开发模式)
"""

import sys
from app import app

if __name__ == "__main__":
    # 直接运行进入开发模式 (Flask 自带服务器)
    # 生产环境用 gunicorn: gunicorn app:app
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)