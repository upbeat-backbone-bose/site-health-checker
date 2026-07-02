"""
测试告警模块
覆盖: 防抖逻辑、Webhook 消息构建、告警聚合
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import json
import tempfile
from datetime import datetime, timezone


# ── 复现被测逻辑 (无外部依赖) ─────────────────────────────────

class AlertState:
    def __init__(self, state_file: str):
        self.state_file = state_file
        self.alerts: dict = {}
        self.cooldown: dict = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file) as f:
                    d = json.load(f)
                    self.alerts = d.get("alerts", {})
                    self.cooldown = d.get("cooldown", {})
        except Exception:
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump({"alerts": self.alerts, "cooldown": self.cooldown}, f, indent=2)

    def should_alert(self, target_key: str, consecutive_failures: int, aggregation_minutes: int = 5, failure_threshold: int = 1) -> bool:
        now = time.time()
        if target_key in self.cooldown:
            if now < self.cooldown[target_key]:
                return False
            else:
                del self.cooldown[target_key]
        if consecutive_failures < failure_threshold:
            return False
        return True

    def record_alert(self, target_key: str, aggregation_minutes: int = 5):
        now = time.time()
        self.cooldown[target_key] = now + aggregation_minutes * 60
        if target_key not in self.alerts:
            self.alerts[target_key] = {"count": 0, "first_at": now}
        self.alerts[target_key]["count"] += 1
        self.alerts[target_key]["last_at"] = now
        self.save()


# ── 消息构建 (简化版, 验证结构正确) ──────────────────────────

def build_dingtalk_text(results: list, is_recovery: bool = False) -> str:
    alert_type = "🔄 恢复" if is_recovery else "🚨 故障"
    lines = [f"## {alert_type}", ""]
    for r in results:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"{icon} {r['name']}: {r['message']}")
    lines.append(f"\n> ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    return "\n".join(lines)


def build_telegram_text(results: list, is_recovery: bool = False) -> str:
    alert_type = "🔄 RECOVERED" if is_recovery else "🚨 ALERT"
    lines = [f"*{alert_type}*", ""]
    for r in results:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"{icon} *{r['name']}*: {r['message']}")
    return "\n".join(lines)


# ── 测试用例 ──────────────────────────────────────────────────

class TestAlertState:
    """AlertState 防抖逻辑测试"""

    def test_first_alert_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/state.json")
            assert state.should_alert("test-site", 1, aggregation_minutes=5, failure_threshold=1) is True

    def test_cooldown_blocks_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/state.json")
            state.record_alert("test-site", aggregation_minutes=5)
            # 立即再查，应该被冷却
            state2 = AlertState(f"{tmpdir}/state.json")
            assert state2.should_alert("test-site", 1, aggregation_minutes=5) is False

    def test_cooldown_expires_after_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/state.json")
            state.cooldown["test-site"] = time.time() - 1  # 已过期
            assert state.should_alert("test-site", 1) is True

    def test_failure_threshold_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/state.json")
            # 连续失败 0 次，不告警
            assert state.should_alert("test-site", 0, failure_threshold=1) is False
            # 连续失败 2 次，告警
            assert state.should_alert("test-site", 2, failure_threshold=1) is True

    def test_different_targets_independent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/state.json")
            state.record_alert("site-A", aggregation_minutes=5)
            # site-B 不受影响
            assert state.should_alert("site-B", 1, aggregation_minutes=5) is True

    def test_alert_count_persists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/state.json"
            s1 = AlertState(path)
            s1.record_alert("site")
            s1.record_alert("site")
            s1.record_alert("site")

            s2 = AlertState(path)
            assert s2.alerts["site"]["count"] == 3

    def test_state_file_created(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/state.json"
            state = AlertState(path)
            state.record_alert("test")
            assert os.path.exists(path)
            with open(path) as f:
                d = json.load(f)
                assert "alerts" in d
                assert "cooldown" in d


class TestAlertMessage:
    """告警消息构建测试"""

    def test_dingtalk_recovery_message(self):
        results = [{"name": "blog", "ok": True, "message": "HTTP OK [200], 120ms"}]
        text = build_dingtalk_text(results, is_recovery=True)
        assert "🔄 恢复" in text
        assert "blog" in text
        assert "✅" in text

    def test_dingtalk_alert_message(self):
        results = [
            {"name": "API", "ok": False, "message": "TCP 连接超时"},
        ]
        text = build_dingtalk_text(results, is_recovery=False)
        assert "🚨 故障" in text
        assert "API" in text
        assert "❌" in text
        assert "TCP 连接超时" in text

    def test_telegram_alert_format(self):
        results = [{"name": "web", "ok": False, "message": "证书已过期"}]
        text = build_telegram_text(results)
        assert "🚨 ALERT" in text
        assert "*web*" in text  # Telegram bold

    def test_telegram_recovery_format(self):
        results = [{"name": "site", "ok": True, "message": "All OK"}]
        text = build_telegram_text(results, is_recovery=True)
        assert "🔄 RECOVERED" in text

    def test_multi_site_message(self):
        results = [
            {"name": "site-A", "ok": True, "message": "OK"},
            {"name": "site-B", "ok": False, "message": "Down"},
            {"name": "site-C", "ok": False, "message": "Cert expired"},
        ]
        text = build_dingtalk_text(results)
        assert "site-A" in text
        assert "site-B" in text
        assert "site-C" in text
        assert "✅" in text
        assert "❌" in text


class TestAlertAggregation:
    """告警聚合场景测试"""

    def test_5min_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/s.json")
            state.record_alert("site", aggregation_minutes=5)
            remaining = state.cooldown["site"] - time.time()
            assert 299 <= remaining <= 301, f"期望约300秒，实际{remaining}s"

    def test_30min_cooldown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = AlertState(f"{tmpdir}/s.json")
            state.record_alert("site", aggregation_minutes=30)
            remaining = state.cooldown["site"] - time.time()
            assert 1799 <= remaining <= 1801, f"期望约1800秒，实际{remaining}s"
