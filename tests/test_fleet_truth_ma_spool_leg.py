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

    def write(alias, **doc):
        doc.setdefault("schema", bc.SPOOL_SCHEMA)
        doc.setdefault("fetched_at", time.time())
        (d / f"{alias}.json").write_text(json.dumps(doc))
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
