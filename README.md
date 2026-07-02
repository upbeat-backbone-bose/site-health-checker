# Site Monitor - 生产级网站监控

[![CI](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/ci.yml)
[![Release](https://github.com/upbeat-backbone-bose/site-health-checker/actions/workflows/release.yml/badge.svg)](https://github.com/upbeat-backbone-bose/site-health-checker/releases)
[![codecov](https://codecov.io/gh/upbeat-backbone-bose/site-health-checker/branch/main/graph/badge.svg)](https://codecov.io/gh/upbeat-backbone-bose/site-health-checker)

**支持 L4/L7 HTTP 检测、证书检测、CDN 识别、Docker 容器状态监控。**

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
| **健康检查** | `/health` + `/metrics` (Prometheus 格式) |

---

## 快速开始

### 1. 一键拉取镜像 (无需 clone)

```bash
# 拉取最新稳定版
docker pull ghcr.io/upbeat-backbone-bose/site-health-checker:latest

# 运行 (需要先配置 config.py)
docker run -d \
  --name site-monitor \
  -v /path/to/your/config.py:/app/config.py:ro \
  -v site-monitor-data:/data \
  -p 8080:8080 \
  ghcr.io/upbeat-backbone-bose/site-health-checker:latest
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

---

## GitHub Actions CI/CD

| Workflow | 触发条件 | 作用 |
|---------|---------|------|
| `ci.yml` | push/PR 到 main | 运行 51 个单元测试 + Docker 构建验证 + 健康检查 |
| `release.yml` | push tag `v*` | 构建多架构镜像 (amd64/arm64) 推送到 GHCR + 创建 GitHub Release |

### 发版流程

```bash
# 1. 确认所有测试通过
pytest tests/ -v

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

## 健康检查接口

| 接口 | 说明 |
|------|------|
| `http://localhost:8080/health` | JSON 格式状态 (适合 K8s probes) |
| `http://localhost:8080/metrics` | Prometheus 格式指标 |

Prometheus 配置示例：

```yaml
scrape_configs:
  - job_name: 'site-monitor'
    static_configs:
      - targets: ['site-monitor:8080']
    metrics_path: '/metrics'
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
├── main.py                 # 入口
├── config.py               # 配置 (监控目标、告警、检测参数)
├── monitor.py              # 核心检测引擎
├── alert.py                # 告警发送
├── loop.py                 # 主循环 + 健康检查
├── requirements.txt        # Python 依赖
├── Dockerfile              # 多架构镜像 (amd64/arm64)
├── docker-compose.yml      # Compose 部署
├── pytest.ini              # 测试配置
├── .gitignore
├── README.md
└── .github/
    └── workflows/
        ├── ci.yml          # CI: 测试 + Docker 构建验证
        └── release.yml     # CD: GHCR 推送 + Release 创建
```

---

## 测试

```bash
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

---

## 后续维护建议

1. **监控自身**: 用 Prometheus 拉取 `/metrics`，告警"容器不健康"
2. **日志收集**: 挂载日志目录到 ELK/Loki，或用 `docker logs`
3. **多实例**: 不同监控目标用不同实例，避免单点
4. **证书预警**: 证书 < 7 天自动告警，< 30 天提前预警
5. **HTTPS 代理**: 如果需要走代理，在 `httpx.Client()` 中加 `proxy` 参数
