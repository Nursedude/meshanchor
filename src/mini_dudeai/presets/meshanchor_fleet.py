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
  * FederationPeerSource → the MA map's /api/status.federation.peer_status →
    kind="federation_peer_unhealthy" (per peer in_backoff / errored). OPT-IN
    (default OFF; MINI_DUDEAI_ENABLE_FEDERATION=1) because it polls the map and
    would source_error every tick on a box without one — enable it ONLY on
    meshanchor-server (which runs the MA map + the mini). Schema CONFIRMED live
    2026-07-22 against a running map (each peer: ok / in_backoff /
    backoff_multiplier / last_error / consecutive_failures / peer_name) — the
    earlier "p['ok'] only" reading was a consumer's partial lens; the full shape
    matches MeshForge's, so its source ports with just the ok-vs-reachable fix.
  * digest is OFF (federator-only artifact, meaningless here).

The fleet ntfy topic is NOT hard-coded in source (MF014). Operator must set
MINI_DUDEAI_NTFY_TOPIC (or pass ntfy_topic= when calling build_engine).
"""
from __future__ import annotations

import json
import os
import socket
from typing import Iterable

from ..actions import (
    FileAnnotateAction, NoopAction, NtfyAction, ProposeEscalationAction,
)
from ..engine import RuleEngine
from ..sources import BootHealthSource
from ..sources.base import Condition, ExtractorSource, Source

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


class FederationPeerSource(Source):
    """One Condition per UN-healthy federation peer, from the MA map's
    /api/status.federation.peer_status (MeshAnchor's map_data_service is a
    code-twin of MeshForge's, so the peer schema is identical — confirmed live
    2026-07-22 against a running map: each peer carries ok / in_backoff /
    backoff_multiplier / last_error / consecutive_failures / peer_name).

    A per-VANTAGE reading: it polls THIS box's OWN map, so it reports how the MA
    server sees its federation peers — box A seeing peer C unhealthy while B sees
    it fine is path evidence no single vantage produces. Healthy peers emit
    nothing. The map unreachable → one source_error (the box is blind to
    federation), NOT silence — absence through a dead channel ≠ all-healthy.

    Ported from MeshForge's FederationPeerSource with the field corrected: the
    live schema's health flag is ``ok`` (MF's copy checked a non-existent
    ``reachable`` and so only ever fired on in_backoff; here ``not ok`` works)."""

    def __init__(self, url: str, timeout: float = 6.0,
                 name: str = "federation") -> None:
        self.url = url
        self.timeout = timeout
        self.name = name

    def collect(self) -> Iterable[Condition]:
        from .._util import fetch_json
        data, err = fetch_json(self.url, timeout=self.timeout)
        if err:
            yield Condition(
                kind="source_error", subject="ma_map",
                detail=f"/api/status unreachable: {err}", source=self.name)
            return
        if not isinstance(data, dict):
            return
        peers = (data.get("federation") or {}).get("peer_status") or []
        for p in peers:
            if not isinstance(p, dict):
                continue
            name = p.get("peer_name") or p.get("hostname") or p.get("name") or "?"
            unhealthy = p.get("in_backoff") or (p.get("last_error")
                                                and not p.get("ok", True))
            if unhealthy:
                yield Condition(
                    kind="federation_peer_unhealthy",
                    subject=str(name),
                    detail=(f"in_backoff={p.get('in_backoff')} "
                            f"mult={p.get('backoff_multiplier')} "
                            f"ok={p.get('ok')} last_err={p.get('last_error')!r}"),
                    source=self.name,
                    extras={
                        "backoff_multiplier": p.get("backoff_multiplier"),
                        "consecutive_failures": p.get("consecutive_failures"),
                    })


def _repo_root() -> str:
    """/opt/meshanchor — four levels up from this file (…/src/mini_dudeai/
    presets/meshanchor_fleet.py). Where configs/ lives."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__),
                                        "..", "..", "..", ".."))


def _rule_ids(path: str):
    """The set of valid rule ids in a rules document, or None if unreadable.
    Reuses the byte-locked candidate validator — no re-implemented parsing."""
    from ..candidate import validate_rules_document
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    valid, _errors = validate_rules_document(doc)
    return {r.get("id") for r in valid if r.get("id")}


