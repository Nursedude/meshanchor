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


def test_cli_brief_default_is_the_engines_brief_path():
    """The --brief CLI default must be engine.brief_path — the file the daemon
    writes and warmstart reads. The old sibling-of-state default wrote an
    orphan ``mini_dudeai_brief.md`` beside the MA state that no reader ever
    read (found live 2026-08-11: a manual --brief on the replica created
    exactly that orphan)."""
    import types
    from mini_dudeai.daemon import _brief_out_path
    eng = types.SimpleNamespace(
        brief_path="/h/.local/share/meshanchor/mini/brief.md",
        state_store=types.SimpleNamespace(
            path="/h/.local/share/meshanchor/mini/state.json"),
    )
    assert _brief_out_path(eng, "") == "/h/.local/share/meshanchor/mini/brief.md"
    assert _brief_out_path(eng, "/tmp/x.md") == "/tmp/x.md"
    eng.brief_path = None
    assert _brief_out_path(eng, "") \
        == "/h/.local/share/meshanchor/mini/mini_dudeai_brief.md"


# === peer-app conventions on a shared fleet ======================
# Ported from MeshForge 2026-08-12. There, the pane read THIS repo's replica as
# "no_mini" for 19 days — from our 07-24 move off the home-dir convention —
# while the daemon ticked every 30s. The mirror is LATENT on this side (MA's
# fleet_hosts lists one box), so only a test can hold it shut: these pin the
# behaviour BEFORE a MeshForge box is ever added to that list.

def _peer():
    """The first peer convention from the adapter (app, state_rel, hist_rel)."""
    return _util.PEER_APPS[0]


def test_peer_convention_is_the_meshforge_one():
    app, state_rel, hist_rel = _peer()
    assert app == "meshforge"
    assert state_rel == "mini_dudeai_state.json"
    assert hist_rel == "mini_dudeai_history.jsonl"
    # this app's own convention still leads the order
    assert _util.app_state_candidates()[0][1] == _util.APP_STATE_RELPATH


def test_breadth_cmd_finds_a_meshforge_box_through_a_real_shell(tmp_path):
    """A MeshForge box laid out ITS way must read as a healthy foreign box,
    not as absent. Executed, not asserted on as a string."""
    from mini_dudeai.rollup import _split_claw_payload, _split_src_tag
    _app, state_rel, _h = _peer()
    (tmp_path / state_rel).write_text(
        json.dumps({"last_tick_ts": NOW - 5, "rule_count": 69, "host": "moc1"}))
    out = subprocess.run(["sh", "-c", _remote_breadth_cmd()], cwd=str(tmp_path),
                         capture_output=True, text=True, timeout=15)
    assert out.returncode == 0, out.stderr
    state_text, _claws = _split_claw_payload(out.stdout)
    src, state_text = _split_src_tag(state_text)
    assert src == state_rel
    assert json.loads(state_text)["rule_count"] == 69


def test_breadth_cmd_prefers_this_apps_own_convention(tmp_path):
    """A dual-stack box carries both. Ours wins — this pane is MeshAnchor's."""
    from mini_dudeai.rollup import _split_claw_payload, _split_src_tag
    _app, state_rel, _h = _peer()
    (tmp_path / state_rel).write_text(json.dumps({"rule_count": 69}))
    mini = tmp_path / _SUBDIR
    mini.mkdir(parents=True)
    (mini / "state.json").write_text(json.dumps({"rule_count": 10}))
    out = subprocess.run(["sh", "-c", _remote_breadth_cmd()], cwd=str(tmp_path),
                         capture_output=True, text=True, timeout=15)
    state_text, _claws = _split_claw_payload(out.stdout)
    src, state_text = _split_src_tag(state_text)
    assert src == _util.APP_STATE_RELPATH
    assert json.loads(state_text)["rule_count"] == 10


def test_deep_cmd_pairs_state_and_history_of_the_SAME_app(tmp_path):
    """Never one app's state beside the other's fires."""
    from mini_dudeai.rollup import (_DEEP_SENTINEL, _split_deep_payload,
                                    _state_probe_sh)
    _app, state_rel, hist_rel = _peer()
    (tmp_path / state_rel).write_text(json.dumps({"last_tick_ts": NOW, "rule_count": 69}))
    (tmp_path / hist_rel).write_text(
        json.dumps({"ts": NOW - 5, "transition": "edge_up", "rule_id": "mf_rule",
                    "subject": "moc1", "detail": "d"}) + "\n")
    mini = tmp_path / _SUBDIR
    mini.mkdir(parents=True)
    # a DECOY history at THIS app's path — pairing failure would surface it
    (mini / "history.jsonl").write_text(
        json.dumps({"ts": NOW - 5, "transition": "edge_up", "rule_id": "DECOY",
                    "subject": "x", "detail": "d"}) + "\n")
    out = subprocess.run(["sh", "-c", _state_probe_sh(_DEEP_SENTINEL) + "; true"],
                         cwd=str(tmp_path), capture_output=True, text=True, timeout=15)
    state, history, src = _split_deep_payload(out.stdout)
    assert out.returncode == 0 and src == state_rel
    assert state["rule_count"] == 69
    assert [h["rule_id"] for h in history] == ["mf_rule"]


def test_no_state_verdict_names_every_path_it_tried():
    """A verdict that doesn't say what it looked at is an assertion, not an
    observation — and "no_mini" was a claim about the box, not the files."""
    from mini_dudeai.rollup import build_rollup, collect_remote
    p = collect_remote("host3", NOW, runner=lambda h, t: (0, "   ", ""))
    assert p["status"] == "no_state_file"
    for cand in _util.app_state_candidate_paths():
        assert cand in p["error"]
    assert "no_mini" not in build_rollup([p], NOW)


def test_collect_local_falls_back_to_a_peer_convention(tmp_path, monkeypatch):
    """A dual-stack MANAGER box whose own daemon is MeshForge's must not be
    reported absent by the very pane it renders."""
    _app, state_rel, _h = _peer()
    monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
    (tmp_path / state_rel).write_text(
        json.dumps({"last_tick_ts": NOW - 5, "rule_count": 69, "host": "moc1"}))
    p = collect_local(NOW)
    assert p is not None and p["status"] == "fresh" and p["host"] == "moc1"
    assert p["state_app"] == "meshforge"


def test_collect_local_still_returns_none_on_a_mini_less_box(tmp_path, monkeypatch):
    """Absence stays absence — the fallback must not invent a posture."""
    monkeypatch.setenv("MINI_DUDEAI_HOME", str(tmp_path))
    assert collect_local(NOW) is None
