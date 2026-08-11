"""Deep-feed active/resolved split — MA's copy of rollup.py is NOT byte-locked.

``src/mini_dudeai/rollup.py`` sits in the diverged tier (MA carries ``_is_self``
and the MESHANCHOR host namespace), so parity_check does not gate it and
MeshForge's suite cannot see a regression here. Per this repo's convention —
byte-locked core is gated upstream, MA tests cover what is MA-specific — the
ported behaviour needs its own pin on this side.

Ported from MeshForge 2026-08-11. The defect: the fleet deep feed had no
active/resolved split at all, so a condition that had CLEARED sat under
"🔎 Fleet escalations (look here first)" carrying its last ACTIVE detail —
a present-tense sentence about a live problem, read by a warm-start session as
an ongoing outage. It is the 2026-06-03 parity_drift defect that the per-box
brief was fixed for and this sibling was not; a fix applied to one instance is
not applied to the class.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai.rollup import build_box_deep, build_deep_feed  # noqa: E402

NOW = 1_780_000_000.0


def _hist_escalation(ts, rule, subject, detail):
    esc = {"rule": rule, "subject": subject, "detail": detail}
    return {"ts": ts, "iso": "2026-08-11T00:00:00", "transition": "edge_down",
            "rule_id": rule, "subject": subject, "detail": detail,
            "outcome": {"extras": {"escalation": esc}}}


def test_cleared_escalation_is_marked_resolved():
    history = [_hist_escalation(NOW - 10, "esc_rule", "ma-box", "peer unhealthy")]
    state = {"last_tick_ts": NOW,
             "rules": {"esc_rule::ma-box": {"rule_id": "esc_rule",
                                            "subject": "ma-box",
                                            "currently_active": False}}}
    rec = build_box_deep("ma-box", state, history, NOW)
    assert rec["escalations"][0]["resolved"] is True


def test_active_and_unknown_pairs_stay_live():
    """Conservative in the same direction as the brief: an unknown (rule,
    subject) pair — renamed rule, pruned state — is never demoted, because
    hiding a live escalation on state drift is the worse failure."""
    history = [_hist_escalation(NOW - 10, "live_rule", "ma-box", "still bad"),
               _hist_escalation(NOW - 11, "ghost_rule", "ma-box", "unknown pair")]
    state = {"last_tick_ts": NOW,
             "rules": {"live_rule::ma-box": {"rule_id": "live_rule",
                                             "subject": "ma-box",
                                             "currently_active": True}}}
    rec = build_box_deep("ma-box", state, history, NOW)
    assert all(e["resolved"] is False for e in rec["escalations"])


def test_known_pair_with_missing_active_flag_stays_live():
    """2026-08-11 MF frontier review, ported with the byte-locked brief.py fix.
    The conservative default only covered a missing PAIR; a pair present in
    state whose ``currently_active`` FIELD was renamed/dropped/nulled by schema
    drift hit ``bool(None) == False`` and flipped to resolved — the hiding
    direction, for every matched escalation at once."""
    history = [_hist_escalation(NOW - 10, "drifted", "ma-box", "flag missing"),
               _hist_escalation(NOW - 11, "nulled", "ma-box", "flag null")]
    state = {"last_tick_ts": NOW,
             "rules": {"drifted::ma-box": {"rule_id": "drifted",
                                           "subject": "ma-box"},
                       "nulled::ma-box": {"rule_id": "nulled",
                                          "subject": "ma-box",
                                          "currently_active": None}}}
    rec = build_box_deep("ma-box", state, history, NOW)
    assert all(e["resolved"] is False for e in rec["escalations"])


def test_deep_feed_moves_resolved_out_of_look_here_first():
    results = [{"host": "ma-box", "status": "fresh", "fires": [], "escalations": [
        {"ts": NOW - 5, "box": "ma-box", "stale": False, "resolved": False,
         "esc": {"rule": "live", "subject": "ma-box", "detail": "rnsd wedged"}},
        {"ts": NOW - 3600, "box": "ma-box", "stale": False, "resolved": True,
         "esc": {"rule": "done", "subject": "ma-box", "detail": "box is DOWN"}},
    ]}]
    out = build_deep_feed(results, NOW)
    head = out[out.index("Fleet escalations"):out.index("✅ Resolved in window")]
    assert "rnsd wedged" in head
    assert "box is DOWN" not in head, \
        "a cleared condition stayed under the fleet-wide 'look here first'"
    tail = out[out.index("✅ Resolved in window"):]
    assert "was (last seen 60m ago)" in tail and "box is DOWN" in tail


def test_records_without_resolved_key_stay_live():
    """Back-compat: records built before the split carry no `resolved` key and
    must not silently vanish from the headline section."""
    results = [{"host": "ma-box", "status": "fresh", "fires": [], "escalations": [
        {"ts": NOW - 5, "box": "ma-box", "stale": False,
         "esc": {"rule": "r", "subject": "s", "detail": "legacy record"}}]}]
    out = build_deep_feed(results, NOW)
    assert "legacy record" in out[:out.index("Recent fleet fires")]
    assert "✅ Resolved in window" not in out
