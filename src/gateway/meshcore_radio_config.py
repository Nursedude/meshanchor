"""MeshCore radio config — read-only state cache + (Phase 4b) setter wrappers.

Extracted from ``meshcore_handler.py`` so the daemon module can stay focused on
connection / messaging concerns. ``MeshCoreHandler`` instantiates one
``MeshCoreRadioConfig`` per handler and delegates the radio-state methods to
it; thin wrappers on the handler preserve the public surface that Phase 4a
tests already depend on (``_refresh_radio_state`` / ``_set_radio_error`` /
``get_radio_state``).

The cache is the only thing exposed to the HTTP layer — channel secrets are
intentionally dropped before they leave the daemon (only a 2-char hash is
surfaced).
"""

import asyncio
import hashlib
import logging
import threading
import time
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)


class RadioWriteError(Exception):
    """Raised when a radio-config setter rejects input or the radio NAKs."""


# ── Region table ────────────────────────────────────────────────────
#
# Sources:
#   * EU433, EU868: ETSI EN 300 220-1 v3.1.1 (Short Range Devices, 25–1000 MHz).
#       — 433.05–434.79 MHz: 10 mW ERP (10 dBm)
#       — 863–870 MHz:       25 mW ERP (14 dBm)
#       https://www.etsi.org/deliver/etsi_en/300200_300299/30022001/
#   * US915: FCC Part 15.247(b)(3) — 902–928 MHz, 1 W conducted (30 dBm).
#       https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-15/subpart-C/section-15.247
#   * KR920: KCC Notice 2013-31, frequency designations for unlicensed devices.
#       — 920.9–923.3 MHz: 25 mW EIRP (14 dBm)
#   * AU915 overlaps US915 with an identical 30 dBm cap (ACMA LIPD class licence).
#
# Caps are *upper bounds* — a node should also respect the radio's own
# `max_tx_power_dbm` (read from SELF_INFO). The setter takes the lower of the
# two before rejecting input.

class RegionBand(NamedTuple):
    label: str
    low_mhz: float
    high_mhz: float
    max_tx_dbm: int
    source: str


REGION_BANDS: Tuple[RegionBand, ...] = (
    RegionBand("EU433", 433.05, 434.79, 10, "ETSI EN 300 220 — 10 mW ERP"),
    RegionBand("EU868", 863.0, 870.0, 14, "ETSI EN 300 220 — 25 mW ERP"),
    RegionBand("KR920", 920.9, 923.3, 14, "KCC — 25 mW EIRP"),
    # Listed last so the narrower KR920 wins on overlap.
    RegionBand("US915", 902.0, 928.0, 30, "FCC Part 15.247(b)(3) — 1 W conducted"),
)


def region_for_freq(freq_mhz: Optional[float]) -> Optional[RegionBand]:
    """Return the narrowest band that contains ``freq_mhz``, or None if unknown.

    Narrower bands are preferred so KR920 (1.4 MHz wide) wins over US915
    (26 MHz wide) on overlap. Caller decides what to do with None — the
    setter treats it as "no region cap" and falls back to the radio's own
    ``max_tx_power_dbm``.
    """
    if freq_mhz is None:
        return None
    matches = [b for b in REGION_BANDS if b.low_mhz <= freq_mhz <= b.high_mhz]
    if not matches:
        return None
    return min(matches, key=lambda b: b.high_mhz - b.low_mhz)


# ── LoRa parameter ranges ──────────────────────────────────────────
#
# Defensive bounds applied before we even ship the call to the radio. The
# radio firmware does its own validation; ours is here to catch obvious
# typos (e.g. a stray `0` that turns 125 kHz into 1250 kHz) before they
# reach the wire.

# SX1262 supported bandwidths in kHz.
LORA_BW_KHZ_VALID = (7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0)
LORA_SF_RANGE = (5, 12)            # SX1262: SF5..SF12
LORA_CR_RANGE = (5, 8)             # 4/5 .. 4/8
LORA_FREQ_RANGE_MHZ = (137.0, 1020.0)  # SX1262 PLL range


