# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

LABEL maintainer="Site Monitor"
LABEL description="Production-ready website monitoring with L4/L7/Cert/CDN/Docker checks"

# ── 系统依赖 ─────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# ── 工作目录 ─────────────────────────────────────────────────
WORKDIR /app

# ── Python 依赖 (按依赖顺序安装, 减少层重建) ───────────────────
# 先装预编译 wheel, 加快构建速度
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 应用代码 ─────────────────────────────────────────────────
COPY *.py ./

# ── 数据目录 (Docker Volume) ─────────────────────────────────
# /data 存放: 状态文件、告警防抖记录
# /var/run 挂载 Docker Socket
RUN mkdir -p /data && chmod 755 /data

# 非 root 用户 (安全加固)
RUN useradd -m -s /bin/bash monitor && \
    chown -R monitor:monitor /app /data

USER monitor

# ── 健康检查 ─────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

# ── 端口 ─────────────────────────────────────────────────────
EXPOSE 8080

# ── 入口 ─────────────────────────────────────────────────────
CMD ["python3", "-u", "loop.py"]
