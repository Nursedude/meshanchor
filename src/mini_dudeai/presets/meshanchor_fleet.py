"""MeshAnchor fleet preset — the MA-native twin of MeshForge's meshforge_fleet.

The mini engine core is byte-identical across the two repos (parity_check.py
BYTE_IDENTICAL tier); only this ADAPTER differs, because the two NOCs expose
health differently:

  * MeshForge writes a JSON blob (/var/lib/meshforge/watchdog.json) that
    meshforge_fleet reads with a JsonFileSource.
  * MeshAnchor's fleet-watchdog writes to SQLite instead — table
    ``blackout_events`` in ``~/.local/share/meshanchor/fleet_history.db``
    (monitoring.fleet_history). The unit of record is a blackout ``kind`` ∈
    {no_data, http_dead, frozen, daemon_dead, role_drift}, not a Signal.

So this preset supplies a ``BlackoutDbSource`` that projects the ACTIVE
blackouts into ``signal_class`` Conditions — the class filter is the blackout
kind, so the MA role seed's ``match.kind: signal_class`` + ``match.class: <kind>``
rules work exactly like MeshForge's. It subclasses the byte-locked
``ExtractorSource`` so the source_error-on-read-failure and extractor-crash
guards come for free (honest_failure_modes: a health surface that can't be read
is source_error, never a silent "no blackouts").

What is wired here (and what is NOT, and why):
  * BlackoutDbSource → the fleet_history DB → kind="signal_class".
  * BootHealthSource → ~/mini_dudeai_clean_exit → kind="unexpected_reboot".
  * NtfyAction / ProposeEscalationAction / FileAnnotateAction / NoopAction.
  * federation + digest are OFF: MeshAnchor's :5000 /api/status shape is not
    yet confirmed to match MeshForge's federation.peer_status, and wiring a
    source against an unconfirmed surface would emit a source_error every tick
    and pin src_errors — the declared-absent-vs-error confusion the whole
    honest-failure-modes discipline exists to stop. Turn them on deliberately
    once the MA endpoint is confirmed (a follow-up increment).

The fleet ntfy topic is NOT hard-coded in source (MF014). Operator must set
MINI_DUDEAI_NTFY_TOPIC (or pass ntfy_topic= when calling build_engine).
"""
from __future__ import annotations

import os
import socket
from typing import Iterable

from ..actions import (
    FileAnnotateAction, NoopAction, NtfyAction, ProposeEscalationAction,
)
from ..engine import RuleEngine
from ..sources import BootHealthSource
from ..sources.base import ExtractorSource

# blackout kind → the condition severity we stamp (informational; the seed rule
# carries the real ntfy priority). daemon_dead is the box-down tell.
_KIND_SEVERITY = {
    "daemon_dead": "error",
    "no_data": "warning",
    "http_dead": "warning",
    "frozen": "warning",
    "role_drift": "info",
}


def _blackout_extractor(rows):
    """Project active blackout_events rows to Condition-ready dicts.

    Each row (query_active_blackouts) has keys id/ts_started/ts_ended/kind/reason.
    The condition kind is 'signal_class' (so seed rules' match.kind: signal_class
    work directly); the blackout `kind` becomes extras["class"] and is matched by
    rule.match.class — the exact same contract meshforge_fleet uses."""
    host = socket.gethostname()
    out = []
    for r in rows or []:
        try:
            kind = r["kind"]
            reason = r["reason"]
        except (KeyError, TypeError, IndexError):
            # sqlite3.Row supports mapping access; a malformed row is skipped,
            # not fused into a bogus condition (honest_failure_modes #1).
            continue
        out.append({
            "subject": host,
            "detail": (reason or f"{kind} active"),
            "class": kind or "unknown",
            "severity": _KIND_SEVERITY.get(kind, "warning"),
        })
    return out


