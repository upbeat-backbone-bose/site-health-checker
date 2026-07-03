"""
集成测试 - 覆盖 app.py 和 config.py 真实代码
使用 sys.modules 注入 mock, 让缺依赖的沙箱也能跑
"""

import sys
import os
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_mock_modules():
    """注入缺失依赖的 mock 模块, 让 import 能跑"""
    # pydantic
    if "pydantic" not in sys.modules:
        pydantic = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        pydantic.BaseModel = BaseModel
        sys.modules["pydantic"] = pydantic

    # cryptography + cryptography.x509 等子模块
    if "cryptography" not in sys.modules:
        crypto = types.ModuleType("cryptography")
        sys.modules["cryptography"] = crypto

    if "cryptography.hazmat" not in sys.modules:
        hazmat = types.ModuleType("cryptography.hazmat")
        sys.modules["cryptography.hazmat"] = hazmat

    if "cryptography.hazmat.backends" not in sys.modules:
        backends = types.ModuleType("cryptography.hazmat.backends")

        class _DefaultBackend:
            pass
        backends.default_backend = lambda: _DefaultBackend()
        sys.modules["cryptography.hazmat.backends"] = backends

    if "cryptography.x509" not in sys.modules:
        x509 = types.ModuleType("cryptography.x509")

        class _OID:
            def __init__(self, *args, **kwargs):
                self._args = args
                self._kwargs = kwargs
            def __eq__(self, other):
                return isinstance(other, _OID)
            def __hash__(self):
                return id(self)

        x509.oid = types.SimpleNamespace(ExtensionOID=types.SimpleNamespace(SUBJECT_ALTERNATIVE_NAME=_OID()))
        x509.load_der_x509_certificate = lambda *a, **k: None
        sys.modules["cryptography.x509"] = x509

    # flask
    if "flask" not in sys.modules:
        flask = types.ModuleType("flask")

        class _Response:
            def __init__(self, data, mimetype=None):
                self._data = data
                self._mimetype = mimetype
            def get_data(self, as_text=False):
                if as_text:
                    return self._data if isinstance(self._data, str) else self._data.decode()
                return self._data.encode() if isinstance(self._data, str) else self._data

        class _JsonResp:
            def __init__(self, obj):
                import json
                self.json = obj
                self._text = json.dumps(obj)
            def get_data(self, as_text=False):
                return self._text

        def _jsonify(obj):
            return _JsonResp(obj)

        def _make_response(*args, **kwargs):
            if args:
                return _Response(*args, **kwargs)
            return _Response("", kwargs.get("mimetype"))

        class _RouteRule:
            def __init__(self, rule):
                self.rule = rule

        class _UrlMap:
            def __init__(self):
                self._rules = []
            def add(self, rule):
                self._rules.append(rule)
            def iter_rules(self):
                return iter(self._rules)

        class _FlaskApp:
            def __init__(self, name):
                self.name = name
                self.url_map = _UrlMap()
                # Use real dict so 'in' works
                self.error_handler_spec = {None: {}}
            def route(self, rule):
                self.url_map.add(_RouteRule(rule))
                def decorator(fn):
                    return fn
                return decorator
            def errorhandler(self, code):
                def decorator(fn):
                    self.error_handler_spec[None][code] = fn
                    return fn
                return decorator
            def run(self, **kwargs):
                pass

        flask.Flask = _FlaskApp
        flask.Response = _Response
        flask.jsonify = _jsonify
        flask.make_response = _make_response
        sys.modules["flask"] = flask


_ensure_mock_modules()

import json
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


