"""
集成测试 - 覆盖 alert.py 真实代码
"""

import sys
import os
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_mock_modules():
    if "pydantic" not in sys.modules:
        pydantic = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        pydantic.BaseModel = BaseModel
        sys.modules["pydantic"] = pydantic

    if "cryptography" not in sys.modules:
        sys.modules["cryptography"] = types.ModuleType("cryptography")
    if "cryptography.hazmat" not in sys.modules:
        sys.modules["cryptography.hazmat"] = types.ModuleType("cryptography.hazmat")
    if "cryptography.hazmat.backends" not in sys.modules:
        backends = types.ModuleType("cryptography.hazmat.backends")
        backends.default_backend = lambda: None
        sys.modules["cryptography.hazmat.backends"] = backends
    if "cryptography.x509" not in sys.modules:
        x509 = types.ModuleType("cryptography.x509")

        class _OID:
            def __init__(self, *args, **kwargs):
                pass
            def __eq__(self, other):
                return isinstance(other, _OID)
            def __hash__(self):
                return id(self)
        x509.oid = types.SimpleNamespace(ExtensionOID=types.SimpleNamespace(SUBJECT_ALTERNATIVE_NAME=_OID()))
        x509.load_der_x509_certificate = lambda *a, **k: None
        sys.modules["cryptography.x509"] = x509


_ensure_mock_modules()

import json
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, Mock

import httpx


class TestAlertState:
    """AlertState 真实类测试"""

    def _make_state(self, tmpdir, name="s.json"):
        from alert import AlertState
        return AlertState(f"{tmpdir}/{name}")

    def _patch_config(self, **overrides):
        """patch config.alert 的属性"""
        defaults = {
            "aggregation_minutes": 5,
            "failure_threshold": 1,
        }
        defaults.update(overrides)
        return patch.multiple("alert.config.alert", **defaults)

    def test_save_and_load(self):
        """保存和加载状态"""
        from alert import AlertState

        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/state.json"
            state = AlertState(path)
            state.alerts = {"site-a": {"count": 5, "first_at": 1234.0, "last_at": 5678.0}}
            state.cooldown = {"site-a": 9999.0}
            state.save()

            loaded = AlertState(path)
            assert loaded.alerts["site-a"]["count"] == 5
            assert loaded.cooldown["site-a"] == 9999.0

    def test_load_missing_file(self):
        """加载不存在的文件"""
        from alert import AlertState

        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/never.json"
            state = AlertState(path)
            assert state.alerts == {}
            assert state.cooldown == {}

    def test_load_corrupted_file(self):
        """加载损坏的文件"""
        from alert import AlertState

        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/broken.json"
            with open(path, "w") as f:
                f.write("{broken json")
            state = AlertState(path)
            assert state.alerts == {}

    def test_should_alert_first_time(self):
        """首次告警"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            with self._patch_config():
                assert state.should_alert("site", consecutive_failures=1) is True

    def test_should_alert_in_cooldown(self):
        """冷却期内不告警"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            state.cooldown["site"] = time.time() + 60  # 60s 后过期

            with self._patch_config():
                assert state.should_alert("site", consecutive_failures=1) is False

    def test_should_alert_cooldown_expired(self):
        """冷却过期后可以告警"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            state.cooldown["site"] = time.time() - 1  # 已过期

            with self._patch_config():
                assert state.should_alert("site", consecutive_failures=1) is True

    def test_should_alert_threshold_not_met(self):
        """未达失败阈值不告警"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            with self._patch_config(failure_threshold=1):
                assert state.should_alert("site", consecutive_failures=0) is False
            with self._patch_config(failure_threshold=3):
                assert state.should_alert("site", consecutive_failures=2) is False

    def test_record_alert(self):
        """记录告警"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            with self._patch_config(aggregation_minutes=5):
                state.record_alert("site")

            assert state.cooldown["site"] > time.time()
            assert state.alerts["site"]["count"] == 1

    def test_record_multiple_alerts(self):
        """多次记录累加"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._make_state(tmpdir)
            with self._patch_config(aggregation_minutes=5):
                state.record_alert("site")
                state.record_alert("site")
                state.record_alert("site")

            assert state.alerts["site"]["count"] == 3


