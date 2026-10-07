"""rns_ingress /fleet cell — pinned on the MeshAnchor side of the twin.

``src/utils/fleet_truth.py`` is byte-identical with MeshForge (parity_check);
the 10-06 port copied the cell without its tests, so this side carried it
unpinned. Core states + the 2026-10-07 housekeeping hint (listed identities
with no validated inbound in 14 d — a hint in the HEALTHY reason, never a
state).
"""
from __future__ import annotations

from utils import fleet_truth as ft

NOW = 1_800_000_000.0


def _box(**ingress):
    slo = {"overall_status": "ready", "rns_ingress": ingress or None}
    base = {"alias": "gw", "resolution_method": "dns", "status": None,
            "slo": slo, "error": None, "answered_at": NOW}
    return ft.build_box_truth(base, now=NOW, signal_classes=["role_drift"])[
        "subsystems"]["rns_ingress"]


def test_no_ledger_is_absent():
    c = _box()
    assert c["state"] == ft.DARK and c.get("absent") is True


def test_declared_and_quiet_is_healthy():
    c = _box(policy="observe", listed=13, unlisted_recent=[],
             updated_at=NOW - 60)
    assert c["state"] == ft.HEALTHY and "13" in c["reason"]


def test_unlisted_sender_fires():
    c = _box(policy="enforce", listed=13, updated_at=NOW - 5,
             unlisted_recent=[{"hash": "627f" + "f" * 28, "label": "627f",
                               "seen": 1, "refused": 1, "last_seen": NOW - 5}])
    assert c["state"] == ft.FAILED and "REFUSED" in c["reason"]


def test_stale_use_is_a_hint_not_an_alarm():
    c = _box(policy="observe", listed=13, unlisted_recent=[],
             updated_at=NOW - 60,
             use={"judgeable": True, "observed_s": 20 * 86400.0,
                  "no_inbound": ["7cda" + "0" * 28, "9217" + "0" * 28]})
    assert c["state"] == ft.HEALTHY
    assert "2 listed with no inbound in 14d" in c["reason"]


def test_unwatched_window_says_too_early():
    c = _box(policy="observe", listed=13, unlisted_recent=[],
             updated_at=NOW - 60,
             use={"judgeable": False, "observed_s": 3 * 86400.0,
                  "no_inbound": None})
    assert "use: too early (3.0d watched of 14d)" in c["reason"]
