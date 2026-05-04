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
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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