def validate_lora_params(
    freq_mhz: float, bw_khz: float, sf: int, cr: int
) -> None:
    """Raise RadioWriteError if any LoRa parameter is out of range."""
    if not isinstance(freq_mhz, (int, float)):
        raise RadioWriteError(f"freq must be numeric, got {type(freq_mhz).__name__}")
    if not (LORA_FREQ_RANGE_MHZ[0] <= float(freq_mhz) <= LORA_FREQ_RANGE_MHZ[1]):
        raise RadioWriteError(
            f"freq {freq_mhz} MHz outside SX1262 PLL range "
            f"{LORA_FREQ_RANGE_MHZ[0]}..{LORA_FREQ_RANGE_MHZ[1]} MHz"
        )
    if not isinstance(bw_khz, (int, float)) or float(bw_khz) not in LORA_BW_KHZ_VALID:
        raise RadioWriteError(
            f"bw {bw_khz} kHz not in supported set {LORA_BW_KHZ_VALID}"
        )
    if not isinstance(sf, int) or not (LORA_SF_RANGE[0] <= sf <= LORA_SF_RANGE[1]):
        raise RadioWriteError(
            f"sf {sf} outside range {LORA_SF_RANGE[0]}..{LORA_SF_RANGE[1]}"
        )
    if not isinstance(cr, int) or not (LORA_CR_RANGE[0] <= cr <= LORA_CR_RANGE[1]):
        raise RadioWriteError(
            f"cr {cr} outside range {LORA_CR_RANGE[0]}..{LORA_CR_RANGE[1]}"
        )


def validate_tx_power(
    dbm: int, freq_mhz: Optional[float], radio_max_dbm: Optional[int]
) -> int:
    """Validate ``dbm`` against region cap + radio's own max. Returns the value.

    The cap used is the lower of the regional limit (from ``freq_mhz``) and
    the radio's reported ``max_tx_power_dbm``. If neither is known we just
    enforce a sane absolute ceiling of 30 dBm so a typo can't request 1 kW.
    """
    if not isinstance(dbm, int):
        raise RadioWriteError(f"tx_power must be int, got {type(dbm).__name__}")
    if dbm < -9:  # SX1262 floor
        raise RadioWriteError(f"tx_power {dbm} dBm below radio floor (-9)")

    caps: List[int] = []
    band = region_for_freq(freq_mhz)
    if band is not None:
        caps.append(band.max_tx_dbm)
    if radio_max_dbm is not None:
        caps.append(int(radio_max_dbm))
    if not caps:
        caps.append(30)  # absolute ceiling fallback

    cap = min(caps)
    if dbm > cap:
        why = []
        if band is not None:
            why.append(f"{band.label} cap = {band.max_tx_dbm} dBm ({band.source})")
        if radio_max_dbm is not None:
            why.append(f"radio max = {radio_max_dbm} dBm")
        raise RadioWriteError(
            f"tx_power {dbm} dBm exceeds {cap} dBm "
            f"({'; '.join(why) if why else 'absolute ceiling'})"
        )
    return dbm


# ── Channel-secret derivation ──────────────────────────────────────
#
# meshcore_py auto-derives the channel secret as ``sha256(name)[:16]`` when
# the channel name starts with ``#`` and no explicit secret is supplied.
# We expose the same rule here so the TUI / HTTP layers can preview the
# derived hash before committing the write.

CHANNEL_NAME_MAX_LEN = 32


def derive_channel_secret(name: str) -> bytes:
    """Return ``sha256(name.encode())[:16]`` — meshcore_py's rule for #-names."""
    return hashlib.sha256(name.encode("utf-8")).digest()[:16]


def parse_channel_secret(secret_hex: Optional[str], name: str) -> bytes:
    """Validate user-supplied secret, or derive from name when permitted.

    Rules:
      * If ``secret_hex`` is given, it must decode to exactly 16 bytes
        (32 hex chars). Whitespace is tolerated.
      * Otherwise, ``name`` must start with ``#`` so we can safely derive
        ``sha256(name)[:16]``. Non-#-prefixed channels need an explicit
        secret — this matches meshcore_py's contract.
    """
    if secret_hex:
        cleaned = "".join(secret_hex.split())
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError as e:
            raise RadioWriteError(f"secret is not valid hex: {e}") from e
        if len(raw) != 16:
            raise RadioWriteError(
                f"secret must be 16 bytes / 32 hex chars, got {len(raw)} bytes"
            )
        return raw
    if not name.startswith("#"):
        raise RadioWriteError(
            f"channel '{name}' has no '#' prefix — explicit secret is required"
        )
    return derive_channel_secret(name)


