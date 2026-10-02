"""
The removed RNS-over-Meshtastic transport, as config: read never, written never.

Port of MeshForge (2026-10-02). The transport was removed 2026-10-01 but its
dataclass stayed and ``GatewayConfig.save`` re-serialised all 11 dead keys
into every gateway.json on every save, and two shipped templates carried the
block. MA is mode-based: ``bridge_mode: rns_transport`` is refused at startup
(tests/test_bridge_cli_rns_transport_removed.py) and the section's ``enabled``
was never read, so here an old section is simply ignored and dropped.

Run: python3 -m pytest tests/test_gateway_transport.py -v
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.gateway.config import GatewayConfig

OLD_SECTION = {"enabled": False, "connection_type": "tcp", "device_path": "localhost:4403",
               "data_speed": 8, "hop_limit": 3}


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "gateway.json"
    with patch.object(GatewayConfig, "get_config_path", classmethod(lambda cls: path)):
        yield path


def test_old_file_with_the_section_still_loads(cfg_file):
    cfg_file.write_text(json.dumps({"enabled": True, "rns_transport": OLD_SECTION}))
    assert GatewayConfig.load().load_error is None


def test_save_drops_the_dead_section(cfg_file):
    cfg_file.write_text(json.dumps({"enabled": True, "rns_transport": OLD_SECTION}))
    assert GatewayConfig.load().save() is True
    assert "rns_transport" not in json.loads(cfg_file.read_text())


def test_fresh_save_never_writes_the_section(cfg_file):
    assert GatewayConfig().save() is True
    assert "rns_transport" not in json.loads(cfg_file.read_text())


def test_no_shipped_template_carries_the_section():
    shipped = list((Path(__file__).resolve().parent.parent / "src" / "gateway"
                    / "templates").glob("*.json"))
    assert shipped
    for p in shipped:
        assert '"rns_transport"' not in p.read_text(), p
