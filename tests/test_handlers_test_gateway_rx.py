"""Tests for the Test Gateway RX handler (MN-2).

Smoke coverage for handler structure, gating, topic/payload builders, the
mosquitto_pub error paths, the log/conversation watchers, and the result
renderer. The MQTT/RNS/NomadNet integrations themselves are mocked.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.test_gateway_rx import TestGatewayRxHandler
    h = TestGatewayRxHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


def _fake_config(bridge_mode="mqtt_bridge", enabled=True, dest="abc123",
                 broker="localhost", port=1883, username="", password="",
                 use_tls=False, root="msh", region="US", channel="LongFast"):
    """Build a SimpleNamespace mirroring GatewayConfig's attribute shape."""
    return SimpleNamespace(
        enabled=enabled,
        bridge_mode=bridge_mode,
        rns=SimpleNamespace(default_lxmf_destination=dest),
        mqtt_bridge=SimpleNamespace(
            broker=broker, port=port, username=username, password=password,
            use_tls=use_tls, root_topic=root, region=region, channel=channel,
        ),
        meshtastic=SimpleNamespace(channel=2),
    )


# ── Structure / registration ────────────────────────────────────────


class TestStructure:

    def test_handler_registered(self):
        from handlers import get_all_handlers
        names = [c.__name__ for c in get_all_handlers()]
        assert "TestGatewayRxHandler" in names

    def test_handler_id_and_section(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        h = TestGatewayRxHandler()
        assert h.handler_id == "test_gateway_rx"
        assert h.menu_section == "mesh_networks"

    def test_menu_items_gated_on_gateway_flag(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        h = TestGatewayRxHandler()
        items = h.menu_items()
        assert len(items) == 1
        tag, _desc, flag = items[0]
        assert tag == "test_gateway_rx"
        assert flag == "gateway"

    def test_execute_unknown_action_is_safe(self):
        h = _make_handler()
        h.execute("nope")  # must not raise


# ── Topic + payload builders ────────────────────────────────────────


class TestBuilders:

    def test_topic_includes_region_when_set(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config(root="msh", region="US", channel="LongFast")
        topic = TestGatewayRxHandler._build_topic(cfg)
        assert topic == "msh/US/2/json/LongFast/!feedface"

    def test_topic_omits_region_when_blank(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config(root="msh", region="", channel="LongFast")
        topic = TestGatewayRxHandler._build_topic(cfg)
        assert topic == "msh/2/json/LongFast/!feedface"

    def test_topic_defaults_root_when_blank(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config(root="", region="US", channel="LongFast")
        topic = TestGatewayRxHandler._build_topic(cfg)
        assert topic.startswith("msh/")

    def test_payload_is_valid_json(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config()
        payload = TestGatewayRxHandler._build_payload("hello", cfg)
        parsed = json.loads(payload)
        assert parsed["payload"]["text"] == "hello"
        assert parsed["channel"] == 2
        assert parsed["sender"] == "!feedface"

    def test_payload_escapes_quotes(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config()
        payload = TestGatewayRxHandler._build_payload('he said "hi"', cfg)
        # Must still parse cleanly even with embedded quotes
        parsed = json.loads(payload)
        assert parsed["payload"]["text"] == 'he said "hi"'

    def test_payload_handles_non_int_channel(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config()
        cfg.meshtastic.channel = "not-a-number"
        payload = TestGatewayRxHandler._build_payload("x", cfg)
        parsed = json.loads(payload)
        # Falls back to default channel index 2
        assert parsed["channel"] == 2


# ── _publish_probe ─────────────────────────────────────────────────


class TestPublishProbe:

    def test_success(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config()
        fake = MagicMock(returncode=0, stderr="")
        with patch("subprocess.run", return_value=fake):
            ok, err = TestGatewayRxHandler._publish_probe(cfg, "topic", "payload")
        assert ok is True and err == ""

    def test_mosquitto_pub_missing(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config()
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            ok, err = TestGatewayRxHandler._publish_probe(cfg, "topic", "payload")
        assert ok is False
        assert "mosquitto_pub not installed" in err

    def test_timeout(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["mosquitto_pub"], timeout=10),
        ):
            ok, err = TestGatewayRxHandler._publish_probe(cfg, "topic", "payload")
        assert ok is False
        assert "timed out" in err

    def test_credentials_added_when_username_set(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config(username="alice", password="secret")
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            TestGatewayRxHandler._publish_probe(cfg, "topic", "payload")
        cmd = captured["cmd"]
        assert "-u" in cmd and "alice" in cmd
        assert "-P" in cmd and "secret" in cmd

    def test_tls_adds_capath(self):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        cfg = _fake_config(use_tls=True)
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return MagicMock(returncode=0, stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            TestGatewayRxHandler._publish_probe(cfg, "topic", "payload")
        assert "--capath" in captured["cmd"]


# ── _probe_in_logs / _probe_in_conversations ───────────────────────


class TestWatchers:

    def test_logs_finds_needle_in_meshanchor_log(self, tmp_path):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Filename must match the meshanchor_*.log glob (ported from
        # MeshForge's meshforge_*.log)
        (log_dir / "meshanchor_20260504.log").write_text(
            "some line\nthis line has needle-XYZ in it\n"
        )
        assert TestGatewayRxHandler._probe_in_logs(log_dir, "needle-XYZ") is True

    def test_logs_misses_when_glob_doesnt_match(self, tmp_path):
        """Sanity: a meshforge_*.log in MeshAnchor's dir is NOT picked up.
        Confirms the brand rename took effect."""
        from handlers.test_gateway_rx import TestGatewayRxHandler
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "meshforge_20260504.log").write_text("needle-XYZ here\n")
        assert TestGatewayRxHandler._probe_in_logs(log_dir, "needle-XYZ") is False

    def test_logs_returns_false_when_dir_missing(self, tmp_path):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        assert TestGatewayRxHandler._probe_in_logs(tmp_path / "nope", "x") is False

    def test_conversations_finds_recent_match(self, tmp_path):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        conv = tmp_path / "conversations"
        conv.mkdir()
        peer = conv / "abcdef1234"
        peer.mkdir()
        msg = peer / "msg1.txt"
        msg.write_text("body with needle-XYZ inside")
        # File mtime is "now" by default — within the 60s cutoff
        result = TestGatewayRxHandler._probe_in_conversations(conv, "needle-XYZ")
        assert result == msg

    def test_conversations_skips_old_files(self, tmp_path):
        from handlers.test_gateway_rx import TestGatewayRxHandler
        conv = tmp_path / "conversations"
        conv.mkdir()
        peer = conv / "abc"
        peer.mkdir()
        msg = peer / "old.txt"
        msg.write_text("needle-XYZ")
        # Push mtime 5 minutes into the past
        old = time.time() - 5 * 60
        os.utime(msg, (old, old))
        assert TestGatewayRxHandler._probe_in_conversations(conv, "needle-XYZ") is None


# ── Probe text branding ─────────────────────────────────────────────


class TestProbeBranding:

    def test_probe_text_uses_meshanchor_prefix(self, monkeypatch):
        """The probe needle must use 'meshanchor-rx-probe-' so it shows up in
        MeshAnchor logs (not MeshForge ones). This regression guard catches a
        future copy-paste from MeshForge that forgets to rename."""
        import handlers.test_gateway_rx as mod
        # Point at a fake config that satisfies all preflight gates so we
        # reach the probe-text construction site, then bail out at the
        # publish step by raising — the captured exception lets us inspect
        # state.
        h = _make_handler()
        captured = {}

        def fake_publish(config, topic, payload):
            captured["payload"] = payload
            return False, "fake-stop"

        monkeypatch.setattr(mod, "_HAS_GATEWAY_CONFIG", True)
        monkeypatch.setattr(mod, "_GatewayConfig", MagicMock(load=MagicMock(return_value=_fake_config())))
        monkeypatch.setattr(mod, "check_service",
                            lambda _: SimpleNamespace(available=True, fix_hint=None))
        monkeypatch.setattr(mod, "check_rns_shared_instance", lambda: True)
        monkeypatch.setattr(h, "_publish_probe", fake_publish)
        # Run — _publish_probe returns (False, ...) so the test bails before
        # the watch loop, keeping the test fast and offline.
        h._run_rx_test()
        # The payload's text field carries the probe needle
        parsed = json.loads(captured["payload"])
        text = parsed["payload"]["text"]
        assert text.startswith("meshanchor-rx-probe-"), (
            f"Expected 'meshanchor-rx-probe-...' prefix, got: {text!r}"
        )
        # Must NOT contain the MeshForge brand
        assert "meshforge" not in text.lower()


# ── Bridge mode warning ────────────────────────────────────────────


class TestBridgeModeWarning:

    def test_meshcore_bridge_default_triggers_warn(self, monkeypatch):
        """MeshAnchor's MeshCore-primary default ('meshcore_bridge') is not in
        the MQTT-aware modes set, so the handler asks for confirmation."""
        import handlers.test_gateway_rx as mod
        h = _make_handler()
        cfg = _fake_config(bridge_mode="meshcore_bridge")
        monkeypatch.setattr(mod, "_HAS_GATEWAY_CONFIG", True)
        monkeypatch.setattr(mod, "_GatewayConfig", MagicMock(load=MagicMock(return_value=cfg)))
        monkeypatch.setattr(mod, "check_service",
                            lambda _: SimpleNamespace(available=True, fix_hint=None))
        # User says "no" to the bridge-mode confirm; handler returns early
        h.ctx.dialog._yesno_returns = [False]
        h._run_rx_test()
        # Verify the dialog title was the bridge-mode warning
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == "yesno"]
        assert any("Non-MQTT Bridge Mode" in args[0] for _, args, _ in yesno_calls)

    def test_mqtt_bridge_does_not_trigger_bridge_mode_warn(self, monkeypatch):
        import handlers.test_gateway_rx as mod
        h = _make_handler()
        cfg = _fake_config(bridge_mode="mqtt_bridge")
        monkeypatch.setattr(mod, "_HAS_GATEWAY_CONFIG", True)
        monkeypatch.setattr(mod, "_GatewayConfig", MagicMock(load=MagicMock(return_value=cfg)))
        monkeypatch.setattr(mod, "check_service",
                            lambda _: SimpleNamespace(available=True, fix_hint=None))
        monkeypatch.setattr(mod, "check_rns_shared_instance", lambda: True)
        # Trip out at publish to keep the test offline
        monkeypatch.setattr(h, "_publish_probe", lambda *a, **kw: (False, "stop"))
        h._run_rx_test()
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == "yesno"]
        assert not any("Non-MQTT Bridge Mode" in args[0] for _, args, _ in yesno_calls)


# ── Registry integration ──────────────────────────────────────────


class TestRegistry:

    def test_visible_when_gateway_flag_on(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers
        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"gateway": True})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _ in reg.get_menu_items("mesh_networks")]
        assert "test_gateway_rx" in tags

    def test_hidden_when_gateway_flag_off(self):
        from handler_protocol import TUIContext
        from handler_registry import HandlerRegistry
        from handlers import get_all_handlers
        ctx = TUIContext(dialog=FakeDialog(), feature_flags={"gateway": False})
        reg = HandlerRegistry(ctx)
        for cls in get_all_handlers():
            reg.register(cls())
        tags = [tag for tag, _ in reg.get_menu_items("mesh_networks")]
        assert "test_gateway_rx" not in tags