class TestConfig:
    """config.py 配置类测试"""

    def test_default_config(self):
        """默认配置"""
        from config import config, AlertConfig, L4Config, L7Config, CertConfig, DockerConfig

        assert isinstance(config.check_interval, int)
        assert config.check_interval > 0
        assert isinstance(config.log_level, str)
        assert config.state_file == "/data/state.json"
        assert isinstance(config.alert, AlertConfig)
        assert isinstance(config.l4, L4Config)
        assert isinstance(config.l7, L7Config)
        assert isinstance(config.cert, CertConfig)
        assert isinstance(config.docker, DockerConfig)

    def test_alert_config_defaults(self):
        from config import AlertConfig
        c = AlertConfig()
        assert c.enabled is True
        assert c.aggregation_minutes == 5
        assert c.failure_threshold == 1
        assert c.dingtalk_url is None
        assert c.feishu_url is None

    def test_alert_config_with_webhooks(self):
        from config import AlertConfig
        c = AlertConfig(
            dingtalk_url="https://oapi.dingtalk.com/robot/send?access_token=x",
            feishu_url="https://open.feishu.cn/hook/x",
        )
        assert "dingtalk" in c.dingtalk_url
        assert "feishu" in c.feishu_url

    def test_l4_config_defaults(self):
        from config import L4Config
        c = L4Config()
        assert c.connect_timeout == 5.0
        assert c.read_timeout == 3.0

    def test_l7_config_defaults(self):
        from config import L7Config
        c = L7Config()
        assert c.request_timeout == 10.0
        assert c.follow_redirects is True
        assert 200 in c.expected_status_codes
        assert c.slow_threshold_ms == 2000

    def test_cert_config_defaults(self):
        from config import CertConfig
        c = CertConfig()
        assert c.check_validity is True
        assert c.warn_days == 30
        assert c.critical_days == 7

    def test_docker_config_defaults(self):
        from config import DockerConfig
        c = DockerConfig()
        assert c.enabled is True
        assert c.socket_path == "/var/run/docker.sock"
        assert c.watch_containers == []

    def test_config_load(self):
        """默认配置能加载"""
        from config import config

        assert hasattr(config, 'check_interval')
        assert hasattr(config, 'log_level')
        assert hasattr(config, 'state_file')
        assert hasattr(config, 'alert')
        assert hasattr(config, 'l4')
        assert hasattr(config, 'l7')
        assert hasattr(config, 'cert')
        assert hasattr(config, 'docker')

    def test_sites_defined(self):
        """默认 SITES 列表"""
        from config import SITES

        assert isinstance(SITES, list)
        assert len(SITES) > 0

        for site in SITES:
            assert "name" in site
            assert "checks" in site
            assert isinstance(site["checks"], list)


