"""Shipped HAT templates must not carry a `Webserver:` block (TUI audit
finding 10, 2026-09-28; MeshForge Issue #58 / moc3 2026-05-18).

A config.d/ overlay's Webserver wins over config.yaml, so `Port: 443` in a
template moves meshtasticd's API off :9443. MeshAnchor's activation path has
NO overlay sanitizer — the template is copied into config.d/ as-is — so the
shipped files are the only guard. All 33 carried the block; measured the same
day, meshanchor-server's config.d/usb-serial.yaml still carries it (inert only
because meshtasticd is not installed there).
"""
from pathlib import Path

import yaml

TEMPLATES = Path(__file__).parent.parent / "templates" / "available.d"


def test_available_d_templates_carry_no_webserver():
    files = sorted(TEMPLATES.glob("*.yaml"))
    assert files, "no templates found — the glob is aimed wrong"
    carrying = [f.name for f in files
                if "Webserver" in (yaml.safe_load(f.read_text()) or {})]
    assert carrying == []


# Keys a config.d/ overlay must never carry: they override config.yaml.
# Mirrors MeshForge core.meshtasticd_templates.HAT_OVERLAY_FORBIDDEN_KEYS
# (MeshAnchor has no sanitizer, so there is no in-repo constant to share).
FORBIDDEN = {"Webserver", "TCP", "Logging", "MQTT", "Bluetooth", "General"}
EXAMPLES = Path(__file__).parent.parent / "examples" / "configs"


def test_wizard_usb_overlay_carries_only_serial():
    # meshanchor-server's config.d/usb-serial.yaml came from this wizard with
    # `Webserver: Port: 443` (found by Config Doctor, stripped 2026-09-28).
    from handlers.first_run import usb_overlay_content
    loaded = yaml.safe_load(usb_overlay_content("/dev/ttyUSB0"))
    assert loaded == {"Serial": {"Device": "/dev/ttyUSB0"}}


def test_overlay_examples_carry_no_forbidden_keys():
    # Both examples say "Copy to: /etc/meshtasticd/config.d/".
    files = sorted(EXAMPLES.glob("meshtasticd-*.yaml"))
    assert len(files) == 2, [f.name for f in files]
    carrying = {f.name: sorted(FORBIDDEN & set(yaml.safe_load(f.read_text()) or {}))
                for f in files}
    assert all(not keys for keys in carrying.values()), carrying


def test_available_d_templates_carry_no_forbidden_keys():
    # MeshAnchor copies these into config.d/ as-is (no sanitizer), so TCP /
    # Logging here silently override config.yaml. All 34 carried both until
    # 2026-09-28; config.yaml already declares Logging, 4403 is the TCP default.
    files = sorted(TEMPLATES.glob("*.yaml"))
    assert files, "no templates found — the glob is aimed wrong"
    carrying = {f.name: sorted(FORBIDDEN & set(yaml.safe_load(f.read_text()) or {}))
                for f in files}
    assert {k: v for k, v in carrying.items() if v} == {}
