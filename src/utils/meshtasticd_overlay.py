"""meshtasticd config.d/ overlays: what they must not carry, and an audit.

A config.d/ overlay OVERRIDES /etc/meshtasticd/config.yaml. A HAT/USB overlay
that carries `Webserver: Port: 443` moves meshtasticd's API off :9443 and every
/api/v1 consumer goes deaf (MeshForge Issue #58 — moc3 2026-05-18; and
meshanchor-server carried this in config.d/usb-serial.yaml, written by our own
setup wizard, for five months until 2026-09-28).

Port of MeshForge `core.meshtasticd_templates` (HAT_OVERLAY_FORBIDDEN_KEYS,
sanitize_hat_overlay) and its Config Doctor `check_meshtasticd_overlay_keys`.
Keep the key set identical to MeshForge's.
"""
from pathlib import Path
from typing import List, Optional, Tuple

# Top-level keys an overlay must not carry: they belong to config.yaml.
HAT_OVERLAY_FORBIDDEN_KEYS = frozenset({
    'Webserver',
    'TCP',
    'Logging',
    'MQTT',
    'Bluetooth',
    'General',
})

MESHTASTICD_API_PORT = 9443
CONFIG_D = Path('/etc/meshtasticd/config.d')


# Top-level keys meshtasticd itself reads — measured, not recalled:
#   grep -oE 'yamlConfig\["[A-Za-z0-9_]+"\]' src/platform/portduino/PortduinoGlue.cpp | sort -u
# on v2.7.26.54e0d8d gives these 12. A key outside this set is silently
# ignored — a `Serial:`-only overlay configures nothing (verified with
# `meshtasticd --output-yaml`: merged config identical to no overlay).
MESHTASTICD_TOP_LEVEL_KEYS = frozenset({
    'Config', 'Display', 'General', 'GPIO', 'GPS', 'HostMetrics', 'I2C',
    'Input', 'Logging', 'Lora', 'Touchscreen', 'Webserver',
})


def classify_overlay(content: str) -> str:
    """What a meshtasticd overlay IS, from its content, never its filename.

    Judged on what survives activation: the keys HAT_OVERLAY_FORBIDDEN_KEYS
    strips (Webserver, Logging, General, …) are removed first, so a
    `Serial:` + `Webserver:` file is 'ignored', not a radio.
      'ch341'   — `Lora:` with `spidev: ch341` (exact, as the firmware
                  compares): a USB-SPI board (MeshToad, MeshStick, …).
      'spi'     — any other `Lora:` overlay (a HAT on the Pi's SPI bus).
      'aux'     — no `Lora:`, but keys meshtasticd reads (Display, GPS,
                  I2C, …): a real overlay, NOT a radio config — activating
                  it as one would replace the radio's overlay.
      'ignored' — nothing meshtasticd reads survives (e.g. `Serial:` only),
                  or not YAML: activating it changes nothing.
    Only 'ch341'/'spi' belong in a radio menu. First YAML document only, as
    yaml-cpp's LoadFile reads. Twins: MeshForge core/meshtasticd_templates.py,
    MeshAnchor utils/meshtasticd_overlay.py — same body.
    """
    import yaml
    try:
        doc = next(yaml.safe_load_all(content), None)
    except yaml.YAMLError:
        return 'ignored'
    if not isinstance(doc, dict):
        return 'ignored'
    keys = (set(doc) - HAT_OVERLAY_FORBIDDEN_KEYS) & MESHTASTICD_TOP_LEVEL_KEYS
    lora = doc.get('Lora')
    if 'Lora' in keys and isinstance(lora, dict):
        return 'ch341' if lora.get('spidev') == 'ch341' else 'spi'
    return 'aux' if keys - {'Lora'} else 'ignored'


def sanitize_hat_overlay(content: str) -> Tuple[str, List[str]]:
    """Strip forbidden top-level blocks from an overlay before activation.

    Returns ``(text, stripped_keys)``. Unparseable or non-mapping input is
    returned unchanged with no keys stripped — meshtasticd's own load will
    surface the parse error rather than us silently mangling the file.
    """
    import yaml
    try:
        loaded = yaml.safe_load(content)
    except yaml.YAMLError:
        return content, []
    if not isinstance(loaded, dict):
        return content, []
    stripped = sorted(k for k in loaded if k in HAT_OVERLAY_FORBIDDEN_KEYS)
    if not stripped:
        return content, []
    for key in stripped:
        del loaded[key]
    return yaml.safe_dump(loaded, sort_keys=False, default_flow_style=False), stripped


def install_overlay(src: Path, dst: Path) -> List[str]:
    """Write ``src`` into config.d/ as ``dst`` — sanitized, never a raw copy.

    Returns the stripped top-level keys (empty when the template was clean).
    """
    content, stripped = sanitize_hat_overlay(src.read_text())
    dst.write_text(content)
    return stripped


def audit_overlays(config_d: Optional[Path] = None) -> Tuple[str, List[str]]:
    """Read-only audit of the ACTIVE overlays.

    Returns ``(status, lines)`` where status is one of:
      ``absent``      config.d/ does not exist (meshtasticd not installed)
      ``ok``          every overlay parsed and carries no forbidden key
      ``port_moved``  an overlay sets Webserver: Port to something not 9443
      ``overrides``   an overlay carries another forbidden key
      ``unreadable``  an overlay could not be read/parsed — NOT clean
    Worst status wins; ``lines`` name each file and what it carries.
    """
    import yaml
    config_d = CONFIG_D if config_d is None else config_d
    if not config_d.is_dir():
        return 'absent', []
    try:
        files = sorted(p for p in config_d.iterdir()
                       if p.suffix in ('.yaml', '.yml') and p.is_file())
    except OSError as exc:
        return 'unreadable', [f"cannot list {config_d}: {exc}"]

    port_moved, overrides, unreadable = [], [], []
    for path in files:
        try:
            loaded = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as exc:
            unreadable.append(f"{path.name}: unreadable ({type(exc).__name__})")
            continue
        if not isinstance(loaded, dict):
            continue
        web = loaded.get('Webserver')
        port = web.get('Port') if isinstance(web, dict) else None
        if port is not None and port != MESHTASTICD_API_PORT:
            port_moved.append(f"{path.name}: Webserver: Port: {port} "
                              f"(API moves off :{MESHTASTICD_API_PORT})")
        keys = sorted(k for k in loaded if k in HAT_OVERLAY_FORBIDDEN_KEYS)
        if keys:
            overrides.append(f"{path.name}: overrides config.yaml ({', '.join(keys)})")

    lines = port_moved + overrides + unreadable
    if port_moved:
        return 'port_moved', lines
    if unreadable:
        return 'unreadable', lines
    if overrides:
        return 'overrides', lines
    return 'ok', lines
