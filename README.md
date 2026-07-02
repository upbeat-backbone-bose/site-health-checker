# Site Monitor - 生产级网站监控

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

### 1. 配置监控目标

编辑 `config.py`，在 `SITES` 列表中添加你的网站：

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

### 2. 配置告警 (可选)

在 `config.py` 的 `AlertConfig` 中填入 Webhook 地址：

```python
class AlertConfig(BaseModel):
    dingtalk_url: str = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    feishu_url: str = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
    # 或 Telegram
    telegram_token: str = "123456:ABCdef..."
    telegram_chat_id: str = "987654321"
```

### 3. 一键启动

```bash
# 开发调试 (直接在主机跑)
pip install -r requirements.txt
python3 loop.py

# Docker 部署 (推荐)
docker compose up -d

# 查看日志
docker compose logs -f
```

---

## Docker 部署详细说明

### 宿主机无 Docker Socket 的场景 (纯 HTTP 监控)

```yaml
services:
  site-monitor:
    volumes:
      - ./config.py:/app/config.py:ro
      - ./data:/data
    # 不挂载 docker.sock, 只做 HTTP 监控
```

### 宿主机有 Docker Socket 的场景 (监控容器)

```yaml
services:
  site-monitor:
    volumes:
      - ./config.py:/app/config.py:ro
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro  # 只读
```

> ⚠️ **安全注意**: Docker Socket 挂载后, 容器内代码有宿主机 root 权限。生产环境务必加 `:ro` 只读，并确认 `config.py` 中 `watch_containers` 只监控必要容器。

### NAS / 群晖 部署

```bash
# 1. SSH 登录 NAS
# 2. 创建监控目录
mkdir -p /volume1/docker/site-monitor
cd /volume1/docker/site-monitor

# 3. 上传代码 (config.py 先配置好)
# 4. 启动
docker compose up -d
```

### VPS / 云服务器

```bash
git clone <your-repo>
cd site-monitor
# 修改 config.py 配置
docker compose up -d --build
```

---

## 健康检查接口

容器运行后，可通过以下接口监控自身：

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

## 告警机制说明

- **防抖**: 同一目标 5 分钟内只告警一次 (可配置 `aggregation_minutes`)
- **阈值**: 连续失败 1 次即告警 (可配置 `failure_threshold`)
- **恢复通知**: 故障恢复后发送恢复消息 (需要自行扩展)

---

## 文件结构

```
site-monitor/
├── main.py          # 入口
├── config.py         # 配置 (监控目标、告警、检测参数)
├── monitor.py        # 核心检测引擎
├── alert.py          # 告警发送
├── loop.py           # 主循环 + 健康检查
├── requirements.txt  # Python 依赖
├── Dockerfile        # 镜像构建
├── docker-compose.yml
└── README.md
```

---

## 测试

```bash
# 运行全部测试 (51 个用例)
pip install pytest
pytest tests/ -v

# 覆盖率报告
pytest tests/ -v --cov=. --cov-report=term-missing
```

**测试覆盖模块:**

| 模块 | 覆盖内容 | 用例数 |
|------|---------|--------|
| `test_monitor.py` | URL 解析、数据结构、CDN 指纹、证书过期计算 | 24 |
| `test_alert.py` | 防抖逻辑、消息构建、告警聚合 | 14 |
| `test_loop.py` | 状态持久化、摘要输出、配置校验 | 13 |

> ⚠️ 网络相关测试 (L4/L7/Cert/CDN/Docker Socket 真实调用) 需要完整依赖 + 网络环境，在 Docker 容器内运行更准确。

---

## 常见问题

**Q: 容器无法访问宿主机 Docker Socket?**
A: 确认 `config.docker.socket_path` 指向挂载路径，默认 `/var/run/docker.sock`

**Q: 证书检测报 SSL 错误?**
A: 检查目标是否强制 TLS 1.3，`ssl.create_default_context()` 已设置最低 TLSv1.2

**Q: 告警没有发出?**
A: 检查 Webhook URL 是否正确，确认机器人没有被限流 (钉钉每个机器人每分钟最多 20 条)

**Q: 想加新检测类型?**
A: 在 `monitor.py` 中添加函数，在 `run_check()` 中注册即可

---

## 后续维护建议

1. **监控自身**: 用 Prometheus 拉取 `/metrics`，告警"容器不健康"
2. **日志收集**: 挂载日志目录到 ELK/Loki，或用 `docker logs`
3. **多实例**: 不同监控目标用不同实例，避免单点
4. **证书预警**: 证书 < 7 天自动告警，< 30 天提前预警
5. **HTTPS 代理**: 如果需要走代理，在 `httpx.Client()` 中加 `proxy` 参数
