# Site Monitor - 生产级网站监控

[![CI](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/ci.yml)
[![Release](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/release.yml/badge.svg)](https://github.com/upbeat-backbone-bose/site-health-checker/releases)
[![Sync README](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/sync-readme.yml/badge.svg)](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/sync-readme.yml)
[![codecov](https://codecov.io/gh/upbeat-backbone-bose/site-health-checker/branch/main/graph/badge.svg)](https://codecov.io/gh/upbeat-backbone-bose/site-health-checker)
[![Version](https://img.shields.io/badge/version-v1.2.1-blue)](https://github.com/upbeat-backbone-bose/site-health-checker/releases/tag/v1.2.1)
[![Docker Pulls](https://img.shields.io/badge/docker-pulls-ghcr.io%2Fupbeat--backbone--bose%2Fsite--health--checker-blue)](https://github.com/upbeat-backbone-bose/site-health-checker/pkgs/container/site-health-checker)

**支持 L4/L7 HTTP 检测、证书检测、CDN 识别、Docker 容器状态监控。基于 Flask + Gunicorn (参考 prometheus/*_exporter 架构) 构建。**

---

## 架构

```
┌─────────────────────────────────────┐
│  Gunicorn (1 worker, 4 threads)     │
│  ┌────────────────────────────────┐ │
│  │  Flask app.py                  │ │
│  │  /  /health  /metrics  /status │ │
│  └────────────────────────────────┘ │
│           ▲                         │
│           │ 模块加载时启动一次       │
│           │ (daemon thread)         │
│  ┌────────────────────────────────┐ │
│  │  Monitor Loop                  │ │
│  │  L4 / L7 / Cert / CDN / Docker │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
```

**为什么不用手搓的多线程 HTTP 服务器？**
本项目第一版用 `threading.HTTPServer` + daemon thread 自跑，踩了 3 小时坑：
- CI 里 `docker logs` 看不到任何输出（ExitCode: 0 但服务没起来）
- daemon 线程在主线程异常时会一起死掉，容器端口却还占着
- 多线程 + GIL 让健康检查和服务循环互相阻塞

**业界标准方案（Flask + Gunicorn）** 直接解决：gunicorn master 进程管 worker，监控循环在 worker 模块加载时启动，HTTP 请求由 gunicorn 线程池处理。

---

## 功能特性

| 检测类型 | 说明 |
|---------|------|
| **L4 TCP** | 端口连通性 + 连接延迟检测 |
| **L7 HTTP** | 状态码、响应时间、关键词匹配 |
| **证书检测** | 过期时间、协议版本、SAN 域名匹配、预警 |
| **CDN 识别** | 自动识别 Cloudflare/阿里云/CDN/直连 |
| **Docker Socket** | 宿主机容器健康状态监控 |
| **告警聚合** | 钉钉/飞书/Telegram，5 分钟防抖 |
| **持久化** | 重启后状态恢复，支持 Prometheus 拉取 |
| **健康检查** | `/` `/health` `/metrics` `/status` 四个 endpoint |

---

## 快速开始

### 1. 一键拉取镜像 (无需 clone)

```bash
# 拉取最新稳定版
docker pull ghcr.io/upbeat-backbone-bose/site-health-checker:latest

# 运行 (需要先准备 config.py)
docker run -d \
  --name site-monitor \
  -v /path/to/your/config.py:/app/config.py:ro \
  -v site-monitor-data:/data \
  -p 8080:8080 \
  ghcr.io/upbeat-backbone-bose/site-health-checker:latest

# 验证
curl http://localhost:8080/health
```

> **版本标签**: `latest` (最新稳定版) / `v1.0.0` / `v1` / `v1.0` (语义化版本)

### 2. 配置监控目标

编辑本地 `config.py`：

```python
SITES = [
    {
        "name": "我的网站",
        "url": "https://www.example.com",
        "checks": ["l7", "cert", "cdn"],
    },
    {
        "name": "SSH 服务器",
        "host": "ssh.example.com",
        "checks": ["l4"],
        "l4_port": 22,
    },
]
```

### 3. Docker Compose 部署

```yaml
services:
  site-monitor:
    image: ghcr.io/upbeat-backbone-bose/site-health-checker:latest
    container_name: site-monitor
    restart: unless-stopped
    volumes:
      - ./config.py:/app/config.py:ro
      - ./data:/data
    ports:
      - "8080:8080"
```

```bash
docker compose up -d
docker compose logs -f
```

### 4. 本地开发模式

```bash
# 装依赖
pip install -r requirements.txt

# 直接跑 (Flask 自带服务器, 单进程)
python3 app.py

# 或用 gunicorn 模拟生产
gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 app:app
```

---

## HTTP 接口

| 路径 | 说明 |
|------|------|
| `GET /` | 服务信息 (uptime, endpoint 列表) |
| `GET /health` | 健康检查 (K8s liveness probe 用) |
| `GET /status` | 详细状态 (所有目标检测结果) |
| `GET /metrics` | Prometheus 格式指标 |

**`/health` 响应示例：**
```json
{
  "status": "ok",
  "uptime_seconds": 3600.5,
  "last_check": "2026-07-02T16:00:00+00:00",
  "monitor_running": true
}
```

**`/metrics` 响应示例：**
```
# HELP site_monitor_up Service is up
# TYPE site_monitor_up gauge
site_monitor_up 1

# HELP site_monitor_check Site check status (1=ok, 0=fail)
# TYPE site_monitor_check gauge
site_monitor_check{site="blog",type="l7"} 1
site_monitor_check{site="blog",type="cert"} 1
site_monitor_check{site="api",type="l7"} 0
```

**Prometheus 抓取配置：**
```yaml
scrape_configs:
  - job_name: 'site-monitor'
    static_configs:
      - targets: ['site-monitor:8080']
    metrics_path: '/metrics'
```

---

## GitHub Actions CI/CD

| Workflow | 触发条件 | 作用 |
|---------|---------|------|
| `ci.yml` | push/PR 到 main, 手动 dispatch | 运行 51 个单元测试 + Docker 构建验证 + 健康检查 |
| `release.yml` | push tag `v*` | 构建多架构镜像 (amd64/arm64) 推送到 GHCR + 创建 GitHub Release |

### CI Job 详情

每次 push main 会自动运行：
1. **Test Python 3.12** — `pytest tests/ -v --cov=.`
2. **Docker Build** — 构建镜像 → 启动容器 → `curl /health` 验证

### 发版流程

```bash
# 1. 确认所有测试通过 (CI 已自动跑)
# 2. 打标签 (自动触发 release.yml)
git tag v1.0.0
git push origin v1.0.0

# 3. GitHub Actions 自动完成:
#    - 构建 amd64 + arm64 镜像
#    - 推送 ghcr.io/upbeat-backbone-bose/site-health-checker:v1.0.0
#    - 推送 ghcr.io/upbeat-backbone-bose/site-health-checker:latest
#    - 创建 GitHub Release
```

---

## 告警配置

在 `config.py` 的 `AlertConfig` 中填入 Webhook 地址：

```python
class AlertConfig(BaseModel):
    dingtalk_url: str = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    feishu_url: str = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
    # 或 Telegram
    telegram_token: str = "123456:ABCdef..."
    telegram_chat_id: str = "987654321"
```

**告警机制**: 同一目标 5 分钟内只告警一次，连续失败 1 次即触发。

---

## 文件结构

```
site-monitor/
├── app.py                 # ⭐ Flask app + 监控循环 (gunicorn 入口)
├── main.py                # 兼容入口 (直接 python3 main.py 进开发模式)
├── loop.py                # 兼容 shim (旧版入口, 转发到 app.py)
├── config.py              # 配置 (监控目标、告警、检测参数)
├── monitor.py             # 核心检测引擎 (L4/L7/Cert/CDN/Docker)
├── alert.py               # 告警发送 (钉钉/飞书/Telegram)
├── tests/                 # 51 个单元测试
│   ├── test_monitor.py
│   ├── test_alert.py
│   └── test_loop.py
├── requirements.txt       # httpx, cryptography, pydantic, flask, gunicorn
├── Dockerfile             # 多架构镜像 (amd64/arm64)
├── docker-compose.yml
├── pytest.ini
├── .gitignore
├── README.md
└── .github/
    └── workflows/
        ├── ci.yml         # CI: 测试 + Docker 构建 + 健康检查
        └── release.yml    # CD: GHCR 推送 + Release 创建
```

---

## 测试

```bash
pip install -r requirements.txt
pip install pytest pytest-cov
pytest tests/ -v --cov=. --cov-report=term-missing
```

| 模块 | 覆盖内容 | 用例数 |
|------|---------|--------|
| `test_monitor.py` | URL 解析、数据结构、CDN 指纹、证书过期计算 | 24 |
| `test_alert.py` | 防抖逻辑、消息构建、告警聚合 | 14 |
| `test_loop.py` | 状态持久化、摘要输出、配置校验 | 13 |

> ⚠️ 网络相关测试 (L4/L7 真实 HTTP 请求) 需要完整依赖 + 网络环境，在 CI 容器内运行更准确。

---

## 常见问题

**Q: 容器无法访问宿主机 Docker Socket?**
A: 确认 `config.docker.socket_path` 指向挂载路径，默认 `/var/run/docker.sock`

**Q: 证书检测报 SSL 错误?**
A: 检查目标是否强制 TLS 1.3，`ssl.create_default_context()` 已设置最低 TLSv1.2

**Q: 告警没有发出?**
A: 检查 Webhook URL 是否正确，确认机器人没有被限流 (钉钉每个机器人每分钟最多 20 条)

**Q: GHCR 镜像拉取失败?**
A: GHCR 镜像默认公开，如需私有需在 GitHub Packages 设置 `visibility: public`

**Q: CI 报 `docker logs` 看不到任何输出但 ExitCode: 0？**
A: 用 `timeout 10 docker run -it` foreground 模式跑，看真实 stdout。
本项目早期版本踩过这个坑：`docker run -d` 启动容器后立即正常退出 (`ExitCode: 0`)，但日志为空，最终定位是 Python 模块加载时的 `signal.signal` 在未初始化状态下被引用。**改为 Flask + Gunicorn 后这个问题消失**，因为 gunicorn 有完整的进程生命周期管理。

**Q: 为什么不用一个 Python 文件启动一切？**
A: 手搓 `threading.HTTPServer` + 自定义主循环会遇到 daemon 线程管理、GIL 阻塞、日志缓冲等一系列问题。Flask + Gunicorn 是 Python Web 服务的业界标准，简单可靠，参考 prometheus/blackbox_exporter 等成熟项目。

---

## 后续维护建议

1. **监控自身**: 用 Prometheus 拉取 `/metrics`，告警"容器不健康"
2. **日志收集**: gunicorn 默认输出到 stdout，`docker logs` 即可；或挂载到 ELK/Loki
3. **多实例**: 不同监控目标用不同实例，避免单点
4. **证书预警**: 证书 < 7 天自动告警，< 30 天提前预警
5. **HTTPS 代理**: 如果需要走代理，在 `httpx.Client()` 中加 `proxy` 参数
6. **Workers 调优**: 单 worker 足够（监控循环在 daemon 线程），多 worker 会启动多个监控循环

---

## 参考资料

- [prometheus/client_python](https://github.com/prometheus/client_python) — Python Prometheus 客户端 (CI 范本)
- [prometheus/blackbox_exporter](https://github.com/prometheus/blackbox_exporter) — 黑盒监控 (Flask + Gunicorn 架构)
- [Gunicorn 部署文档](https://docs.gunicorn.org/en/stable/deploy.html)
- [GitHub Actions: workflow 权限](https://docs.github.com/en/actions/security-guides/automatic-token-authentication)