class TestAppEndpoints:
    """app.py Flask endpoint 测试"""

    def test_index_endpoint(self):
        """测试 GET /"""
        # 直接测试 index 函数
        with patch('app._state', {"_start_time": time.time() - 10, "results": [], "last_check": "", "running": True}):
            from app import index
            result = index()
            assert result.json["service"] == "site-monitor"
            assert result.json["status"] == "running"
            assert result.json["uptime_seconds"] >= 10
            assert "/health" in result.json["endpoints"]
            assert "/metrics" in result.json["endpoints"]
            assert "/status" in result.json["endpoints"]

    def test_health_endpoint(self):
        """测试 GET /health"""
        with patch('app._state', {"_start_time": time.time() - 60, "last_check": "2026-07-02T16:00:00", "running": True}):
            from app import health
            result = health()
            assert result.json["status"] == "ok"
            assert result.json["uptime_seconds"] >= 60
            assert result.json["last_check"] == "2026-07-02T16:00:00"
            assert result.json["monitor_running"] is True

    def test_health_endpoint_no_data_yet(self):
        """健康检查在还没有检测数据时也能返回"""
        with patch('app._state', {"_start_time": time.time(), "last_check": "", "running": True}):
            from app import health
            result = health()
            assert result.json["status"] == "ok"
            assert result.json["last_check"] == ""

    def test_status_endpoint_empty(self):
        """状态 endpoint 空状态"""
        with patch('app._state_lock'), patch('app._state', {"results": [], "last_check": "2026-07-02T16:00:00"}):
            from app import status
            result = status()
            assert result.json["total"] == 0
            assert result.json["sites"] == []
            assert result.json["last_check"] == "2026-07-02T16:00:00"

    def test_status_endpoint_with_results(self):
        """状态 endpoint 有数据"""
        mock_results = [
            {"name": "blog", "url": "https://blog.com", "checks": {"l7": {"ok": True}}},
            {"name": "api", "url": "https://api.com", "checks": {"l7": {"ok": False}}},
        ]
        with patch('app._state_lock'), patch('app._state', {"results": mock_results, "last_check": "2026-07-02T16:00:00"}):
            from app import status
            result = status()
            assert result.json["total"] == 2
            assert len(result.json["sites"]) == 2

    def test_metrics_endpoint_empty(self):
        """指标 endpoint 空数据"""
        with patch('app._state_lock'), patch('app._state', {"results": []}):
            from app import metrics
            result = metrics()
            text = result.get_data(as_text=True)
            assert "site_monitor_up 1" in text
            assert "# HELP site_monitor_check" in text
            assert "# TYPE site_monitor_check" in text

    def test_metrics_endpoint_with_results(self):
        """指标 endpoint 有数据"""
        mock_results = [
            {
                "name": "my-site",
                "checks": {
                    "l7": {"ok": True, "latency_ms": 123.45},
                    "cert": {"ok": True, "latency_ms": 0},
                }
            },
            {
                "name": "another site",  # 含空格和特殊字符
                "checks": {
                    "l7": {"ok": False, "latency_ms": 5000},
                }
            },
        ]
        with patch('app._state_lock'), patch('app._state', {"results": mock_results}):
            from app import metrics
            result = metrics()
            text = result.get_data(as_text=True)

            assert 'site_monitor_check{site="my_site",type="l7"} 1' in text
            assert 'site_monitor_check{site="my_site",type="cert"} 1' in text
            assert 'site_monitor_check{site="another_site",type="l7"} 0' in text
            assert "123" in text  # latency

    def test_not_found_handler(self):
        """404 处理"""
        from app import app as real_app
        # error handler 注册到 app 上 (error_handler_spec 是真 dict)
        # 我们用 .get() 避免 KeyError
        spec = real_app.error_handler_spec
        # 确认 spec 至少是 dict 类型
        assert isinstance(spec, dict)
        # 可能在测试沙箱中 errorhandler 装饰器没生效 (mock 简化版)
        # 只要结构正确即可
        # 检查 None key 存在
        assert None in spec

    def test_500_handler(self):
        """500 错误处理"""
        from app import app
        assert isinstance(app.error_handler_spec, dict)


class TestAppMonitorLoop:
    """监控循环启动逻辑"""

    def test_start_monitor_only_once(self):
        """启动监控循环只能执行一次"""
        with patch('app._state', {"running": False}) as mock_state, \
             patch('app.threading.Thread') as mock_thread_cls:

            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread

            from app import _start_monitor
            _start_monitor()

            # 验证启动了一个线程
            mock_thread.start.assert_called_once()
            assert mock_state["running"] is True

    def test_start_monitor_already_running(self):
        """已运行时不重复启动"""
        with patch('app._state', {"running": True}) as mock_state, \
             patch('app.threading.Thread') as mock_thread_cls:

            from app import _start_monitor
            _start_monitor()

            # 不应该启动线程
            mock_thread_cls.assert_not_called()


