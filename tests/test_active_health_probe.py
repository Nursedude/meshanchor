"""Tests for active_health_probe — focuses on noc.yaml `managed: false` honored
by create_gateway_health_probe(). Without this, MeshCore-only boxes
(--skip-meshtasticd installs) emit UNHEALTHY warnings every 30s for
services they intentionally don't run.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class TestUnmanagedServices:
    """_unmanaged_services() reads /etc/meshanchor/noc.yaml and returns the
    set of service names with `managed: false`."""

    def test_returns_empty_when_yaml_missing(self, tmp_path):
        from utils import active_health_probe as ahp
        with patch.object(ahp, 'Path', return_value=tmp_path / "missing.yaml"):
            assert ahp._unmanaged_services() == set()

    def test_returns_empty_when_yaml_malformed(self, tmp_path):
        from utils import active_health_probe as ahp
        bad = tmp_path / "noc.yaml"
        bad.write_text(":\n  not: [valid")
        with patch.object(ahp, 'Path', return_value=bad):
            assert ahp._unmanaged_services() == set()

    def test_extracts_managed_false_services_nested(self, tmp_path):
        """install_noc.sh emits services nested under top-level `noc:` —
        this is the canonical shape on every fleet box."""
        from utils import active_health_probe as ahp
        cfg = tmp_path / "noc.yaml"
        cfg.write_text(
            "noc:\n"
            "  services:\n"
            "    meshtasticd:\n"
            "      managed: false\n"
            "    rnsd:\n"
            "      managed: true\n"
            "    mosquitto:\n"
            "      managed: false\n"
        )
        with patch.object(ahp, 'Path', return_value=cfg):
            assert ahp._unmanaged_services() == {"meshtasticd", "mosquitto"}

    def test_extracts_managed_false_services_flat(self, tmp_path):
        """Hand-edited flat configs (no `noc:` wrapper) must still work —
        this was the fixture shape used pre-fix."""
        from utils import active_health_probe as ahp
        cfg = tmp_path / "noc.yaml"
        cfg.write_text(
            "services:\n"
            "  meshtasticd:\n"
            "    managed: false\n"
        )
        with patch.object(ahp, 'Path', return_value=cfg):
            assert ahp._unmanaged_services() == {"meshtasticd"}

    def test_default_managed_true_when_unspecified(self, tmp_path):
        """Service entry without explicit `managed:` defaults to managed=True
        — must NOT appear in unmanaged set."""
        from utils import active_health_probe as ahp
        cfg = tmp_path / "noc.yaml"
        cfg.write_text(
            "noc:\n"
            "  services:\n"
            "    meshtasticd:\n"
            "      auto_start: true\n"
        )
        with patch.object(ahp, 'Path', return_value=cfg):
            assert ahp._unmanaged_services() == set()


class TestCreateGatewayHealthProbe:
    """create_gateway_health_probe skips checks for unmanaged services."""

    def test_unmanaged_service_not_registered(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, '_unmanaged_services', return_value={"meshtasticd"}):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert "meshtasticd" not in registered, (
            "managed=false meshtasticd must not be probed — would emit "
            "UNHEALTHY noise on MeshCore-only boxes"
        )
        assert "rnsd" in registered
        assert "mosquitto" in registered

    def test_all_managed_registers_all(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, '_unmanaged_services', return_value=set()):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert {"meshtasticd", "rnsd", "mosquitto"}.issubset(registered)
