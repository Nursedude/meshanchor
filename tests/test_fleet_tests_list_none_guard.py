"""Re-review guard for the 2026-07-06 B-F9 fix.

B-F9 made `_list_timers_scope` return None on a FAILED probe (was []). The
sibling consumer `_schedules_block` was guarded, but `_serve_fleet_tests_list`
did `for raw in _list_timers_scope(scope)` unguarded → `for raw in None` →
TypeError → 500 on /fleet/tests, specifically on the root-daemon host the fix
targets. This pins the guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils.map_http_handler import MapRequestHandler  # noqa: E402


def test_serve_fleet_tests_list_survives_none_timer_scope(monkeypatch):
    import monitoring.fleet_aggregator as fa
    # Both scopes report a FAILED probe (None), like a root-daemon host with no
    # resolvable operator. (_serve_fleet_tests_list imports the fn from here.)
    monkeypatch.setattr(fa, "_list_timers_scope", lambda scope: None)

    h = MapRequestHandler.__new__(MapRequestHandler)
    captured = {}
    h._serve_json = lambda payload, status=200: captured.update(
        payload=payload, status=status)

    h._serve_fleet_tests_list()  # must NOT raise TypeError

    assert captured.get("status", 200) == 200
    assert "tests" in captured["payload"]