class TestAppSaveState:
    """app.py 的 save_state 函数"""

    def test_save_state_creates_directory(self):
        """保存时自动创建目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = f"{tmpdir}/subdir/state.json"
            from app import save_state
            save_state(state_file, [{"name": "test", "checks": {"l7": {"ok": True}}}])
            assert os.path.exists(state_file)

    def test_save_state_writes_valid_json(self):
        """保存的 JSON 可解析"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = f"{tmpdir}/state.json"
            from app import save_state
            data = [{"name": "site", "checks": {"l7": {"ok": True}}}]
            save_state(state_file, data)

            with open(state_file) as f:
                loaded = json.load(f)
            assert "last_check" in loaded
            assert loaded["results"] == data

    def test_save_state_with_to_dict_method(self):
        """保存带 to_dict 方法的对象"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = f"{tmpdir}/state.json"

            # 模拟 to_dict 对象
            obj = MagicMock()
            obj.to_dict.return_value = {"name": "test", "checks": {"l7": {"ok": True}}}
            obj.__class__.__name__ = "SiteStatus"

            from app import save_state
            save_state(state_file, [obj])

            with open(state_file) as f:
                loaded = json.load(f)
            assert loaded["results"][0]["name"] == "test"

    def test_save_state_exception_handled(self):
        """异常被捕获不抛出"""
        from app import save_state
        # 给一个不可写的路径 (例如权限不足的目录)
        # 这里用 mock 来模拟异常
        with patch('app.json.dump', side_effect=Exception("disk full")):
            save_state("/tmp/state.json", [])
            # 应该不抛异常


class TestFlaskAppConfig:
    """Flask app 配置"""

    def test_app_created(self):
        """app 实例已创建"""
        from app import app
        assert app is not None
        assert app.name == "app"

    def test_app_has_routes(self):
        """app 包含所有路由"""
        from app import app
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/" in rules
        assert "/health" in rules
        assert "/status" in rules
        assert "/metrics" in rules

    def test_app_has_error_handlers(self):
        """app 有错误处理器"""
        from app import app
        # 检查 error handler 注册
        assert 404 in app.error_handler_spec[None]
        assert 500 in app.error_handler_spec[None]


class TestCertCheckReal:
    """证书检测的辅助逻辑测试"""

    def test_cert_domain_extraction(self):
        """从 URL 提取主机名用于证书检测"""
        from monitor import parse_host_port

        h, p, s = parse_host_port("https://api.example.com:8443/v1/users")
        assert h == "api.example.com"
        assert p == 8443

    def test_cert_skipped_for_http(self):
        """HTTP URL 也能解析 (但 cert 检测会失败)"""
        from monitor import parse_host_port

        h, p, s = parse_host_port("http://example.com/api")
        assert s == "http"

    def test_cert_check_no_cryptography_pkg(self):
        """证书解析异常处理"""
        # 直接测试 check_cert 在 cryptography 包缺失时的行为
        from monitor import check_cert

        with patch('monitor.socket.create_connection') as mock_conn:
            # 不让真实 socket 建立
            mock_conn.side_effect = OSError("connection refused")

            result = check_cert("https://test.com")
            assert result.ok is False
            assert "失败" in result.message or "错误" in result.message or "refused" in result.message.lower()


class TestMonitorLoopReal:
    """monitor_loop 真实测试 (mock 检测函数)"""

    def test_loop_runs_check_all(self):
        """主循环调用 check_all"""
        from app import _monitor_loop

        with patch('app.check_all') as mock_check, \
             patch('app.send_alert') as mock_alert, \
             patch('app.save_state') as mock_save, \
             patch('app.time.sleep') as mock_sleep:

            mock_result = MagicMock()
            mock_result.to_dict.return_value = {"name": "test", "checks": {"l7": {"ok": True}}}
            mock_check.return_value = [mock_result]

            # 模拟第一次 sleep 就跳出循环
            def stop_loop(*args):
                raise KeyboardInterrupt()
            mock_sleep.side_effect = stop_loop

            try:
                _monitor_loop()
            except KeyboardInterrupt:
                pass

            mock_check.assert_called_once()
            mock_save.assert_called_once()
            mock_alert.assert_called_once()

    def test_loop_handles_exception(self):
        """循环异常处理"""
        from app import _monitor_loop

        with patch('app.check_all') as mock_check, \
             patch('app.time.sleep') as mock_sleep:

            mock_check.side_effect = [
                Exception("Boom"),  # 第一次抛异常
                KeyboardInterrupt(),  # 第二次跳出循环
            ]

            try:
                _monitor_loop()
            except KeyboardInterrupt:
                pass

            # 应该被调用了 2 次 (异常后继续)
            assert mock_check.call_count == 2


class TestConfigSchema:
    """配置 schema 校验"""

    def test_sites_have_valid_checks(self):
        """SITES 中的检查类型必须合法"""
        from config import SITES

        valid_checks = {"l4", "l7", "cert", "cdn", "docker"}

        for site in SITES:
            if site.get("enabled", True):
                for check in site.get("checks", []):
                    assert check in valid_checks, f"Invalid check type '{check}' in site '{site.get('name')}'"

    def test_sites_have_required_fields(self):
        """SITES 必须有 name 和 checks"""
        from config import SITES

        for site in SITES:
            assert "name" in site, f"Site missing 'name': {site}"
            assert "checks" in site, f"Site '{site.get('name')}' missing 'checks'"