def validate_channel_name(name: str) -> str:
    """Trim + bounds-check a channel name. Returns the cleaned value."""
    if not isinstance(name, str):
        raise RadioWriteError(f"name must be str, got {type(name).__name__}")
    cleaned = name.strip()
    if not cleaned:
        raise RadioWriteError("channel name is empty")
    if len(cleaned) > CHANNEL_NAME_MAX_LEN:
        raise RadioWriteError(
            f"channel name '{cleaned[:8]}…' exceeds {CHANNEL_NAME_MAX_LEN} chars"
        )
    if "\x00" in cleaned:
        raise RadioWriteError("channel name contains a null byte")
    return cleaned


def _coerce_int(value: Any) -> Optional[int]:
    """Convert SELF_INFO/DEVICE_INFO numeric fields to int, tolerating None."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    """Convert SELF_INFO numeric fields to float, tolerating None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_radio_state() -> Dict[str, Any]:
    """Initial shape of the read-only radio-state cache served by /radio."""
    return {
        "radio_freq_mhz": None,
        "radio_bw_khz": None,
        "radio_sf": None,
        "radio_cr": None,
        "tx_power_dbm": None,
        "max_tx_power_dbm": None,
        "max_channels": None,
        "channels": [],          # list[{idx, name, hash}] — secret never exposed
        "node_name": None,
        "fw_build": None,
        "model": None,
        "fw_ver": None,
        "last_refresh_ts": None,  # epoch seconds
        "source": None,           # "radio" | "simulator" | None
        "error": None,            # str if last refresh failed
    }


def _is_simulator(meshcore: Any) -> bool:
    # Duck-type to avoid importing MeshCoreSimulator (would cycle back into
    # meshcore_handler at module load).
    if meshcore is None:
        return False
    return type(meshcore).__name__ == "MeshCoreSimulator"


