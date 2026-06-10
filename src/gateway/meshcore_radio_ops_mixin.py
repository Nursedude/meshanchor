"""MeshCore radio-config / control-surface mixin for MeshCoreHandler.

Code-motion extraction from ``meshcore_handler.py`` (1,500-line rule),
following the ``meshcore_bridge_mixin.py`` idiom: the thin radio-config
delegates to :class:`gateway.meshcore_radio_config.MeshCoreRadioConfig`
(Phase 4a/4b read/write surfaces), the desired-config apply / drift-log
hook (Session 3), the periodic advert heartbeat, and the Session 4 radio
control surfaces. Mixed back into ``MeshCoreHandler`` in
``gateway/meshcore_handler.py`` (the hub) — zero behavior change, no
external import paths affected.

Host class must provide ``self._radio`` (MeshCoreRadioConfig),
``self._loop``, ``self._stop_event``, ``self._connected``,
``self._advert_heartbeat_task``, ``self.config`` and ``self._async_wait``.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class MeshCoreRadioOpsMixin:
    """Radio config delegates, advert heartbeat, and control surfaces."""

    # ─────────────────────────────────────────────────────────────────
    # Radio config — thin delegates to MeshCoreRadioConfig.
    #
    # Implementation lives in gateway.meshcore_radio_config so this module
    # can stay focused on connection / messaging. The wrappers preserve the
    # public method names that Phase 4a tests already depend on.
    # ─────────────────────────────────────────────────────────────────

    async def _refresh_radio_state(self) -> None:
        await self._radio.refresh()

    def _apply_desired_and_log_drift(self) -> None:
        """Session 3 hook: push gateway.json[meshcore].desired_* to the radio
        and log any drift. Opt-in via apply_desired_on_connect.

        The cache write happens regardless — drift detection across daemon
        restarts depends on having the snapshot on disk.
        """
        from utils.meshcore_config import (
            DesiredConfig, apply_desired_config, cache_radio_state,
            check_drift, load_cached_radio_state,
        )

        mc_config = getattr(self.config, "meshcore", None)
        if mc_config is None:
            return

        actual = self.get_radio_state(refresh=False)
        cached = load_cached_radio_state()
        desired = DesiredConfig.from_gateway_config(mc_config)

        if cached is not None:
            for d in check_drift(actual, cached=cached):
                logger.info(
                    "MeshCore radio drift since last cache: %s was %r, now %r — %s",
                    d.field, d.expected, d.actual, d.fix_hint,
                )

        if getattr(mc_config, "apply_desired_on_connect", False) and not desired.is_empty():
            logger.info("MeshCore: applying desired config from gateway.json")
            result = apply_desired_config(self, desired)
            if not result.get("applied"):
                reason = result.get("reason") or "unknown"
                logger.warning("MeshCore desired-config apply skipped: %s", reason)
            for err in result.get("errors") or []:
                logger.warning("MeshCore desired-config write failed: %s", err)
            for d in result.get("drift_after") or []:
                logger.warning(
                    "MeshCore drift after apply: %s expected %r got %r — %s",
                    d.field, d.expected, d.actual, d.fix_hint,
                )
            actual = result.get("post_state") or self.get_radio_state(refresh=False)
        elif not desired.is_empty():
            for d in check_drift(actual, desired=desired):
                logger.warning(
                    "MeshCore desired vs actual drift: %s desired=%r actual=%r — %s",
                    d.field, d.expected, d.actual, d.fix_hint,
                )

        cache_radio_state(actual)

    def _set_radio_error(self, message: str) -> None:
        self._radio.set_error(message)

    def get_radio_state(self, refresh: bool = False) -> Dict[str, Any]:
        return self._radio.get_state(refresh=refresh)

    # Synchronous setters — schedule the async write on the daemon's event
    # loop and wait for completion. Raise RadioWriteError on validation /
    # NAK so the HTTP layer can map cleanly to 4xx/5xx.
    def set_radio_lora(
        self, freq_mhz: float, bw_khz: float, sf: int, cr: int
    ) -> Dict[str, Any]:
        return self._run_radio_write(
            self._radio.set_lora(freq_mhz, bw_khz, sf, cr)
        )

    def set_radio_tx_power(self, dbm: int) -> Dict[str, Any]:
        return self._run_radio_write(self._radio.set_tx_power(dbm))

    def set_radio_channel(
        self, idx: int, name: str, secret_hex: Optional[str] = None
    ) -> Dict[str, Any]:
        return self._run_radio_write(
            self._radio.set_channel(idx, name, secret_hex)
        )

    def set_radio_name(self, name: str) -> Dict[str, Any]:
        """Set the advertised node name. Caller should send_advert after."""
        return self._run_radio_write(self._radio.set_name(name))

    def set_radio_coords(self, lat: float, lon: float) -> Dict[str, Any]:
        """Set the radio's GPS coordinates. Caller should send_advert after."""
        return self._run_radio_write(self._radio.set_coords(lat, lon))

    def send_radio_advert(self, flood: bool = False) -> Dict[str, Any]:
        """Broadcast an advertisement so peers pick up name/coords/key changes."""
        return self._run_radio_write(self._radio.send_advert(flood=flood))

    # ── Periodic advert heartbeat ───────────────────────────────────────

    def _advert_heartbeat_settings(self) -> tuple:
        """Read (interval_sec, flood) from gateway config. Defaults if absent."""
        mc = getattr(self.config, "meshcore", None)
        if mc is None:
            return 0, False
        interval = int(getattr(mc, "advert_heartbeat_sec", 600) or 0)
        flood = bool(getattr(mc, "advert_heartbeat_flood", False))
        return interval, flood

    def _start_advert_heartbeat(self) -> None:
        """Schedule the periodic-advert task on the event loop, if enabled."""
        interval, flood = self._advert_heartbeat_settings()
        if interval <= 0:
            logger.debug("MeshCore advert heartbeat disabled (interval=%d)", interval)
            return
        if self._loop is None or not self._loop.is_running():
            logger.debug("MeshCore advert heartbeat skipped: no running loop")
            return
        # Cancel any leftover task from a prior connect cycle.
        self._cancel_advert_heartbeat()
        self._advert_heartbeat_task = self._loop.create_task(
            self._advert_heartbeat_loop(interval, flood),
            name="meshcore-advert-heartbeat",
        )
        logger.info(
            "MeshCore advert heartbeat: every %ds (flood=%s)", interval, flood,
        )

    def _cancel_advert_heartbeat(self) -> None:
        """Cancel the heartbeat task if running. Safe to call repeatedly."""
        task = self._advert_heartbeat_task
        self._advert_heartbeat_task = None
        if task is None or task.done():
            return
        try:
            task.cancel()
        except Exception as e:
            logger.debug(f"Advert heartbeat cancel error: {e}")

    async def _advert_heartbeat_loop(self, interval: int, flood: bool) -> None:
        """Send one advert per ``interval`` seconds until stop or disconnect.

        Failures are logged and swallowed — a transient wire glitch must
        not kill the heartbeat. The loop also exits if the radio handle
        gets swapped out from under us (reconnect creates a fresh task).
        """
        # Fire one immediately so neighbors don't have to wait a full
        # interval after a daemon restart to hear from us.
        await self._fire_heartbeat_advert(flood)
        while not self._stop_event.is_set() and self._connected:
            try:
                await self._async_wait(interval)
            except asyncio.CancelledError:
                return
            if self._stop_event.is_set() or not self._connected:
                return
            await self._fire_heartbeat_advert(flood)

    async def _fire_heartbeat_advert(self, flood: bool) -> None:
        """Send one heartbeat advert. Logs at INFO; never raises."""
        try:
            await self._radio.send_advert(flood=flood)
            logger.info("MeshCore advert heartbeat fired (flood=%s)", flood)
        except Exception as e:
            logger.warning(f"MeshCore advert heartbeat failed: {e}")

    # ── Session 4 — radio control surfaces ──────────────────────────────

    def reset_radio(self) -> Dict[str, Any]:
        """Soft-reset the radio. Returns the post-reset (stale) state."""
        return self._run_radio_write(self._radio.reset_radio())

    def apply_preset(self, region: str, preset: str) -> Dict[str, Any]:
        """Map ``(region, preset)`` to LoRa params via the PRESETS table,
        push, verify. Raises RadioWriteError if the pair is unknown."""
        from .meshcore_radio_config import RadioWriteError
        from utils.meshcore_config import lookup_preset
        mapped = lookup_preset(region, preset)
        if mapped is None:
            raise RadioWriteError(
                f"unknown (region, preset) pair: ({region!r}, {preset!r})"
            )
        freq, bw, sf, cr = mapped
        # set_radio_lora already validates + verifies via re-read
        return self.set_radio_lora(freq_mhz=freq, bw_khz=bw, sf=sf, cr=cr)

    def get_firmware_info(self) -> Dict[str, Any]:
        """Return a firmware-focused slice of the cached radio state."""
        state = self.get_radio_state(refresh=False)
        return {
            "fw_build": state.get("fw_build"),
            "fw_ver": state.get("fw_ver"),
            "model": state.get("model"),
            "node_name": state.get("node_name"),
            "last_refresh_ts": state.get("last_refresh_ts"),
            "source": state.get("source"),
        }

    def _run_radio_write(self, coro) -> Dict[str, Any]:
        """Bridge sync HTTP/TUI callers to the daemon's asyncio loop.

        If no loop is running (tests, sim, or pre-connect), runs inline so
        validation paths still work without booting the daemon. Otherwise
        schedules on ``self._loop`` and blocks for up to 10s.
        """
        if self._loop is None or not self._loop.is_running():
            return asyncio.run(coro)
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=10.0)
