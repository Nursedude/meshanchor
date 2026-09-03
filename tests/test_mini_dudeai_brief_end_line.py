"""The END line of the warm brief — ported from MeshForge (byte-identical tier).

Operator audit 2026-09-03: the brief measured only the harness; this line puts
the domain's END (a message arriving) above every instrument section. Absent
record → inert; stale/unreadable → UNKNOWN, never a number.
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mini_dudeai.brief import build_brief, end_line, read_delivery_snapshot, write_brief  # noqa: E402

NOW = 1_800_000_000.0


def _state(last_tick=NOW):
    return {"rules": {}, "last_tick_ts": last_tick, "rule_count": 0,
            "error_count": 0, "host": "test-box"}


def _snap(ts, last_event_ts, rate=0.968):
    return {"schema": 1, "host": "gw", "ts": ts,
            "snapshot": {"confirmation_rate": rate, "last_event_ts": last_event_ts,
                         "state_totals": {"queued": 10, "sent": 9, "confirmed": 8,
                                          "dropped": 1}}}


def _read(tmp_path, doc, now=NOW):
    p = tmp_path / "delivery_snapshot.json"
    p.write_text(json.dumps(doc))
    return read_delivery_snapshot(str(p), now)


def test_absent_is_inert_not_a_number():
    line = end_line(None, None, NOW)
    assert line.startswith("🎯 END") and "inert" in line
    assert "confirmation_rate" not in line


def test_fresh_shows_the_mesh(tmp_path):
    snap, err = _read(tmp_path, _snap(NOW - 20, NOW - 61))
    line = end_line(snap, err, NOW)
    assert "confirmation_rate 0.968" in line and "confirmed 8" in line


def test_stale_is_unknown_never_healthy(tmp_path):
    snap, err = _read(tmp_path, _snap(NOW - 3600, NOW - 3700))
    line = end_line(snap, err, NOW)
    assert "UNKNOWN" in line and "stale" in line and "0.968" not in line


def test_corrupt_is_unknown(tmp_path):
    p = tmp_path / "delivery_snapshot.json"
    p.write_text("{not json")
    snap, err = read_delivery_snapshot(str(p), NOW)
    assert "UNKNOWN" in end_line(snap, err, NOW)


def test_renders_first_after_posture():
    out = build_brief(_state(), [], NOW)
    lines = [l for l in out.splitlines() if l.strip()]
    posture = next(i for i, l in enumerate(lines) if l.startswith("🟢"))
    assert lines[posture + 1].startswith("🎯 END")


def test_write_brief_reads_the_snapshot_beside_the_state_home(tmp_path):
    (tmp_path / "mini_dudeai_history.jsonl").write_text("")
    snapdir = tmp_path / ".local" / "share" / "meshforge"
    snapdir.mkdir(parents=True)
    now = time.time()
    (snapdir / "delivery_snapshot.json").write_text(json.dumps(_snap(now - 5, now - 30)))
    text = write_brief(str(tmp_path / "mini_dudeai_state.json"),
                       str(tmp_path / "mini_dudeai_history.jsonl"),
                       str(tmp_path / "brief.md"), state=_state(last_tick=now), now_ts=now)
    assert "confirmation_rate 0.968" in text