class MiniSelfSource(Source):
    """The mini watching its OWN rule-seed integrity, from inside its tick.

    MeshForge routes mini self-observation through its external watchdog
    (watchdog_probes_mini → watchdog.json). MeshAnchor's fleet_watchdog has a
    fixed closed kind set, so — deliberately, to avoid touching it — the MA mini
    self-observes the one thing it can honestly check from within: did this box
    fall behind the role seed? If the repo seed (configs/mini_dudeai_rules.<seed>)
    gained a rule for a new failure class but the live ~/mini_dudeai_rules.json
    was never re-seeded, the box silently misses it. This makes that gap a signal.

    ONE-DIRECTIONAL (live-behind-seed only): extra live-only rules are legitimate
    box-local additions and never fire. Self-guards to SILENCE (no condition, no
    source_error) when either file is unreadable or the seed is absent — a
    missing seed is 'not applicable here', NOT drift and NOT an error
    (honest_failure_modes #2: absence through no-config ≠ a positive signal).
    NOTE this cannot catch total mini-death (the source runs inside the tick);
    that needs an external watcher — a documented WS-A follow-up.
    """

    def __init__(self, rules_path: str, seed_path: str,
                 name: str = "mini_self") -> None:
        self.rules_path = rules_path
        self.seed_path = seed_path
        self.name = name

    def collect(self) -> Iterable[Condition]:
        seed_ids = _rule_ids(self.seed_path)
        if not seed_ids:
            return  # no readable seed → not applicable (silence, not error)
        live_ids = _rule_ids(self.rules_path)
        if live_ids is None:
            return  # live rules unreadable — the engine's own EMPTY-ruleset
            #         warning owns that; don't double-signal here.
        missing = seed_ids - live_ids
        if not missing:
            return
        yield Condition(
            kind="signal_class",
            subject=socket.gethostname(),
            detail=(f"live mini rules behind the {os.path.basename(self.seed_path)} "
                    f"seed — MISSING {len(missing)} rule(s): "
                    f"{', '.join(sorted(missing))}. Re-seed: cp the repo seed to "
                    f"~/mini_dudeai_rules.json (or promote)."),
            source=self.name,
            extras={"class": "rules_seed_drift", "severity": "info",
                    "missing_count": len(missing)},
        )


def build_engine(
    home: str | None = None,
    rules_path: str | None = None,
    state_path: str | None = None,
    history_path: str | None = None,
    brief_path: str | None = None,
    annotate_path: str | None = None,
    db_path: str | None = None,
    seed_path: str | None = None,
    federator_url: str | None = None,
    ntfy_topic: str | None = None,
    enable_blackout: bool | None = None,
    enable_boot_health: bool | None = None,
    enable_self_observe: bool | None = None,
    enable_federation: bool | None = None,
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
    if enable_self_observe is None:
        enable_self_observe = os.environ.get(
            "MINI_DUDEAI_ENABLE_SELF_OBSERVE", "1") != "0"
    # federation defaults OFF (env "1" to enable): it polls the MA map's
    # /api/status, so on a box WITHOUT the map it would emit a source_error every
    # tick (declared-absent-vs-error trap). Enable it ONLY on meshanchor-server
    # (the box that runs the MA map + the mini) via the env file.
    if enable_federation is None:
        enable_federation = os.environ.get(
            "MINI_DUDEAI_ENABLE_FEDERATION", "0") != "0"
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

    # The role seed to check the live rules against (repo canonical). Default is
    # the ma_noc seed; overridable for tests / a differently-seeded box.
    if seed_path is None:
        seed_path = os.path.join(
            _repo_root(), "configs", "mini_dudeai_rules.ma_noc.json")

    # The MA map's status URL — coexist port 5002 during the MF/MA side-by-side
    # (the unit comment notes a planned move back to 5000; env-override when it
    # lands so this preset needs no edit).
    federator_url = federator_url or os.environ.get(
        "MINI_DUDEAI_FEDERATOR_URL", "http://localhost:5002/api/status")

    sources = []
    if enable_blackout:
        sources.append(BlackoutDbSource(db_path=db_path))
    if enable_self_observe:
        sources.append(MiniSelfSource(rules_path=rules_path, seed_path=seed_path))
    if enable_federation:
        sources.append(FederationPeerSource(url=federator_url))
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
