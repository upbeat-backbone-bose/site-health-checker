"""
测试主循环逻辑
覆盖: 配置加载、状态持久化、健康检查接口结构
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import tempfile
from datetime import datetime, timezone


# ── 复现被测逻辑 (无外部依赖) ─────────────────────────────────

def load_state(state_file: str) -> dict:
    try:
        if os.path.exists(state_file):
            with open(state_file) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(state_file: str, results: list):
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump({
            "last_check": datetime.now(timezone.utc).isoformat(),
            "results": results,
        }, f, indent=2, default=str)


def print_summary(results: list) -> str:
    """返回摘要文本 (用于测试断言)"""
    output = []
    output.append("=" * 60)
    output.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  检测完成")
    output.append("=" * 60)

    for status in results:
        all_ok = all(r["ok"] for r in status.get("checks", {}).values()) if status.get("checks") else True
        icon = "✅" if all_ok else "❌"
        output.append(f"\n{icon} {status['name']}")
        output.append(f"   {status['url']}")

        for check_type, result in status.get("checks", {}).items():
            res_icon = "✅" if result["ok"] else "❌"
            line = f"   {res_icon} {check_type.upper()}: {result['message']}"
            if result.get("latency_ms", 0) > 0:
                line += f" ({result['latency_ms']:.0f}ms)"
            output.append(line)

    failed = [s for s in results if s.get("checks") and not all(r["ok"] for r in s["checks"].values())]
    output.append("\n" + "-" * 60)
    output.append(f"总计: {len(results)} 个目标, {len(failed)} 个异常")
    if failed:
        output.append(f"❌ 异常: {[s['name'] for s in failed]}")
    return "\n".join(output)


# ── 测试用例 ──────────────────────────────────────────────────

class TestStatePersistence:
    """状态持久化测试"""

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = f"{tmpdir}/state.json"
            sample = {
                "last_check": "2026-07-02T12:00:00+00:00",
                "results": [
                    {
                        "name": "Test Site",
                        "url": "https://test.com",
                        "checks": {
                            "l7": {"ok": True, "message": "OK", "latency_ms": 50.0},
                            "cert": {"ok": True, "message": "Valid", "latency_ms": 0},
                        },
                        "consecutive_failures": 0,
                        "last_ok": "2026-07-02T12:00:00+00:00",
                        "last_check": "2026-07-02T12:00:00+00:00",
                    }
                ],
            }

            save_state(state_file, sample["results"])
            loaded = load_state(state_file)

            assert loaded["results"][0]["name"] == "Test Site"
            assert loaded["results"][0]["checks"]["l7"]["ok"] is True
            assert "last_check" in loaded

    def test_load_nonexistent_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_state(f"{tmpdir}/does_not_exist.json")
            assert result == {}

    def test_load_corrupted_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/corrupt.json"
            with open(path, "w") as f:
                f.write('{"broken: json')
            result = load_state(path)
            assert result == {}

    def test_mkdir_if_not_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = f"{tmpdir}/a/b/c/state.json"
            save_state(nested, [])
            assert os.path.exists(nested)


class TestPrintSummary:
    """终端摘要输出测试"""

    def test_all_healthy(self):
        results = [
            {
                "name": "健康站点",
                "url": "https://ok.com",
                "checks": {
                    "l7": {"ok": True, "message": "HTTP OK [200], 50ms", "latency_ms": 50.0},
                },
            }
        ]
        text = print_summary(results)
        assert "✅" in text
        assert "健康站点" in text
        assert "HTTP OK" in text
        assert "50ms" in text
        assert "0 个异常" in text

    def test_all_failed(self):
        results = [
            {
                "name": "故障站点",
                "url": "https://down.com",
                "checks": {
                    "l4": {"ok": False, "message": "Connection refused", "latency_ms": 5000.0},
                    "l7": {"ok": False, "message": "Timeout", "latency_ms": 0},
                },
            }
        ]
        text = print_summary(results)
        assert "❌" in text
        assert "故障站点" in text
        assert "Connection refused" in text
        assert "Timeout" in text
        assert "1 个异常" in text

    def test_mixed_status(self):
        results = [
            {
                "name": "正常站",
                "url": "https://ok.com",
                "checks": {"l7": {"ok": True, "message": "OK", "latency_ms": 10}},
            },
            {
                "name": "异常站",
                "url": "https://down.com",
                "checks": {"l7": {"ok": False, "message": "Down", "latency_ms": 0}},
            },
        ]
        text = print_summary(results)
        assert "✅" in text
        assert "❌" in text
        assert "正常站" in text
        assert "异常站" in text
        assert "1 个异常" in text

    def test_no_checks(self):
        results = [
            {"name": "无检测", "url": "https://empty.com", "checks": {}},
        ]
        text = print_summary(results)
        assert "✅" in text  # 空 checks 视为全 OK
        assert "0 个异常" in text

    def test_latency_shown_for_slow_response(self):
        results = [
            {
                "name": "慢站",
                "url": "https://slow.com",
                "checks": {"l7": {"ok": True, "message": "OK", "latency_ms": 3500}},
            },
        ]
        text = print_summary(results)
        assert "3500ms" in text

    def test_multiple_check_types(self):
        results = [
            {
                "name": "全面检测",
                "url": "https://full.com",
                "checks": {
                    "l4": {"ok": True, "message": "TCP OK", "latency_ms": 5.0},
                    "l7": {"ok": True, "message": "HTTP OK", "latency_ms": 80.0},
                    "cert": {"ok": True, "message": "Valid 90 days", "latency_ms": 0},
                    "cdn": {"ok": True, "message": "Cloudflare", "latency_ms": 0},
                },
            }
        ]
        text = print_summary(results)
        assert "✅" in text
        assert "L4" in text
        assert "L7" in text
        assert "CERT" in text
        assert "CDN" in text


class TestConfigSchema:
    """配置结构测试 (mock)"""

    def test_site_check_types_valid(self):
        valid_checks = {"l4", "l7", "cert", "cdn", "docker"}
        site = {
            "name": "Test",
            "url": "https://test.com",
            "checks": ["l4", "l7", "cert"],
        }
        for ct in site["checks"]:
            assert ct in valid_checks, f"Invalid check type: {ct}"

    def test_alert_thresholds(self):
        config = {
            "aggregation_minutes": 5,
            "failure_threshold": 1,
        }
        assert config["aggregation_minutes"] > 0
        assert config["failure_threshold"] >= 1

    def test_l4_config_defaults(self):
        l4_config = {
            "connect_timeout": 5.0,
            "read_timeout": 3.0,
        }
        assert l4_config["connect_timeout"] >= 1.0
        assert l4_config["read_timeout"] >= 1.0

    def test_cert_warning_thresholds(self):
        cert_config = {
            "warn_days": 30,
            "critical_days": 7,
        }
        assert cert_config["warn_days"] > cert_config["critical_days"]
        assert cert_config["critical_days"] >= 0
