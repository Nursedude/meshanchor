"""MeshCore desired-state push, drift detection, and config doctor.

Session 3 of the MeshCore high-integration charter
(`.claude/plans/meshcore_high_integration_charter.md`). This module owns
the *operator-visible* MeshCore configuration:

* **What we own**: region (pulled from ``gateway.json[meshcore]``), the
  selected preset (LoRa freq/bw/sf/cr tuple), TX power, channel slots.
* **What we don't own**: firmware version (info-only — flash flow is a
  separate follow-up), the radio's PHY/MAC internals, BLE settings.

Three orthogonal surfaces:

1. ``apply_desired_config(handler, desired)`` — push the operator's
   desired values to the live radio and verify via a re-read. Used at
   connect time and from the TUI's preset-switch flow.
2. ``check_drift(...)`` — compare actual radio state against last-seen
   cached state, logging a WARNING + fix hint when they diverge.
3. ``meshcore_config_doctor()`` — paralleling MeshForge's Config Doctor,
   returns a list of structured issues for the TUI / CLI to render.

The "last-seen" cache lives at ``~/.config/meshanchor/meshcore_state.json``
so drift is detectable across daemon restarts. The cache is informational:
losing it (or seeing a stale one) never blocks the connect.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# ─── What MeshAnchor owns ───────────────────────────────────────────
#
# Preset table — keyed by (region, preset_name) → (freq_mhz, bw_khz, sf, cr).
# Names follow MeshCore firmware convention (lowercase snake_case). Operators
# pick a (region, preset) pair in gateway.json; apply_desired_config maps that
# to the four numbers and pushes them via MeshCoreRadioConfig.set_lora.
#
# Adding a row here is the canonical way to teach MeshAnchor about a new
# preset — TUI status, doctor, and drift detection all consume this table.

PRESETS: Dict[Tuple[str, str], Tuple[float, float, int, int]] = {
    # US 915 MHz ISM band (FCC Part 15.247)
    ("US915", "default_lf"):  (915.000, 250.0, 11, 5),
    ("US915", "medium_fast"): (915.000, 250.0, 10, 5),
    # EU 868 MHz SRD band (ETSI EN 300 220)
    ("EU868", "default_lf"):  (869.525, 250.0, 11, 5),
    # EU 433 MHz SRD band
    ("EU433", "default_lf"):  (433.000, 250.0, 11, 5),
    # KR 920 MHz (KCC)
    ("KR920", "default_lf"):  (922.100, 250.0, 11, 5),
}


def lookup_preset(region: str, preset: str) -> Optional[Tuple[float, float, int, int]]:
    """Return (freq, bw, sf, cr) for ``(region, preset)``, or None if unknown."""
    return PRESETS.get(((region or "").upper(), (preset or "").lower()))


def known_regions() -> List[str]:
    """Sorted list of regions with at least one defined preset."""
    return sorted({region for region, _ in PRESETS.keys()})


def known_presets(region: str) -> List[str]:
    """Sorted preset names for ``region``."""
    region_upper = (region or "").upper()
    return sorted({p for r, p in PRESETS.keys() if r == region_upper})


def preset_name_for(
    freq: Optional[float],
    bw: Optional[float],
    sf: Optional[int],
    cr: Optional[int],
) -> Optional[Tuple[str, str]]:
    """Reverse-lookup: given live LoRa params, return ``(region, preset)`` if
    one matches exactly, else None. Tolerates None inputs (returns None)."""
    if None in (freq, bw, sf, cr):
        return None
    try:
        key = (round(float(freq), 3), round(float(bw), 1), int(sf), int(cr))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    for (region, preset), value in PRESETS.items():
        if (round(value[0], 3), round(value[1], 1), value[2], value[3]) == key:
            return (region, preset)
    return None


# ─── Desired-config view of gateway.json[meshcore] ──────────────────


@dataclass
class DesiredChannel:
    """Operator-specified channel slot — pushed to the radio at connect."""
    idx: int
    name: str
    secret: Optional[str] = None  # 32-hex-char secret; None ⇒ auto-derive from #-name


@dataclass
class DesiredConfig:
    """Operator's intent for the MeshCore radio.

    Anything left as ``None`` / empty means "don't push". This lets new
    fields land without forcing existing operators to set every value.
    """
    region: str = ""
    preset: str = ""
    freq_mhz: Optional[float] = None
    bw_khz: Optional[float] = None
    sf: Optional[int] = None
    cr: Optional[int] = None
    tx_power_dbm: Optional[int] = None
    channels: List[DesiredChannel] = field(default_factory=list)

    @classmethod
    def from_gateway_config(cls, mc_config: Any) -> "DesiredConfig":
        """Pull desired values out of a ``MeshCoreConfig`` dataclass."""
        region = (getattr(mc_config, "region", "") or "").upper()
        preset = (getattr(mc_config, "preset", "") or "").lower()
        freq = getattr(mc_config, "desired_freq_mhz", None)
        bw = getattr(mc_config, "desired_bw_khz", None)
        sf = getattr(mc_config, "desired_sf", None)
        cr = getattr(mc_config, "desired_cr", None)
        # If region+preset are set and explicit overrides are blank, expand
        # the preset to its (freq, bw, sf, cr) so apply_desired sees one set
        # of values rather than two layers to merge.
        if region and preset and None in (freq, bw, sf, cr):
            mapped = PRESETS.get((region, preset))
            if mapped is not None:
                freq = freq if freq is not None else mapped[0]
                bw = bw if bw is not None else mapped[1]
                sf = sf if sf is not None else mapped[2]
                cr = cr if cr is not None else mapped[3]
            else:
                logger.warning(
                    "DesiredConfig: region=%s preset=%s not in PRESETS — "
                    "ignoring preset, falling back to explicit desired_* "
                    "fields if any", region, preset,
                )
        tx_power = getattr(mc_config, "desired_tx_power_dbm", None)
        raw_channels = getattr(mc_config, "desired_channels", None) or []
        channels: List[DesiredChannel] = []
        for entry in raw_channels:
            try:
                channels.append(DesiredChannel(
                    idx=int(entry["idx"]),
                    name=str(entry["name"]),
                    secret=entry.get("secret"),
                ))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("DesiredConfig: skipping bad channel entry %r: %s",
                               entry, e)
        return cls(
            region=region,
            preset=preset,
            freq_mhz=_to_float(freq),
            bw_khz=_to_float(bw),
            sf=_to_int(sf),
            cr=_to_int(cr),
            tx_power_dbm=_to_int(tx_power),
            channels=channels,
        )

    def has_lora(self) -> bool:
        return None not in (self.freq_mhz, self.bw_khz, self.sf, self.cr)

    def is_empty(self) -> bool:
        return (
            not self.has_lora()
            and self.tx_power_dbm is None
            and not self.channels
        )


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ─── State cache (~/.config/meshanchor/meshcore_state.json) ─────────


def _state_cache_path(config_dir: Optional[Path] = None) -> Path:
    base = config_dir if config_dir is not None else (
        get_real_user_home() / ".config" / "meshanchor"
    )
    return Path(base) / "meshcore_state.json"


def cache_radio_state(
    state: Dict[str, Any],
    *,
    config_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Persist the last-seen radio state for cross-restart drift detection.

    Skips silently on permission errors / disk-full — the cache is purely
    informational.  Returns the path written on success, None on failure.
    """
    target = _state_cache_path(config_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "saved_ts": time.time(),
            "state": _redact_state(state),
        }
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, indent=2, default=str))
        os.replace(tmp, target)
        return target
    except OSError as e:
        logger.debug("Could not cache radio state to %s: %s", target, e)
        return None