class TestSendDingTalk:
    """钉钉发送测试"""

    def test_send_dingtalk_success(self):
        """发送成功"""
        from alert import _send_dingtalk

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = {"errcode": 0, "errmsg": "ok"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=test", {"msgtype": "text", "text": {"content": "test"}})
            assert result is True

    def test_send_dingtalk_failure(self):
        """发送失败"""
        from alert import _send_dingtalk

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = {"errcode": 310000, "errmsg": "invalid token"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _send_dingtalk("https://invalid.url", {"msgtype": "text", "text": {"content": "test"}})
            assert result is False

    def test_send_dingtalk_exception(self):
        """网络异常"""
        from alert import _send_dingtalk

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.side_effect = Exception("Network error")
            mock_client_cls.return_value = mock_client

            result = _send_dingtalk("https://test.url", {"msgtype": "text"})
            assert result is False


class TestSendFeishu:
    """飞书发送测试"""

    def test_send_feishu_success(self):
        """飞书发送成功"""
        from alert import _send_feishu

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = {"code": 0, "msg": "success"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _send_feishu("https://open.feishu.cn/hook/test", {"msg_type": "text"})
            assert result is True

    def test_send_feishu_failure(self):
        """飞书发送失败"""
        from alert import _send_feishu

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = {"code": 99991663, "msg": "invalid token"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _send_feishu("https://invalid.url", {"msg_type": "text"})
            assert result is False

    def test_send_feishu_exception(self):
        """网络异常"""
        from alert import _send_feishu

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.side_effect = httpx.RequestError("Network")
            mock_client_cls.return_value = mock_client

            result = _send_feishu("https://test.url", {"msg_type": "text"})
            assert result is False


class TestSendTelegram:
    """Telegram 发送测试"""

    def test_send_telegram_success(self):
        from alert import _send_telegram

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True, "result": {"message_id": 1}}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _send_telegram("123456:abc", "987", "Hello")
            assert result is True

    def test_send_telegram_failure(self):
        from alert import _send_telegram

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": False, "description": "Bad Request"}
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = _send_telegram("invalid", "987", "Hello")
            assert result is False

    def test_send_telegram_exception(self):
        from alert import _send_telegram

        with patch('alert.httpx.Client') as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.side_effect = Exception("Connection failed")
            mock_client_cls.return_value = mock_client

            result = _send_telegram("token", "chat", "msg")
            assert result is False


class TestMessageBuilders:
    """消息构建器测试"""

    def _make_status(self, name, ok=True, message="OK", url="https://test.com", checks=None):
        """构造 SiteStatus 类似对象"""
        from dataclasses import dataclass, field
        from typing import Dict

        @dataclass
        class _Status:
            name: str
            url: str
            ok: bool = True
            message: str = "OK"
            checks: Dict = field(default_factory=dict)

        if checks is None:
            checks = {"l7": type("R", (), {"ok": ok, "message": message, "latency_ms": 10})()}

        return _Status(name=name, url=url, ok=ok, message=message, checks=checks)

    def test_dingtalk_markdown_structure(self):
        """验证钉钉消息结构"""
        from alert import build_dingtalk_markdown

        results = [self._make_status("test", ok=False, message="Connection failed")]
        payload = build_dingtalk_markdown(results)

        assert payload["msgtype"] == "markdown"
        assert "markdown" in payload
        assert "title" in payload["markdown"]
        assert "text" in payload["markdown"]
        assert "test" in payload["markdown"]["text"]

    def test_dingtalk_recovery_message(self):
        """恢复消息"""
        from alert import build_dingtalk_markdown

        results = [self._make_status("test", ok=True)]
        payload = build_dingtalk_markdown(results, is_recovery=True)
        assert "恢复" in payload["markdown"]["text"]

    def test_dingtalk_multi_sites(self):
        """多站点"""
        from alert import build_dingtalk_markdown

        results = [
            self._make_status("site-A", ok=True),
            self._make_status("site-B", ok=False, message="Failed"),
        ]
        payload = build_dingtalk_markdown(results)
        assert "site-A" in payload["markdown"]["text"]
        assert "site-B" in payload["markdown"]["text"]

    def test_feishu_card_structure(self):
        """飞书卡片结构"""
        from alert import build_feishu_card

        results = [self._make_status("test", ok=False, message="Error")]
        payload = build_feishu_card(results)

        assert payload["msg_type"] == "interactive"
        assert "card" in payload
        assert "header" in payload["card"]
        assert "elements" in payload["card"]

    def test_feishu_recovery_card(self):
        """飞书恢复卡片"""
        from alert import build_feishu_card

        results = [self._make_status("test", ok=True)]
        payload = build_feishu_card(results, is_recovery=True)
        assert "恢复" in payload["card"]["header"]["title"]["content"]

    def test_telegram_alert_text(self):
        """Telegram 告警文本"""
        from alert import build_telegram_text

        results = [self._make_status("test", ok=False, message="Failed")]
        text = build_telegram_text(results)
        assert "ALERT" in text
        assert "test" in text

    def test_telegram_recovery_text(self):
        """Telegram 恢复文本"""
        from alert import build_telegram_text

        results = [self._make_status("test", ok=True)]
        text = build_telegram_text(results, is_recovery=True)
        assert "RECOVERED" in text


class TestSendAlert:
    """send_alert 端到端测试"""

    def _import_alert(self):
        """延迟导入避免循环依赖"""
        from alert import send_alert
        return send_alert

    def _make_problem_site(self, name="bad", url="https://bad.com"):
        """构造有问题的 SiteStatus"""
        from dataclasses import dataclass, field

        @dataclass
        class _Status:
            name: str
            url: str
            ok: bool = False
            checks: dict = field(default_factory=dict)

        @dataclass
        class _Check:
            ok: bool = False
            message: str = "Failed"
            latency_ms: float = 0

        return _Status(name=name, url=url, ok=False, checks={"l7": _Check()})

    def _make_healthy_site(self, name="good"):
        from dataclasses import dataclass, field

        @dataclass
        class _Status:
            name: str
            url: str
            ok: bool = True
            checks: dict = field(default_factory=dict)

        @dataclass
        class _Check:
            ok: bool = True
            message: str = "OK"
            latency_ms: float = 10

        return _Status(name=name, url="https://good.com", ok=True, checks={"l7": _Check()})

    def test_send_alert_disabled(self):
        """告警禁用时直接返回"""
        send_alert = self._import_alert()

        with patch('alert.config') as mock_config:
            mock_config.alert.enabled = False
            results = [self._make_problem_site()]
            with patch('alert._send_dingtalk') as m_d, patch('alert._send_feishu') as m_f, patch('alert._send_telegram') as m_t:
                send_alert(results)
                m_d.assert_not_called()
                m_f.assert_not_called()
                m_t.assert_not_called()

    def test_send_alert_no_problems(self):
        """无异常时不告警"""
        send_alert = self._import_alert()

        with patch('alert.config') as mock_config:
            mock_config.alert.enabled = True
            results = [self._make_healthy_site()]
            with patch('alert._send_dingtalk') as m_d:
                send_alert(results)
                m_d.assert_not_called()

    def test_send_alert_to_dingtalk(self):
        """发送钉钉告警"""
        send_alert = self._import_alert()

        with patch('alert.config') as mock_config, \
             patch('alert._send_dingtalk') as m_d, \
             patch('alert._state') as mock_state:

            mock_config.alert.enabled = True
            mock_config.alert.aggregation_minutes = 5
            mock_config.alert.failure_threshold = 1
            mock_config.alert.dingtalk_url = "https://test.url"
            mock_config.alert.feishu_url = None
            mock_config.alert.telegram_token = None
            mock_config.alert.telegram_chat_id = None

            mock_state.should_alert.return_value = True
            m_d.return_value = True

            results = [self._make_problem_site()]
            send_alert(results)
            m_d.assert_called_once()
            mock_state.record_alert.assert_called_once()

    def test_send_alert_cooldown_blocks(self):
        """冷却期内不发送"""
        send_alert = self._import_alert()

        with patch('alert.config') as mock_config, \
             patch('alert._send_dingtalk') as m_d, \
             patch('alert._state') as mock_state:

            mock_config.alert.enabled = True
            mock_config.alert.aggregation_minutes = 5
            mock_config.alert.failure_threshold = 1
            mock_config.alert.dingtalk_url = "https://test.url"

            mock_state.should_alert.return_value = False

            results = [self._make_problem_site()]
            send_alert(results)
            m_d.assert_not_called()

    def test_send_alert_to_multiple_channels(self):
        """多渠道发送"""
        send_alert = self._import_alert()

        with patch('alert.config') as mock_config, \
             patch('alert._send_dingtalk') as m_d, \
             patch('alert._send_feishu') as m_f, \
             patch('alert._send_telegram') as m_t, \
             patch('alert._state') as mock_state:

            mock_config.alert.enabled = True
            mock_config.alert.aggregation_minutes = 5
            mock_config.alert.failure_threshold = 1
            mock_config.alert.dingtalk_url = "https://ding.url"
            mock_config.alert.feishu_url = "https://feishu.url"
            mock_config.alert.telegram_token = "token"
            mock_config.alert.telegram_chat_id = "chat"

            mock_state.should_alert.return_value = True
            m_d.return_value = True
            m_f.return_value = True
            m_t.return_value = True

            results = [self._make_problem_site()]
            send_alert(results)
            m_d.assert_called_once()
            m_f.assert_called_once()
            m_t.assert_called_once()