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
    # the health surface read clean — no false source_error
    assert state.get("error_count", 0) == 0
    assert state.get("rule_count") == 7
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
