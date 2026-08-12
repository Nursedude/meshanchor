"""Reader/writer artifact-path wiring — the MA side, where it actually diverges.

2026-08-11 (MF frontier-review follow-on): the 07-23 adapter pass carried
unit/repo/preset NAMES through ``_util`` but not PATHS. The byte-locked
``warmstart.py`` and this repo's ``rollup.py`` kept MeshForge-convention
locations baked in (``$HOME/mini_dudeai_*``) while the ``meshanchor_fleet``
preset writes ``~/.local/share/meshanchor/mini/{brief.md,state.json,
history.jsonl}`` — so bare warmstart reported "mini has not run here" beside a
daemon ticking 30 s away (measured live on the replica), and a healthy MA
fleet would have rolled up as ``no_state``.

These tests are the DIVERGENCE pins: on MeshForge the adapter's values
coincide with the old hardcodes, so only this side can catch a re-hardcode.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai import _util  # noqa: E402
from mini_dudeai.presets.meshanchor_fleet import ma_mini_dir  # noqa: E402
from mini_dudeai.rollup import _remote_breadth_cmd, collect_local  # noqa: E402
from mini_dudeai.warmstart import _default_paths  # noqa: E402

NOW = 1_780_000_000.0

_SUBDIR = os.path.join(".local", "share", "meshanchor", "mini")


def test_adapter_paths_are_the_ma_namespace(tmp_path):
    brief, state, history = _util.app_artifact_paths(str(tmp_path))
    assert brief == str(tmp_path / _SUBDIR / "brief.md")
    assert state == str(tmp_path / _SUBDIR / "state.json")
    assert history == str(tmp_path / _SUBDIR / "history.jsonl")


def test_warmstart_defaults_are_the_daemon_writers_paths(tmp_path, monkeypatch):
    """THE bug: bare `python3 -m mini_dudeai.warmstart` read $HOME/
    mini_dudeai_*.{md,json} — paths the meshanchor_fleet preset never writes —
    and answered "mini has not run here" about a ticking daemon."""
    monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
    brief, state = _default_paths()
    a_brief, a_state, _hist = _util.app_artifact_paths()
    assert (brief, state) == (a_brief, a_state)
    assert brief == str(tmp_path / _SUBDIR / "brief.md")
    assert state == str(tmp_path / _SUBDIR / "state.json")


def test_ma_mini_dir_and_adapter_share_one_constant(tmp_path):
    """The preset's ma_mini_dir (and through its test-pinned agreement, the
    fleet_watchdog reader) must be the directory of the adapter's state path —
    one constant, every consumer (honest_failure_modes #5)."""
    home = str(tmp_path)
    _b, state, _h = _util.app_artifact_paths(home)
    assert ma_mini_dir(home) == os.path.dirname(state)


def test_rollup_collect_local_finds_the_ma_state(tmp_path, monkeypatch):
    """Pre-fix, collect_local looked at $HOME/mini_dudeai_state.json and read
    a healthy MA box as having no mini at all."""
    monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
    _b, state_path, _h = _util.app_artifact_paths()
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w") as f:
        json.dump({"last_tick_ts": NOW - 5, "rule_count": 3,
                   "host": "ma-manager"}, f)
    p = collect_local(NOW)
    assert p is not None and p["host"] == "ma-manager"


def test_remote_breadth_cmd_cats_the_ma_relpath_through_a_real_shell(tmp_path):
    """Run the actual remote one-liner with cwd = a fake remote $HOME laid out
    the way the MA daemon writes it. A command only ssh ever executes is a
    command nothing verifies — so execute it. Also pins the claw sibling rule
    (claw ticks live BESIDE the state) surviving the subdir move: an unmatched
    glob must still be skipped, not cat'd literally."""
    home = tmp_path
    mini = home / _SUBDIR
    mini.mkdir(parents=True)
    (mini / "state.json").write_text(json.dumps({"host": "ma-box"}))
    out = subprocess.run(["sh", "-c", _remote_breadth_cmd()], cwd=str(home),
                         capture_output=True, text=True, timeout=15)
    assert out.returncode == 0, out.stderr
    assert '"ma-box"' in out.stdout
