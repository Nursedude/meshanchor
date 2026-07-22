"""MeshAnchor mini-dudeai preset — the MA-native adapter over the byte-locked core.

The engine core is byte-identical to MeshForge (parity_check BYTE_IDENTICAL);
these tests cover ONLY what is MA-specific: the BlackoutDbSource reads MA's
fleet_history SQLite health surface, the seed validates against the shared
rules-document validator, and a full tick over a live temp DB writes state/brief
with NO false source_error.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai import candidate
from mini_dudeai.presets import meshanchor_fleet as maf
from monitoring import fleet_history
from monitoring.fleet_watchdog import ALL_KINDS

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _seed_blackout(db_path, kind, reason):
    fleet_history.record_blackout_started(kind, reason=reason, db_path=db_path)


# ── BlackoutDbSource ────────────────────────────────────────────────────────

def test_blackout_source_projects_active_blackouts_to_signal_class(tmp_path):
    db = tmp_path / "fleet_history.db"
    _seed_blackout(db, "daemon_dead", "meshanchor-daemon inactive")
    _seed_blackout(db, "role_drift", "units diverge from role")
    conds = list(maf.BlackoutDbSource(db_path=db).collect())
    classes = {c.extras.get("class") for c in conds}
    assert classes == {"daemon_dead", "role_drift"}
    for c in conds:
        assert c.kind == "signal_class"           # matches the seed's match.kind
        assert c.subject                          # the box identity, not empty
    daemon = next(c for c in conds if c.extras["class"] == "daemon_dead")
    assert "inactive" in daemon.detail
    assert daemon.extras["severity"] == "error"   # daemon_dead is the box-down tell


def test_blackout_source_empty_db_yields_no_conditions(tmp_path):
    # A fresh box with no active blackouts → zero conditions, NOT a source_error
    # (init_db auto-creates; empty ≠ error).
    conds = list(maf.BlackoutDbSource(db_path=tmp_path / "fresh.db").collect())
    assert conds == []


def test_blackout_source_unreadable_surface_is_source_error(monkeypatch, tmp_path):
    # A real read failure emits ONE source_error, not silent "no blackouts"
    # (honest_failure_modes #2: absence through a broken channel ≠ health).
    def _boom(**kw):
        raise RuntimeError("db locked")
    monkeypatch.setattr(fleet_history, "query_active_blackouts", _boom)
    conds = list(maf.BlackoutDbSource(db_path=tmp_path / "x.db").collect())
    assert len(conds) == 1 and conds[0].kind == "source_error"
    assert "DB read failed" in conds[0].detail


def test_blackout_extractor_skips_malformed_rows():
    rows = [{"kind": "frozen", "reason": "stuck"}, "not-a-dict", {"reason": "x"}]
    out = maf._blackout_extractor(rows)
    # the dict missing "kind" raises KeyError → skipped; the string → skipped.
    assert [d["class"] for d in out] == ["frozen"]


# ── MiniSelfSource (self-observation: rule-seed drift) ──────────────────────

def _write_rules(path, ids):
    path.write_text(json.dumps({"rules": [
        {"id": i, "match": {"kind": "signal_class", "class": i,
                            "subject_glob": "*"},
         "action": {"kind": "propose_escalation", "title": i, "message": "{detail}"}}
        for i in ids]}))


def test_mini_self_source_flags_live_behind_seed(tmp_path):
    seed = tmp_path / "seed.json"; live = tmp_path / "live.json"
    _write_rules(seed, ["a", "b", "c"])
    _write_rules(live, ["a"])                     # missing b, c
    conds = list(maf.MiniSelfSource(str(live), str(seed)).collect())
    assert len(conds) == 1
    c = conds[0]
    assert c.kind == "signal_class" and c.extras["class"] == "rules_seed_drift"
    assert c.extras["missing_count"] == 2
    assert "b" in c.detail and "c" in c.detail


def test_mini_self_source_silent_when_in_sync_or_live_ahead(tmp_path):
    seed = tmp_path / "seed.json"; live = tmp_path / "live.json"
    _write_rules(seed, ["a", "b"])
    _write_rules(live, ["a", "b", "extra"])       # in sync + a box-local extra
    # one-directional: extra live-only rules are legitimate, never fire.
    assert list(maf.MiniSelfSource(str(live), str(seed)).collect()) == []


def test_mini_self_source_silent_when_seed_absent(tmp_path):
    # No readable seed → 'not applicable here', NOT drift and NOT source_error.
    live = tmp_path / "live.json"; _write_rules(live, ["a"])
    assert list(maf.MiniSelfSource(str(live), str(tmp_path / "nope.json"))
                .collect()) == []


def test_mini_self_source_silent_when_live_unreadable(tmp_path):
    # The engine's own EMPTY-ruleset warning owns an unreadable live file; the
    # self-source must not double-signal it.
    seed = tmp_path / "seed.json"; _write_rules(seed, ["a"])
    assert list(maf.MiniSelfSource(str(tmp_path / "nope.json"), str(seed))
                .collect()) == []


def test_repo_seed_is_self_consistent_with_source():
    # The shipped ma_noc seed carries the rules_seed_drift rule that this source's
    # class matches — a box seeded from it reports its own drift (post-bootstrap).
    seed = candidate.seed_rules_path(REPO_ROOT, "ma_noc")
    ids = maf._rule_ids(seed)
    assert "ma_rules_seed_drift_any" in ids


# ── the seed ────────────────────────────────────────────────────────────────

def test_ma_noc_seed_validates_and_covers_every_blackout_kind():
    path = candidate.seed_rules_path(REPO_ROOT, "ma_noc")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    valid, errors = candidate.validate_rules_document(doc)
    assert errors == [], f"seed has invalid rules: {errors}"
    assert len(valid) == len(doc["rules"])
    # every blackout kind fleet_watchdog can emit has a matching rule
    watched = {r["match"].get("class") for r in valid
               if r["match"].get("kind") == "signal_class"}
    # every blackout kind fleet_watchdog can emit MUST have a rule — if MA adds
    # a kind to ALL_KINDS, this fails until the seed covers it (closed-enum gate).
    assert set(ALL_KINDS) <= watched, f"seed misses kinds: {set(ALL_KINDS) - watched}"
    # boot-health + source-blindness are covered too
    kinds = {r["match"]["kind"] for r in valid}
    assert "unexpected_reboot" in kinds and "source_error" in kinds


# ── full tick ───────────────────────────────────────────────────────────────

def test_build_engine_ticks_clean_over_live_db(tmp_path, monkeypatch):
    db = tmp_path / "fleet_history.db"
    _seed_blackout(db, "http_dead", "map not responding")
    home = tmp_path / "home"
    home.mkdir()
    # seed the box's live rules from the repo seed so the tick has something to
    # match (mirrors what promote_seed_rules does on a real box).
    seed = candidate.seed_rules_path(REPO_ROOT, "ma_noc")
    with open(seed, encoding="utf-8") as f:
        (home / "mini_dudeai_rules.json").write_text(f.read())
    monkeypatch.setenv("MINI_DUDEAI_NTFY_TOPIC", "test-topic-not-sent")
    engine = maf.build_engine(home=str(home), db_path=db, enable_boot_health=False)
    state = engine.tick()
    # the health surface read clean — no false source_error, and self-observe
    # sees live==seed → no false drift.
    assert state.get("error_count", 0) == 0
    assert state.get("rule_count") == 8
    # tick() writes state; the run loop writes the brief via _write_brief_safe.
    assert (home / "mini_dudeai_state.json").exists()
    engine._write_brief_safe(state)
    assert (home / "mini_dudeai_brief.md").exists()


def test_preset_requires_ntfy_topic(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_DUDEAI_NTFY_TOPIC", raising=False)
    try:
        maf.build_engine(home=str(tmp_path), db_path=tmp_path / "d.db")
        assert False, "expected ValueError for missing ntfy topic"
    except ValueError as e:
        assert "MINI_DUDEAI_NTFY_TOPIC" in str(e)


# ── deployment wiring (WS-A increment 2) ────────────────────────────────────

def _read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_mini_service_unit_pins_the_ma_preset():
    unit = _read("templates/systemd/meshanchor-mini-dudeai-user.service")
    # MUST pin meshanchor_fleet so the byte-locked daemon's MF-specific
    # `--preset auto` resolution (→ meshforge_fleet, absent on MA) is never hit.
    assert "--preset meshanchor_fleet" in unit
    assert "WorkingDirectory=/opt/meshanchor/src" in unit
    assert "meshforge_fleet" not in unit
    assert "WantedBy=default.target" in unit          # user unit


def test_dream_units_present_and_wired():
    svc = _read("templates/systemd/meshanchor-mini-dudeai-dream-user.service")
    tmr = _read("templates/systemd/meshanchor-mini-dudeai-dream-user.timer")
    assert "--preset meshanchor_fleet --dream" in svc
    assert "Type=oneshot" in svc
    assert "OnCalendar=" in tmr and "Persistent=true" in tmr
    assert "Requires=meshanchor-mini-dudeai-dream.service" in tmr


def test_update_sh_try_restarts_mini_the_deploy_restart_gap():
    # The #79 deploy-restart gap (WS-A): a `git pull` that changes mini code must
    # reach the RUNNING user daemon. update.sh must try-restart it — this pins
    # the regression red-test-first (mirrors MeshForge's TestDeployRestartHook).
    update = _read("scripts/update.sh")
    assert "try-restart meshanchor-mini-dudeai.service" in update