def load_cached_radio_state(
    *,
    config_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return the last-saved radio state, or None if absent / unparseable."""
    target = _state_cache_path(config_dir)
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (OSError, ValueError) as e:
        logger.debug("Could not load cached radio state from %s: %s", target, e)
        return None


def _redact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Drop anything sensitive before persisting. Channel hashes are fine
    (they're already 2-char hashes); secrets never live in the state cache
    because get_radio_state never exposes them."""
    return {k: v for k, v in (state or {}).items() if k != "secret"}


# ─── Drift detection ────────────────────────────────────────────────


@dataclass
class DriftReport:
    """One field that diverges from expected. Used by the doctor + connect
    path to log structured warnings."""
    field: str
    expected: Any
    actual: Any
    fix_hint: str
    severity: str = "warning"  # "info" | "warning" | "error"


def check_drift(
    actual: Dict[str, Any],
    *,
    desired: Optional[DesiredConfig] = None,
    cached: Optional[Dict[str, Any]] = None,
) -> List[DriftReport]:
    """Compare ``actual`` against ``desired`` and ``cached`` snapshots.

    Returns one DriftReport per diverging field. The TUI / doctor caller
    decides how to render them; the bridge's connect path logs each at
    WARNING level.
    """
    reports: List[DriftReport] = []

    if desired is not None and desired.has_lora():
        for field_name, want in (
            ("radio_freq_mhz", desired.freq_mhz),
            ("radio_bw_khz", desired.bw_khz),
            ("radio_sf", desired.sf),
            ("radio_cr", desired.cr),
        ):
            got = actual.get(field_name)
            if got is None:
                continue
            if not _close_enough(got, want, field_name):
                reports.append(DriftReport(
                    field=field_name,
                    expected=want,
                    actual=got,
                    fix_hint=(
                        f"Run 'apply_desired_config' or set in TUI: "
                        f"MeshCore → Radio Config → Set LoRa Params"
                    ),
                ))

    if desired is not None and desired.tx_power_dbm is not None:
        got = actual.get("tx_power_dbm")
        if got is not None and got != desired.tx_power_dbm:
            reports.append(DriftReport(
                field="tx_power_dbm",
                expected=desired.tx_power_dbm,
                actual=got,
                fix_hint=(
                    "TUI: MeshCore → Radio Config → Set TX Power, or update "
                    "gateway.json[meshcore].desired_tx_power_dbm"
                ),
            ))

    if cached is not None:
        cached_state = (cached or {}).get("state") or {}
        for field_name in ("radio_freq_mhz", "radio_bw_khz", "radio_sf",
                           "radio_cr", "tx_power_dbm"):
            old = cached_state.get(field_name)
            new = actual.get(field_name)
            if old is None or new is None:
                continue
            if not _close_enough(new, old, field_name):
                reports.append(DriftReport(
                    field=field_name,
                    expected=old,
                    actual=new,
                    fix_hint=(
                        f"Radio's {field_name} changed since last cache "
                        f"(was {old}, now {new}). Did someone change config "
                        "out-of-band? Run 'View' to confirm intentional."
                    ),
                    severity="info",  # not a real config violation
                ))

    return reports


def _close_enough(a: Any, b: Any, field_name: str) -> bool:
    """Field-aware equality. Floats use ε to absorb radio-side rounding."""
    if a is None or b is None:
        return a == b
    if field_name in ("radio_freq_mhz",):
        try:
            return abs(float(a) - float(b)) < 0.01
        except (TypeError, ValueError):
            return a == b
    if field_name in ("radio_bw_khz",):
        try:
            return abs(float(a) - float(b)) < 0.5
        except (TypeError, ValueError):
            return a == b
    return a == b


# ─── apply_desired_config ───────────────────────────────────────────


def apply_desired_config(
    handler: Any,
    desired: DesiredConfig,
    *,
    timeout_per_op_s: float = 10.0,
) -> Dict[str, Any]:
    """Push ``desired`` to the live radio via ``handler`` and verify.

    ``handler`` must expose the same write surface as
    ``MeshCoreHandler``: ``set_radio_lora``, ``set_radio_tx_power``,
    ``set_radio_channel``, and ``get_radio_state``. The supervisor handler
    does not (yet) expose these — this function detects the gap and
    returns ``{"applied": False, "reason": "supervisor mode"}``.

    Returns a dict::

        {
            "applied": bool,
            "writes": [<one entry per setter call>],
            "errors": [<one entry per failed write>],
            "post_state": <get_radio_state() after all writes>,
            "drift_after": [<DriftReport>],   # empty if push succeeded
        }
    """
    if desired.is_empty():
        return {
            "applied": False,
            "reason": "no desired values configured",
            "writes": [],
            "errors": [],
            "post_state": None,
            "drift_after": [],
        }

    setters_present = (
        hasattr(handler, "set_radio_lora")
        and hasattr(handler, "set_radio_tx_power")
        and hasattr(handler, "set_radio_channel")
        and hasattr(handler, "get_radio_state")
    )
    if not setters_present:
        # MeshCoreSupervisorHandler doesn't expose the radio-config setters
        # yet. Skip cleanly — operator can still push via the in-process
        # daemon by stopping meshcore-radio.service.
        return {
            "applied": False,
            "reason": (
                "active handler does not expose radio-config setters "
                "(supervisor mode? push via in-process daemon for now)"
            ),
            "writes": [],
            "errors": [],
            "post_state": None,
            "drift_after": [],
        }

    writes: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # Order matters: LoRa first (changes the band → cap), then TX, then channels.
    if desired.has_lora():
        try:
            handler.set_radio_lora(
                freq_mhz=desired.freq_mhz, bw_khz=desired.bw_khz,
                sf=desired.sf, cr=desired.cr,
            )
            writes.append({
                "op": "set_radio_lora",
                "args": {
                    "freq": desired.freq_mhz, "bw": desired.bw_khz,
                    "sf": desired.sf, "cr": desired.cr,
                },
            })
        except Exception as e:
            errors.append({"op": "set_radio_lora", "error": str(e)})

    if desired.tx_power_dbm is not None and not _has_op_error(errors, "set_radio_lora"):
        # If LoRa write failed we'd be capping against the wrong band — skip TX.
        try:
            handler.set_radio_tx_power(dbm=desired.tx_power_dbm)
            writes.append({"op": "set_radio_tx_power",
                           "args": {"dbm": desired.tx_power_dbm}})
        except Exception as e:
            errors.append({"op": "set_radio_tx_power", "error": str(e)})

    for ch in desired.channels:
        try:
            handler.set_radio_channel(
                idx=ch.idx, name=ch.name, secret_hex=ch.secret,
            )
            writes.append({
                "op": "set_radio_channel",
                "args": {"idx": ch.idx, "name": ch.name,
                         "has_secret": ch.secret is not None},
            })
        except Exception as e:
            errors.append({
                "op": "set_radio_channel",
                "args": {"idx": ch.idx, "name": ch.name},
                "error": str(e),
            })

    # Re-read once at the end. Refresh=True forces a live read.
    try:
        post = handler.get_radio_state(refresh=True)
    except Exception as e:
        post = {"error": f"post-write refresh failed: {e}"}

    drift_after = check_drift(post or {}, desired=desired)

    return {
        "applied": not errors,
        "writes": writes,
        "errors": errors,
        "post_state": post,
        "drift_after": drift_after,
    }


def _has_op_error(errors: List[Dict[str, Any]], op: str) -> bool:
    return any(e.get("op") == op for e in errors)


# ─── Config doctor ──────────────────────────────────────────────────


@dataclass
class DoctorIssue:
    """Single diagnostic finding from the doctor."""
    severity: str  # "info" | "warning" | "error"
    code: str      # short identifier — TUI groups by this
    message: str
    fix_hint: str


def meshcore_config_doctor(
    *,
    handler: Any = None,
    desired: Optional[DesiredConfig] = None,
    cached: Optional[Dict[str, Any]] = None,
    config_dir: Optional[Path] = None,
) -> List[DoctorIssue]:
    """Run a battery of diagnostics over the current MeshCore config.

    Pure: takes its inputs as arguments so callers can swap them out for
    tests. ``handler`` is optional — the doctor calls ``get_radio_state``
    on it if available, otherwise relies on ``cached``.
    """
    issues: List[DoctorIssue] = []

    if cached is None:
        cached = load_cached_radio_state(config_dir=config_dir)

    actual: Optional[Dict[str, Any]] = None
    if handler is not None and hasattr(handler, "get_radio_state"):
        try:
            actual = handler.get_radio_state(refresh=False) or {}
        except Exception as e:
            issues.append(DoctorIssue(
                severity="error",
                code="radio_state_read_failed",
                message=f"get_radio_state raised: {e}",
                fix_hint=(
                    "Check that the daemon is running and the MeshCore "
                    "radio is connected (TUI: MeshCore → Daemon Control)"
                ),
            ))

    if actual is None and cached is not None:
        actual = (cached or {}).get("state") or None

    if actual is None:
        issues.append(DoctorIssue(
            severity="info",
            code="no_radio_state",
            message="No live radio state and no cached snapshot",
            fix_hint=(
                "Start the daemon and bring up the MeshCore radio, then "
                "rerun the doctor"
            ),
        ))
        # Without state we can't do much else — bail early.
        return issues

    if desired is not None and not desired.is_empty():
        for d in check_drift(actual, desired=desired):
            issues.append(DoctorIssue(
                severity=d.severity,
                code=f"desired_drift:{d.field}",
                message=(
                    f"radio reports {d.field}={d.actual!r}, desired "
                    f"is {d.expected!r}"
                ),
                fix_hint=d.fix_hint,
            ))

    if cached is not None:
        for d in check_drift(actual, cached=cached):
            issues.append(DoctorIssue(
                severity=d.severity,
                code=f"cached_drift:{d.field}",
                message=(
                    f"{d.field} changed since last cache "
                    f"(was {d.expected!r}, now {d.actual!r})"
                ),
                fix_hint=d.fix_hint,
            ))

    # Classify radio firmware for info — caller can render "X is current"
    fw_build = actual.get("fw_build")
    fw_ver = actual.get("fw_ver")
    if fw_build:
        issues.append(DoctorIssue(
            severity="info",
            code="firmware_version",
            message=f"Firmware: {fw_build} (proto v{fw_ver})" if fw_ver
                    else f"Firmware: {fw_build}",
            fix_hint=(
                "Compare against latest MeshCore release on "
                "https://github.com/meshcore-dev/MeshCore/releases — "
                "OTA flash is a separate operator step (not yet automated)"
            ),
        ))

    # Region detection — if the desired region doesn't match the live freq,
    # surface that as an error (likely operator misconfig).
    if desired is not None and desired.region:
        try:
            from gateway.meshcore_radio_config import region_for_freq
            band = region_for_freq(_to_float(actual.get("radio_freq_mhz")))
        except ImportError:
            band = None
        if band is not None and band.label.upper() != desired.region.upper():
            issues.append(DoctorIssue(
                severity="error",
                code="region_mismatch",
                message=(
                    f"radio is on {actual.get('radio_freq_mhz')} MHz "
                    f"({band.label}) but desired region is {desired.region}"
                ),
                fix_hint=(
                    "Either change the radio's frequency to a band in the "
                    "desired region, or update gateway.json[meshcore].region"
                ),
            ))

    return issues
