"""
配置中心 - 所有可变参数集中管理
生产环境: 修改此文件即可，不要碰业务逻辑
使用 dataclass + 简单校验, 无 pydantic 依赖 (便于测试环境运行)
"""

from typing import Optional, List
from dataclasses import dataclass, field


# ── 告警配置 ──────────────────────────────────────────────
@dataclass
class AlertConfig:
    """告警 Webhook 配置"""
    enabled: bool = True
    dingtalk_url: Optional[str] = None       # 钉钉机器人 Webhook
    feishu_url: Optional[str] = None          # 飞书机器人 Webhook
    telegram_token: Optional[str] = None      # Telegram Bot Token
    telegram_chat_id: Optional[str] = None
    # 告警聚合: 同一目标 N 分钟内只告警一次
    aggregation_minutes: int = 5
    # 连续失败 N 次才告警 (防抖)
    failure_threshold: int = 1


# ── L4 TCP 检测配置 ───────────────────────────────────────
@dataclass
class L4Config:
    """TCP 连通性检测"""
    connect_timeout: float = 5.0             # TCP 连接超时 (秒)
    read_timeout: float = 3.0                # 读取超时 (秒)


# ── L7 HTTP 检测配置 ─────────────────────────────────────
@dataclass
class L7Config:
    """HTTP 七层检测"""
    request_timeout: float = 10.0            # 请求总超时
    follow_redirects: bool = True
    expected_status_codes: List[int] = field(default_factory=lambda: [200, 201, 204])
    # 关键词检测: 响应 Body 包含这些关键词才认为正常
    expected_keywords: List[str] = field(default_factory=list)
    # 响应时间阈值 (毫秒), 超过视为慢
    slow_threshold_ms: int = 2000


# ── 证书检测配置 ──────────────────────────────────────────
@dataclass
class CertConfig:
    """TLS 证书检测"""
    check_validity: bool = True              # 检测过期
    warn_days: int = 30                      # 提前 N 天预警
    critical_days: int = 7                   # 严重: 提前 N 天告警
    check_protocol: bool = True              # 检测是否支持 TLS 1.3
    check_subject: bool = True               # 解析证书主体信息


# ── Docker Socket 配置 ────────────────────────────────────
@dataclass
class DockerConfig:
    """Docker Socket 检测"""
    enabled: bool = True
    socket_path: str = "/var/run/docker.sock"
    # 要监控的容器名/ID (留空则监控所有)
    watch_containers: List[str] = field(default_factory=list)


# ── 全局配置 ──────────────────────────────────────────────
@dataclass
class Config:
    check_interval: int = 60                # 检测间隔 (秒)
    log_level: str = "INFO"                  # DEBUG/INFO/WARNING/ERROR

    # 数据持久化
    state_file: str = "/data/state.json"    # 状态文件 (Docker volume 挂载)

    # 告警
    alert: AlertConfig = field(default_factory=AlertConfig)

    # 各检测模块
    l4: L4Config = field(default_factory=L4Config)
    l7: L7Config = field(default_factory=L7Config)
    cert: CertConfig = field(default_factory=CertConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)


# ── 监控目标定义 ──────────────────────────────────────────
# 修改这里添加/删除监控目标
SITES: list[dict] = [
    # ── 基础 HTTP 检测 ─────────────────────────────────
    {
        "name": "我的博客",
        "url": "https://blog.example.com",
        "enabled": True,
        "checks": ["l7"],
    },
    # ── L4 + L7 + 证书 ───────────────────────────────
    {
        "name": "API 服务",
        "url": "https://api.example.com",
        "enabled": True,
        "checks": ["l4", "l7", "cert"],
        "l4_port": 443,
        "l7_expected_keywords": ["ok", "success"],
        "l7_slow_threshold_ms": 1500,
    },
    # ── 只测端口 ─────────────────────────────────────
    {
        "name": "SSH 服务",
        "host": "ssh.example.com",
        "enabled": True,
        "checks": ["l4"],
        "l4_port": 22,
    },
    # ── CDN 检测示例 ────────────────────────────────
    {
        "name": "CDN 源站",
        "url": "https://cdn.example.com/static/app.js",
        "enabled": True,
        "checks": ["l7", "cdn"],
    },
]


# ── 默认配置实例 ───────────────────────────────────────────
config = Config()