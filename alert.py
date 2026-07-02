"""
告警模块 - 钉钉/飞书/Telegram Webhook + 防抖聚合
"""

import json
import time
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import config, AlertConfig
from monitor import SiteStatus, CheckResult

logger = logging.getLogger("alert")


# ════════════════════════════════════════════════════════════
# 防抖: 状态持久化
# ════════════════════════════════════════════════════════════

class AlertState:
    """告警状态追踪: 防止同一目标短时间内重复告警"""

    def __init__(self, state_file: str = "/data/alert_state.json"):
        self.state_file = state_file
        self.alerts: dict = {}      # target_key -> {count, first_at, last_at}
        self.cooldown: dict = {}     # target_key -> cooldown_until_timestamp
        self._load()

    def _state_path(self) -> str:
        return self.state_file

    def _load(self):
        import os
        try:
            if os.path.exists(self._state_path()):
                with open(self._state_path()) as f:
                    d = json.load(f)
                    self.alerts = d.get("alerts", {})
                    self.cooldown = d.get("cooldown", {})
        except Exception as e:
            logger.warning(f"加载告警状态失败: {e}, 从头开始")

    def save(self):
        import os
        os.makedirs(os.path.dirname(self._state_path()), exist_ok=True)
        try:
            with open(self._state_path(), "w") as f:
                json.dump({"alerts": self.alerts, "cooldown": self.cooldown}, f, indent=2)
        except Exception as e:
            logger.warning(f"保存告警状态失败: {e}")

    def should_alert(self, target_key: str, consecutive_failures: int) -> bool:
        """判断是否应该告警 (防抖)"""
        now = time.time()

        # 检查冷却期
        if target_key in self.cooldown:
            if now < self.cooldown[target_key]:
                logger.debug(f"[{target_key}] 在冷却期内, 跳过")
                return False
            else:
                del self.cooldown[target_key]

        # 检查连续失败阈值
        if consecutive_failures < config.alert.failure_threshold:
            return False

        return True

    def record_alert(self, target_key: str, is_recovery: bool = False):
        """记录本次告警, 启动冷却期"""
        now = time.time()
        cooldown_seconds = config.alert.aggregation_minutes * 60
        self.cooldown[target_key] = now + cooldown_seconds

        if target_key not in self.alerts:
            self.alerts[target_key] = {"count": 0, "first_at": now}
        self.alerts[target_key]["count"] += 1
        self.alerts[target_key]["last_at"] = now

        self.save()
        logger.info(f"[{target_key}] 告警已记录, 冷却 {cooldown_seconds}s")


# ════════════════════════════════════════════════════════════
# Webhook 发送
# ════════════════════════════════════════════════════════════

def _send_dingtalk(url: str, payload: dict, timeout: float = 10.0) -> bool:
    """发送钉钉消息"""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(url, json=payload)
            result = resp.json()
            if result.get("errcode") == 0:
                return True
            logger.error(f"钉钉告警失败: {result}")
            return False
    except Exception as e:
        logger.error(f"钉钉请求异常: {e}")
        return False


def _send_feishu(url: str, payload: dict, timeout: float = 10.0) -> bool:
    """发送飞书消息"""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(url, json=payload)
            result = resp.json()
            if result.get("code") == 0:
                return True
            logger.error(f"飞书告警失败: {result}")
            return False
    except Exception as e:
        logger.error(f"飞书请求异常: {e}")
        return False


def _send_telegram(token: str, chat_id: str, text: str, timeout: float = 10.0) -> bool:
    """发送 Telegram 消息"""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            result = resp.json()
            if result.get("ok"):
                return True
            logger.error(f"Telegram 告警失败: {result}")
            return False
    except Exception as e:
        logger.error(f"Telegram 请求异常: {e}")
        return False


# ════════════════════════════════════════════════════════════
# 消息构建
# ════════════════════════════════════════════════════════════

