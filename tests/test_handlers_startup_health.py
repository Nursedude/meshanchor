"""
Unit tests for StartupHealthHandler.

Tests structure, lifecycle hooks, config conflict detection,
and SPI mismatch detection.
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

sys.path.insert(0, os.path.dirname(__file__))
from handler_test_utils import FakeDialog, make_handler_context


def _make_handler():
    from handlers.startup_health import StartupHealthHandler
    h = StartupHealthHandler()
    ctx = make_handler_context()
    h.set_context(ctx)
    return h


# ── Structure ───────────────────────────────────────────────────────


class TestStartupHealthStructure:

    def test_handler_id(self):
        h = _make_handler()
        assert h.handler_id == "startup_health"

    def test_menu_section(self):
        h = _make_handler()
        assert h.menu_section == "system"

    def test_menu_items_empty(self):
        """Startup health has no menu items — runs via lifecycle only."""
        h = _make_handler()
        assert h.menu_items() == []

    def test_execute_is_noop(self):
        h = _make_handler()
        h.execute("anything")  # Should not raise


# ── Lifecycle: on_startup ────────────────────────────────────────────


class TestStartupHealthLifecycle:

    def test_on_startup_calls_check(self):
        h = _make_handler()
        with patch.object(h, '_check_service_misconfig') as mock:
            h.on_startup()
            mock.assert_called_once()


# ── Config Misconfig Detection ──────────────────────────────────────


class TestStartupHealthConfigConflict:

    def test_no_config_d_directory(self):
        """No /etc/meshtasticd/config.d — skip silently."""
        h = _make_handler()
        with patch('pathlib.Path.exists', return_value=False):
            h._check_service_misconfig()
        # No dialog shown
        assert h.ctx.dialog.last_msgbox_title is None

    def test_spi_and_usb_conflict_detected(self):
        """SPI + USB configs both active — offer to fix."""
        h = _make_handler()

        config_d = MagicMock(spec=Path)
        config_d.exists.return_value = True

        # Fake config files
        spi_yaml = MagicMock(spec=Path)
        spi_yaml.name = "waveshare-sx1262.yaml"
        usb_yaml = MagicMock(spec=Path)
        usb_yaml.name = "usb-serial.yaml"

        config_d.glob.return_value = [spi_yaml, usb_yaml]
        usb_config = MagicMock(spec=Path)
        usb_config.exists.return_value = True

        h.ctx.dialog._yesno_returns = [False]  # Don't fix

        with patch('handlers.startup_health.Path') as mock_path:
            mock_path.return_value = config_d
            mock_path.__truediv__ = lambda self, other: usb_config

            # Use real Path for /etc paths
            def path_constructor(p):
                if p == '/etc/meshtasticd/config.d':
                    return config_d
                m = MagicMock(spec=Path)
                m.exists.return_value = (p == '/etc/meshtasticd/config.d/usb-serial.yaml')
                return m

            mock_path.side_effect = path_constructor
            config_d.__truediv__ = lambda self, key: usb_config if key == 'usb-serial.yaml' else MagicMock()

            h._check_service_misconfig()

        # Should have shown a yesno about the conflict
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        assert len(yesno_calls) >= 1
        assert "CONFLICTING" in yesno_calls[0][1][1]

    def test_no_spi_configs_no_conflict(self):
        """Only USB config, no SPI — no conflict."""
        h = _make_handler()

        config_d = MagicMock(spec=Path)
        config_d.exists.return_value = True

        usb_yaml = MagicMock(spec=Path)
        usb_yaml.name = "usb-serial.yaml"
        config_d.glob.return_value = [usb_yaml]

        usb_config = MagicMock(spec=Path)
        usb_config.exists.return_value = True

        with patch('handlers.startup_health.Path') as mock_path:
            mock_path.side_effect = lambda p: config_d if p == '/etc/meshtasticd/config.d' else MagicMock(spec=Path)
            config_d.__truediv__ = lambda self, key: usb_config

            h._check_service_misconfig()

        # No yesno about conflicts
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        # May have SPI device check, but no CONFLICTING message
        conflict_calls = [c for c in yesno_calls if "CONFLICTING" in str(c)]
        assert len(conflict_calls) == 0


# ── SPI Mismatch Detection ─────────────────────────────────────────


class TestStartupHealthSPIMismatch:

    @patch('subprocess.run')
    def test_spi_mismatch_offers_fix(self, mock_run):
        """SPI device present but USB config active — offer fix."""
        mock_run.return_value = MagicMock(returncode=0)  # has meshtasticd

        h = _make_handler()

        config_d = MagicMock(spec=Path)
        config_d.exists.return_value = True
        config_d.glob.return_value = []  # No SPI config files

        usb_config = MagicMock(spec=Path)
        usb_config.exists.return_value = True

        spi_dev = MagicMock(spec=Path)
        spi_dev.name = "spidev0.0"

        h.ctx.dialog._yesno_returns = [False]  # Don't fix

        with patch('handlers.startup_health.Path') as mock_path:
            def path_side_effect(p):
                if p == '/etc/meshtasticd/config.d':
                    return config_d
                if p == '/dev':
                    dev_path = MagicMock(spec=Path)
                    dev_path.glob.return_value = [spi_dev]
                    return dev_path
                m = MagicMock(spec=Path)
                m.exists.return_value = True
                return m

            mock_path.side_effect = path_side_effect
            config_d.__truediv__ = lambda self, key: usb_config

            h._check_service_misconfig()

        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        mismatch_calls = [c for c in yesno_calls if "MISMATCH" in str(c)]
        assert len(mismatch_calls) >= 1

    def test_no_spi_devices_skips(self):
        """No SPI devices — skip mismatch check."""
        h = _make_handler()

        config_d = MagicMock(spec=Path)
        config_d.exists.return_value = True
        config_d.glob.return_value = []

        usb_config = MagicMock(spec=Path)
        usb_config.exists.return_value = True

        dev_path = MagicMock(spec=Path)
        dev_path.glob.return_value = []  # No SPI devices

        with patch('handlers.startup_health.Path') as mock_path:
            def path_side_effect(p):
                if p == '/etc/meshtasticd/config.d':
                    return config_d
                if p == '/dev':
                    return dev_path
                m = MagicMock(spec=Path)
                m.exists.return_value = True
                return m

            mock_path.side_effect = path_side_effect
            config_d.__truediv__ = lambda self, key: usb_config

            h._check_service_misconfig()

        # No yesno calls about mismatch
        yesno_calls = [c for c in h.ctx.dialog.calls if c[0] == 'yesno']
        assert len(yesno_calls) == 0