class BlackoutDbSource(ExtractorSource):
    """Read MeshAnchor's fleet_history blackout DB each tick, emit one
    signal_class Condition per ACTIVE blackout.

    Reuses ExtractorSource: _read() returns (rows, None) on success or
    (None, err) on a real read failure — the base then emits a single
    source_error Condition, so a wedged/unreadable health surface pages rather
    than reading as "no blackouts" (absence ≠ healthy). A DB that simply has no
    active blackouts returns ([], None) → zero conditions, which is correct."""

    def __init__(self, db_path: str | None = None,
                 name: str = "blackout_db") -> None:
        self.kind = "signal_class"
        self.extractor = _blackout_extractor
        self.name = name
        self.db_path = db_path

    def _read(self):
        try:
            from monitoring.fleet_history import query_active_blackouts
        except Exception as e:  # noqa: BLE001 — MA monitoring must be importable
            return None, (f"MeshAnchor fleet_history unavailable "
                          f"({type(e).__name__}: {e})")
        try:
            rows = query_active_blackouts(db_path=self.db_path)
        except Exception as e:  # noqa: BLE001 — a real DB read failure is a signal
            return None, f"blackout DB read failed ({type(e).__name__}: {e})"
        return list(rows or []), None


def build_engine(
    home: str | None = None,
    rules_path: str | None = None,
    state_path: str | None = None,
    history_path: str | None = None,
    brief_path: str | None = None,
    annotate_path: str | None = None,
    db_path: str | None = None,
    ntfy_topic: str | None = None,
    enable_blackout: bool | None = None,
    enable_boot_health: bool | None = None,
) -> RuleEngine:
    """Wire the engine the way a MeshAnchor NOC box runs it.

    All paths/topics are overridable for testing; operator-runtime defaults pull
    from env / standard locations.

    enable_blackout: the box's own fleet_history blackout DB feed. Defaults ON —
    it is every MeshAnchor NOC box's local-health feed. Set "0" (env
    MINI_DUDEAI_ENABLE_BLACKOUT) ONLY on a box that runs no MA fleet-watchdog at
    all: there the DB is DECLARED absent, and leaving the source wired would page
    source_error forever (declared-absent ≠ unobservable ≠ error).

    enable_boot_health: unexpected-reboot detection. Defaults ON — a hard reset
    is fleet-relevant everywhere. The SAME clean_exit_path is passed to BOTH the
    source (reader) and the engine (writer-on-graceful-stop) — that pairing is
    the whole mechanism.
    """
    if enable_blackout is None:
        enable_blackout = os.environ.get("MINI_DUDEAI_ENABLE_BLACKOUT", "1") != "0"
    if enable_boot_health is None:
        enable_boot_health = os.environ.get(
            "MINI_DUDEAI_ENABLE_BOOT_HEALTH", "1") != "0"
    from .._util import resolve_home
    home = home or resolve_home()
    rules_path = rules_path or os.path.join(home, "mini_dudeai_rules.json")
    state_path = state_path or os.path.join(home, "mini_dudeai_state.json")
    history_path = history_path or os.path.join(home, "mini_dudeai_history.jsonl")
    # Per-box brief in $HOME (continuity: ssh any box, mini's posture is fresh).
    brief_path = brief_path or os.path.join(home, "mini_dudeai_brief.md")
    annotate_path = annotate_path or os.path.join(
        home, "mini_dudeai_digest_annotations.md")
    ntfy_topic = ntfy_topic or os.environ.get("MINI_DUDEAI_NTFY_TOPIC")
    if not ntfy_topic:
        raise ValueError(
            "meshanchor_fleet preset requires MINI_DUDEAI_NTFY_TOPIC env var or "
            "ntfy_topic= arg. Operator-specific topics live in "
            "~/.config/meshanchor/mini_dudeai.env, loaded by the systemd unit via "
            "EnvironmentFile= (MF014 keeps them out of the repo).")

    sources = []
    if enable_blackout:
        sources.append(BlackoutDbSource(db_path=db_path))
    clean_exit_path = os.path.join(home, "mini_dudeai_clean_exit")
    if enable_boot_health:
        sources.append(BootHealthSource(
            state_path=state_path,
            clean_exit_path=clean_exit_path,
            assessment_path=os.path.join(home, "mini_dudeai_boot_assessment.json"),
            power_log_path=os.path.join(home, "power_history.log"),
            name="boot_health",
        ))
    actions = {
        "ntfy": NtfyAction(topic=ntfy_topic),
        "annotate_digest": FileAnnotateAction(path=annotate_path),
        "propose_escalation": ProposeEscalationAction(),
        "none": NoopAction(),
    }
    return RuleEngine(
        sources=sources,
        actions=actions,
        rules_path=rules_path,
        state_path=state_path,
        history_path=history_path,
        candidate_path=rules_path + ".candidate",
        brief_path=brief_path,
        clean_exit_path=clean_exit_path if enable_boot_health else None,
    )
