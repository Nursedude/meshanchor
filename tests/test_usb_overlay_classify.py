"""No shipped radio overlay may be one meshtasticd ignores (B7, 2026-09-30).

meshtasticd 2.7.26 parses 11 top-level keys and `Serial:` is not one
(PortduinoGlue.cpp); MeshAnchor shipped 7 `Serial:`-only "USB" templates.
classify_overlay is MeshForge's core.meshtasticd_templates body, verbatim.
"""
from pathlib import Path

from utils.meshtasticd_overlay import MESHTASTICD_TOP_LEVEL_KEYS, classify_overlay

TEMPLATES = Path(__file__).parent.parent / "templates" / "available.d"


def test_classify_overlay():
    assert classify_overlay("Serial:\n  Device: auto\n") == "ignored"
    for text in ("", "# only a comment\n", "just text", "Lora: [unclosed"):
        assert classify_overlay(text) == "ignored", text
    assert classify_overlay("Lora:\n  Module: sx1262\n  spidev: ch341\n") == "ch341"
    assert classify_overlay("Lora:\n  Module: sx1262\n  CS: 21\n") == "spi"
    assert "Serial" not in MESHTASTICD_TOP_LEVEL_KEYS and "I2C" in MESHTASTICD_TOP_LEVEL_KEYS
    # judged on what survives the sanitizer; exact spidev; aux is not a radio
    assert classify_overlay("Serial:\n  Device: auto\nWebserver:\n  Port: 443\n") == "ignored"
    assert classify_overlay("Lora:\n  spidev: CH341\n") == "spi"
    assert classify_overlay("Display:\n  Panel: ST7789\n") == "aux"


def test_no_shipped_template_is_ignored():
    files = sorted(TEMPLATES.glob("*.yaml"))
    assert files, TEMPLATES
    inert = [f.name for f in files if classify_overlay(f.read_text()) == "ignored"]
    assert inert == [], inert


def test_upstream_ch341_overlays_ship():
    for name in ("lora-usb-meshtoad-e22.yaml", "lora-pinedio-usb-sx1262.yaml"):
        assert classify_overlay((TEMPLATES / name).read_text()) == "ch341", name