def build_dingtalk_markdown(results: list[SiteStatus], is_recovery: bool = False) -> dict:
    """构建钉钉 Markdown 消息"""
    alert_type = "🔄 恢复" if is_recovery else "🚨 故障"
    title = f"{alert_type} - 监控告警"

    content_lines = [f"# {title}", ""]

    for status in results:
        emoji = "✅" if all(r.ok for r in status.checks.values()) else "❌"
        content_lines.append(f"## {emoji} {status.name}")
        content_lines.append(f"- URL: {status.url}")

        for check_type, result in status.checks.items():
            icon = "✅" if result.ok else "❌"
            content_lines.append(f"- {icon} [{check_type.upper()}] {result.message}")
            if result.latency_ms > 0:
                content_lines.append(f"  - 延迟: {result.latency_ms:.0f}ms")

        content_lines.append("")

    content_lines.append(f"\n> ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": "\n".join(content_lines),
        }
    }


def build_feishu_card(results: list[SiteStatus], is_recovery: bool = False) -> dict:
    """构建飞书卡片消息"""
    alert_type = "🔄 恢复" if is_recovery else "🚨 故障"

    elements = []
    for status in results:
        all_ok = all(r.ok for r in status.checks.values())
        emoji = "✅" if all_ok else "❌"

        check_lines = []
        for check_type, result in status.checks.items():
            icon = "✅" if result.ok else "❌"
            check_lines.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"{icon} **{check_type.upper()}**: {result.message}"
                }
            })

        elements.append({
            "tag": "card",
            "header": {
                "title": {"tag": "plain_text", "content": f"{emoji} {status.name}"},
                "template": "red" if not all_ok else "green",
            },
            "elements": check_lines + [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"`{status.url}`"}},
                {"tag": "hr"},
            ]
        })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"{alert_type} - {datetime.now().strftime('%H:%M:%S')}"},
                "template": "red" if not all(status.checks and all(r.ok for r in status.checks.values()) for status in results) else "green",
            },
            "elements": elements
        }
    }


def build_telegram_text(results: list[SiteStatus], is_recovery: bool = False) -> str:
    """构建 Telegram 文本消息"""
    alert_type = "🔄 RECOVERED" if is_recovery else "🚨 ALERT"
    lines = [f"*{alert_type}*", ""]

    for status in results:
        all_ok = all(r.ok for r in status.checks.values())
        emoji = "✅" if all_ok else "❌"
        lines.append(f"{emoji} *{status.name}*")
        lines.append(f"  URL: {status.url}")

        for check_type, result in status.checks.items():
            icon = "✅" if result.ok else "❌"
            lines.append(f"  {icon} `{check_type.upper()}` {result.message}")

        lines.append("")

    lines.append(f"\n__{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}__")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# 主告警入口
# ════════════════════════════════════════════════════════════

_state = AlertState()


def send_alert(results: list[SiteStatus], is_recovery: bool = False):
    """发送告警到所有配置的渠道"""
    if not config.alert.enabled:
        return

    # 过滤出有问题的站点
    problem_sites = [s for s in results if s.checks and not all(r.ok for r in s.checks.values())]
    if not problem_sites:
        logger.debug("没有需要告警的问题站点")
        return

    # 构建告警 key (用于防抖)
    alert_keys = [s.name for s in problem_sites]
    alert_key = "||".join(sorted(alert_keys))

    # 防抖检查
    if not _state.should_alert(alert_key, consecutive_failures=1):
        return

    sent = False

    # 钉钉
    if config.alert.dingtalk_url:
        payload = build_dingtalk_markdown(problem_sites, is_recovery)
        if _send_dingtalk(config.alert.dingtalk_url, payload):
            sent = True
            logger.info(f"钉钉告警已发送: {alert_key}")

    # 飞书
    if config.alert.feishu_url:
        payload = build_feishu_card(problem_sites, is_recovery)
        if _send_feishu(config.alert.feishu_url, payload):
            sent = True
            logger.info(f"飞书告警已发送: {alert_key}")

    # Telegram
    if config.alert.telegram_token and config.alert.telegram_chat_id:
        text = build_telegram_text(problem_sites, is_recovery)
        if _send_telegram(config.alert.telegram_token, config.alert.telegram_chat_id, text):
            sent = True
            logger.info(f"Telegram 告警已发送: {alert_key}")

    if sent:
        _state.record_alert(alert_key, is_recovery)
