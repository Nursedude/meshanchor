"""MF review S3 (2026-09-28), ported: the headless gateway launcher must not
start a bridge on DEFAULTS when gateway.json failed to load, and must not
start one when save() refused the enable."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import launcher  # noqa: E402


def _cfg(load_error=None, enabled=False, save_ok=True):
    cfg = SimpleNamespace(load_error=load_error, enabled=enabled)
    cfg.get_config_path = lambda: Path("/nonexistent/gateway.json")
    cfg.save = MagicMock(return_value=save_ok)
    return cfg


def _launch(cfg, answer="y"):
    bridge_cls = MagicMock()
    bridge_cls.return_value.start.return_value = False
    gw = MagicMock()
    gw.load.return_value = cfg
    with patch.object(launcher, "_HAS_BRIDGE", True), \
         patch.object(launcher, "_HAS_GATEWAY_CONFIG", True), \
         patch.object(launcher, "GatewayConfig", gw), \
         patch.object(launcher, "RNSMeshtasticBridge", bridge_cls), \
         patch("builtins.input", return_value=answer):
        launcher.launch_gateway_bridge(Path("/tmp"))
    return bridge_cls


def test_failed_load_starts_nothing(capsys):
    cfg = _cfg(load_error="JSONDecodeError: Expecting value", enabled=False)
    bridge = _launch(cfg)
    bridge.assert_not_called()
    cfg.save.assert_not_called()
    out = capsys.readouterr().out
    assert "failed to load" in out and "JSONDecodeError" in out and "defaults" in out


def test_failed_load_with_enabled_default_true_still_starts_nothing():
    _launch(_cfg(load_error="PermissionError: denied", enabled=True)).assert_not_called()


def test_refused_save_starts_nothing(capsys):
    cfg = _cfg(load_error=None, enabled=False, save_ok=False)
    bridge = _launch(cfg, answer="y")
    cfg.save.assert_called_once()
    bridge.assert_not_called()
    assert "not starting" in capsys.readouterr().out


def test_clean_load_and_saved_enable_starts_the_bridge():
    cfg = _cfg(load_error=None, enabled=False, save_ok=True)
    bridge = _launch(cfg, answer="y")
    assert cfg.enabled is True
    bridge.assert_called_once_with(cfg)
