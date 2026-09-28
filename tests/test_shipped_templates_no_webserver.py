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
