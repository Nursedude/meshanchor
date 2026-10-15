"""MeshAnchor's identity source for NOC read grants (session 3 step 3,
2026-10-15): a target's pinned host key comes from the operator's
``fleet.json`` peer ``ssh`` field — read-only; registration stays in the
shared targets file. A malformed fingerprint is NO pin (the read is then
refused ``identity_unpinned``), never trust-on-first-use."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import box_truth_app as app  # noqa: E402

FP = "SHA256:" + "A" * 43


def _fleet(tmp_path, monkeypatch, doc):
    p = tmp_path / "fleet.json"
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    monkeypatch.setenv("MESHANCHOR_FLEET_CONFIG", str(p))
    return p


def test_the_pin_comes_from_the_ssh_field(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, {"peers": [
        {"name": "moc3", "host": "moc3.local", "ssh": {"expect_hostkey": FP}}]})
    assert app.expect_hostkey_of("moc3") == (FP, None)


def test_ssh_alias_overrides_the_display_name(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, {"peers": [
        {"name": "Gateway 3", "host": "10.0.0.3",
         "ssh": {"alias": "moc3", "expect_hostkey": FP}}]})
    assert app.expect_hostkey_of("moc3") == (FP, None)
    assert app.expect_hostkey_of("Gateway 3") == (None, None)


def test_a_malformed_fingerprint_is_no_pin(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, {"peers": [
        {"name": "moc3", "host": "h", "ssh": {"expect_hostkey": "SHA256:short"}}]})
    assert app.expect_hostkey_of("moc3") == (None, None)


def test_an_unparseable_fleet_json_is_named(tmp_path, monkeypatch):
    p = _fleet(tmp_path, monkeypatch, "{not json")
    expect, why = app.expect_hostkey_of("moc3")
    assert expect is None and str(p) in why


def test_a_peer_without_ssh_has_no_pin(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, {"peers": [{"name": "moc3", "host": "h"}]})
    assert app.expect_hostkey_of("moc3") == (None, None)