class MeshCoreRadioConfig:
    """Per-handler radio-state cache + write helpers.

    Reads ``handler._meshcore`` and ``handler._loop`` lazily so the handler
    can swap the underlying meshcore_py instance during reconnect without us
    holding a stale reference. The cache lock is local to this class.
    """

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self._state: Dict[str, Any] = _empty_radio_state()
        self._lock = threading.Lock()

    # ── Read path ────────────────────────────────────────────────────

    def get_state(self, refresh: bool = False) -> Dict[str, Any]:
        """Return a snapshot of the cache, optionally refreshing first.

        If ``refresh`` is True and the asyncio loop is running, schedules a
        re-read on that loop and waits up to 8s. Otherwise returns whatever
        is cached (possibly empty if the daemon has never connected).
        """
        loop = getattr(self._handler, "_loop", None)
        if refresh and loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self.refresh(), loop)
                fut.result(timeout=8.0)
            except Exception as e:
                self.set_error(f"Refresh failed: {e}")
        with self._lock:
            return dict(self._state)

    def set_error(self, message: str) -> None:
        """Stamp an error onto the cache without losing the prior snapshot."""
        with self._lock:
            self._state["error"] = message
            self._state["last_refresh_ts"] = time.time()

    async def refresh(self) -> None:
        """Re-read radio params + channel slots from the device."""
        meshcore = getattr(self._handler, "_meshcore", None)
        if meshcore is None:
            self.set_error("MeshCore not connected")
            return

        if _is_simulator(meshcore):
            with self._lock:
                self._state = _empty_radio_state()
                self._state.update({
                    "radio_freq_mhz": 869.525,
                    "radio_bw_khz": 250.0,
                    "radio_sf": 11,
                    "radio_cr": 5,
                    "tx_power_dbm": 17,
                    "max_tx_power_dbm": 22,
                    "max_channels": 4,
                    "channels": [
                        {"idx": 0, "name": "public", "hash": "ab"},
                        {"idx": 1, "name": "sim-private", "hash": "c7"},
                    ],
                    "node_name": "Simulator",
                    "fw_build": "sim",
                    "model": "MeshCoreSimulator",
                    "fw_ver": 0,
                    "last_refresh_ts": time.time(),
                    "source": "simulator",
                    "error": None,
                })
            return

        # Lazy import to break the cycle and keep the
        # ``patch("gateway.meshcore_handler._HAS_MESHCORE", False)`` test
        # idiom working — we read the flag from the handler module each call
        # rather than caching at import time.
        from . import meshcore_handler as _mh
        if not _mh._HAS_MESHCORE:
            self.set_error("meshcore_py not installed")
            return

        try:
            EventType = _mh._meshcore_mod.EventType
            commands = meshcore.commands

            # SELF_INFO — radio_freq/bw/sf/cr + tx_power + node_name
            self_evt = await asyncio.wait_for(commands.send_appstart(), timeout=5.0)
            if self_evt.type != EventType.SELF_INFO:
                self.set_error(f"send_appstart returned {self_evt.type}")
                return
            self_info = self_evt.payload or {}

            # DEVICE_INFO — fw, max_channels, model
            dev_evt = await asyncio.wait_for(commands.send_device_query(), timeout=5.0)
            dev_info = dev_evt.payload if dev_evt.type == EventType.DEVICE_INFO else {}

            # Channel slots — iterate up to max_channels (default 4 if missing)
            max_channels = int(dev_info.get("max_channels", 4) or 4)
            channels: List[Dict[str, Any]] = []
            for idx in range(max_channels):
                try:
                    ch_evt = await asyncio.wait_for(
                        commands.get_channel(idx), timeout=3.0
                    )
                except asyncio.TimeoutError:
                    continue
                if ch_evt.type != EventType.CHANNEL_INFO:
                    continue
                ch = ch_evt.payload or {}
                name = (ch.get("channel_name") or "").strip()
                if not name:
                    continue  # empty slot
                channels.append({
                    "idx": int(ch.get("channel_idx", idx)),
                    "name": name,
                    "hash": ch.get("channel_hash") or "",
                })

            with self._lock:
                self._state = _empty_radio_state()
                self._state.update({
                    "radio_freq_mhz": _coerce_float(self_info.get("radio_freq")),
                    "radio_bw_khz": _coerce_float(self_info.get("radio_bw")),
                    "radio_sf": _coerce_int(self_info.get("radio_sf")),
                    "radio_cr": _coerce_int(self_info.get("radio_cr")),
                    "tx_power_dbm": _coerce_int(self_info.get("tx_power")),
                    "max_tx_power_dbm": _coerce_int(self_info.get("max_tx_power")),
                    "max_channels": max_channels,
                    "channels": channels,
                    "node_name": self_info.get("name"),
                    "fw_build": dev_info.get("fw_build"),
                    "model": dev_info.get("model"),
                    "fw_ver": _coerce_int(dev_info.get("fw ver")),
                    "last_refresh_ts": time.time(),
                    "source": "radio",
                    "error": None,
                })
        except asyncio.TimeoutError:
            self.set_error("Timeout reading radio state")
        except Exception as e:
            self.set_error(f"{type(e).__name__}: {e}")

    # ── Write path (Phase 4b) ────────────────────────────────────────
    #
    # Each setter validates input → calls the matching meshcore_py command →
    # awaits the OK/ERROR Event → refreshes the cache so the next GET /radio
    # reflects the new state. Validation lives in module-level helpers above
    # so HTTP / TUI / tests can reuse it without instantiating the class.
    #
    # All raise RadioWriteError on validation failure or radio NAK so the
    # HTTP layer can map cleanly to 400/502.

    def _require_meshcore(self) -> Any:
        meshcore = getattr(self._handler, "_meshcore", None)
        if meshcore is None:
            raise RadioWriteError("MeshCore not connected")
        if _is_simulator(meshcore):
            return meshcore  # simulator path handled per-method
        from . import meshcore_handler as _mh
        if not _mh._HAS_MESHCORE:
            raise RadioWriteError("meshcore_py not installed")
        return meshcore

    @staticmethod
    async def _await_ok(coro, what: str, timeout: float = 8.0) -> Any:
        """Run a meshcore_py command coroutine and require an OK Event.

        Raises RadioWriteError on timeout or non-OK reply. Returns the Event
        so callers can inspect the payload if needed.
        """
        from . import meshcore_handler as _mh
        try:
            evt = await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise RadioWriteError(f"{what}: timeout after {timeout}s") from e
        EventType = _mh._meshcore_mod.EventType if _mh._HAS_MESHCORE else None
        if EventType is not None and evt.type == EventType.ERROR:
            raise RadioWriteError(f"{what}: radio reported ERROR ({evt.payload})")
        if EventType is not None and evt.type != EventType.OK:
            raise RadioWriteError(
                f"{what}: unexpected event type {evt.type} (expected OK)"
            )
        return evt

    async def set_lora(
        self, freq_mhz: float, bw_khz: float, sf: int, cr: int
    ) -> Dict[str, Any]:
        """Validate + push a new (freq, bw, sf, cr) tuple, then refresh cache."""
        validate_lora_params(freq_mhz, bw_khz, sf, cr)
        meshcore = self._require_meshcore()
        if not _is_simulator(meshcore):
            await self._await_ok(
                meshcore.commands.set_radio(
                    float(freq_mhz), float(bw_khz), int(sf), int(cr)
                ),
                what="set_radio",
            )
        await self.refresh()
        return self.get_state(refresh=False)

    async def set_tx_power(self, dbm: int) -> Dict[str, Any]:
        """Validate against region cap + radio max, push, then refresh cache."""
        snap = self.get_state(refresh=False)
        validated = validate_tx_power(
            dbm,
            freq_mhz=snap.get("radio_freq_mhz"),
            radio_max_dbm=snap.get("max_tx_power_dbm"),
        )
        meshcore = self._require_meshcore()
        if not _is_simulator(meshcore):
            await self._await_ok(
                meshcore.commands.set_tx_power(validated),
                what="set_tx_power",
            )
        await self.refresh()
        return self.get_state(refresh=False)

    async def set_channel(
        self, idx: int, name: str, secret_hex: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate slot + name + secret, push, then refresh cache.

        ``secret_hex`` is optional; when omitted, the name must start with
        ``#`` so we can derive ``sha256(name)[:16]`` (matches meshcore_py's
        own auto-derivation rule).
        """
        if not isinstance(idx, int) or idx < 0:
            raise RadioWriteError(f"channel idx must be a non-negative int, got {idx!r}")
        snap = self.get_state(refresh=False)
        max_ch = snap.get("max_channels")
        if max_ch is not None and idx >= int(max_ch):
            raise RadioWriteError(f"channel idx {idx} >= max_channels {max_ch}")
        cleaned_name = validate_channel_name(name)
        secret = parse_channel_secret(secret_hex, cleaned_name)
        meshcore = self._require_meshcore()
        if not _is_simulator(meshcore):
            await self._await_ok(
                meshcore.commands.set_channel(idx, cleaned_name, secret),
                what=f"set_channel[{idx}]",
            )
        await self.refresh()
        return self.get_state(refresh=False)

    # ── Session 4: soft reset ────────────────────────────────────────

    async def reset_radio(self) -> Dict[str, Any]:
        """Soft-reset the MeshCore radio via the wire protocol.

        Tries ``commands.send_reboot()`` first, falls back to
        ``commands.reboot()`` for older meshcore_py builds. The radio
        link drops after reboot; the bridge daemon's reconnect loop
        brings it back up. The cache is invalidated so the next
        ``get_state(refresh=True)`` does a live re-read.
        """
        meshcore = self._require_meshcore()
        if _is_simulator(meshcore):
            with self._lock:
                self._state = _empty_radio_state()
                self._state["error"] = "simulator: reset is a no-op"
                self._state["source"] = "simulator"
                self._state["last_refresh_ts"] = time.time()
            return self.get_state(refresh=False)

        commands = meshcore.commands
        reboot_fn = (
            getattr(commands, "send_reboot", None)
            or getattr(commands, "reboot", None)
        )
        if reboot_fn is None:
            raise RadioWriteError(
                "meshcore_py does not expose a reboot command on this build"
            )

        try:
            await asyncio.wait_for(reboot_fn(), timeout=8.0)
        except asyncio.TimeoutError as e:
            # Reboot may not get an OK reply (the radio drops link mid-call).
            # Treat timeout as expected and let reconnect handle the rest.
            logger.info("Reboot command timed out — radio likely already restarting")
        except Exception as e:
            raise RadioWriteError(f"reset_radio: {type(e).__name__}: {e}") from e

        # Don't try to refresh — radio is restarting. Invalidate the cache so
        # callers know the snapshot is stale.
        with self._lock:
            self._state["error"] = "radio is restarting after reset"
            self._state["last_refresh_ts"] = time.time()
        return self.get_state(refresh=False)
