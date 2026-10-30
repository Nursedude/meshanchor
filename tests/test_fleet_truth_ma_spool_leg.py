"""MeshAnchor's collector reads the NOC read-grant spool when the direct
fetch fails (session 3 step 3, 2026-10-15) — the real _fetch_box against a
real closed port, the real shared reader against a planted spool dir."""
import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import box_truth_center as bc  # noqa: E402
from utils import fleet_truth_collector as ftc  # noqa: E402


def _closed_base():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    d = bc.truth_spool_dir()
    d.mkdir(parents=True)
    monkeypatch.setattr(ftc, "PEER_TIMEOUT_S", 0.5)

    def write(name, **doc):
        doc.setdefault("alias", name)           # the real writer always sets it
        doc.setdefault("schema", bc.SPOOL_SCHEMA)
        doc.setdefault("fetched_at", time.time())
        (d / f"{name}.json").write_text(json.dumps(doc))
    return write


def test_a_fresh_spool_answers_for_a_dark_peer(spool):
    spool("moc3", status={"app": {"name": "meshforge"}}, slo=None,
          http_surface_expected=False, watchdog_expected=True)
    snap = ftc._fetch_box("Gateway 3", _closed_base(), "config", "moc3")
    assert snap["resolution_method"] == "ssh_spool" and snap["error"] is None
    assert snap["status"] == {"app": {"name": "meshforge"}}
    assert snap["http_surface_expected"] is False and snap["watchdog_expected"] is True


def test_an_empty_handed_spool_names_how_the_read_ended(spool):
    spool("moc3", transport={"outcome": "refused_grant",
                             "reason": "grant missing or refused — box_truth_grant.py push moc3"})
    snap = ftc._fetch_box("moc3", _closed_base(), "config", "moc3")
    assert snap["status"] is None and "push moc3" in snap["error"]
    assert "http_surface_expected" not in snap        # undecided stays absent


def test_a_stale_spool_reads_dark_never_last_known_healthy(spool):
    spool("moc3", status={"x": 1}, fetched_at=time.time() - bc.SPOOL_STALE_S - 5)
    snap = ftc._fetch_box("moc3", _closed_base(), "config", "moc3")
    assert snap["status"] is None and snap["resolution_method"] == "config"
    assert snap["error"].startswith("no response from moc3")


def test_self_never_reads_a_spool(spool):
    spool("me", status={"x": 1})
    snap = ftc._fetch_box("me", _closed_base(), "self", "me")
    assert snap["status"] is None and snap["resolution_method"] == "self"


# ── the deferred move (2026-10-15): blocks from a map-less box's raw state ──

def test_a_map_less_peer_gets_its_blocks_from_raw_state(spool):
    now = time.time()
    spool("moc3",
          raw_watchdog={"ts": now - 10, "ok": True, "probe_count": 40, "signals": []},
          raw_mini={"last_tick_ts": now - 5, "rules": {}, "rule_count": 72},
          radio_probe={"tcp_listening": True, "usb_present": False},
          deployment={"role": "gateway-only"}, box_truth={"app": "meshforge"})
    snap = ftc._fetch_box("moc3", _closed_base(), "config", "moc3")
    assert snap["resolution_method"] == "ssh_spool" and snap["error"] is None
    assert snap["status"]["watchdog"]["ok"] is True
    assert snap["status"]["mini_dudeai"]["ok"] is True
    assert snap["status"]["app"] == {"name": "meshforge", "role": "gateway-only"}
    assert snap["slo"] == {"radio": {"connected": True, "mode": "tcp"}}


def test_a_stale_raw_watchdog_reads_not_ok(spool):
    spool("moc3", raw_watchdog={"ts": time.time() - 3600, "ok": True})
    snap = ftc._fetch_box("moc3", _closed_base(), "config", "moc3")
    assert snap["status"]["watchdog"]["ok"] is False
    assert snap["status"]["watchdog"]["reason"].startswith("stale:")


def test_mini_state_without_a_tick_timestamp_is_not_ok():
    # MA's private copy had drifted: no last_tick_ts read ok=True forever
    from utils._map_status_endpoints import mini_block_from_payload
    assert mini_block_from_payload({"rules": {}})["ok"] is False


# ── step-8 non-author pass (2026-10-15, F1/F5) ───────────────────────────

def test_a_spool_doc_for_another_alias_is_refused(spool):
    spool("moc3", alias="moc3", status={"app": {"name": "meshforge"}})
    # kiai's fleet.json entry typo'd ssh.alias="moc3": moc3's truth must not be kiai's
    snap = ftc._fetch_box("kiai", _closed_base(), "config", "moc3", duplicate=True)
    assert snap["status"] is None and "more than once" in snap["error"]


def test_the_spool_doc_must_name_the_alias_asked_for(spool):
    spool("moc3", alias="kiai", status={"x": 1})
    snap = ftc._fetch_box("moc3", _closed_base(), "config", "moc3")
    # refused in the SHARED read_spool (one rule for both apps): reads dark
    assert snap["status"] is None and snap["error"].startswith("no response from moc3")


def test_an_invalid_spool_alias_is_never_read(spool):
    snap = ftc._fetch_box("x", _closed_base(), "config", "../../x")
    assert snap["status"] is None and "not a valid alias" in snap["error"]


def test_collect_snapshots_wires_the_duplicate_flag(spool, tmp_path, monkeypatch):
    # delta read 2026-10-15: only _fetch_box(duplicate=True) was tested; a
    # dropped flag in collect_snapshots would still have passed
    import json as _json
    fleet = tmp_path / "fleet.json"
    base = _closed_base().rsplit(":", 1)
    # TEST-NET-1 (unroutable): 127.0.0.1 peers are dropped as SELF, which made
    # the first version of this test vacuous (all() over zero peers)
    fleet.write_text(_json.dumps({"peers": [
        {"name": "moc3", "host": "192.0.2.1", "port": 9},
        {"name": "kiai", "host": "192.0.2.2", "port": 9, "ssh": {"alias": "moc3"}}]}))
    monkeypatch.setenv("MESHANCHOR_FLEET_CONFIG", str(fleet))
    spool("moc3", status={"app": {"name": "meshforge"}})
    snaps, _ = ftc.collect_snapshots(self_port=int(base[1]))
    peers = {s["alias"]: s for s in snaps if s["resolution_method"] != "self"}
    assert set(peers) == {"moc3", "kiai"}, peers
    assert all(s["status"] is None and "more than once" in (s["error"] or "")
               for s in peers.values()), peers
