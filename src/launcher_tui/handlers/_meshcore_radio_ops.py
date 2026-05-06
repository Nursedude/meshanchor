"""Session 4 radio control flows for the MeshCore TUI handler.

Extracted from ``meshcore.py`` to keep that file under the 1500-line cap
(see CLAUDE.md). Mirrors the ``_meshchatx_service_ops.py`` mixin pattern:
the methods here run on a ``MeshCoreHandler`` instance that already
provides ``self.ctx``, ``self.CHAT_API_BASE``, ``self._radio_fetch_state``,
``self._radio_put``, ``self._show_write_result``, ``self._fmt_freq``, and
``self._fmt_bw``.

Three menu items live here:

* **Switch Preset** — picks a ``(region, preset)`` from
  ``utils.meshcore_config.PRESETS`` and PUTs ``/radio/preset``.
* **Firmware Info** — GET ``/radio/firmware`` for the build / proto version.
* **Soft Reset** — POST ``/radio/reset`` for a wire-protocol reboot.
"""

from __future__ import annotations

from backend import clear_screen


class MeshCoreRadioOpsMixin:
    """Mixin: preset switch, firmware info, soft reset."""

    def _meshcore_switch_preset(self):
        """Pick a (region, preset) from utils.meshcore_config.PRESETS, push,
        and verify via re-read. Uses the daemon's PUT /radio/preset
        endpoint which delegates to MeshCoreHandler.apply_preset."""
        try:
            from utils.meshcore_config import PRESETS, known_presets, known_regions
        except ImportError as e:
            self.ctx.dialog.msgbox(
                "Switch Preset",
                f"meshcore_config module not available: {e}",
            )
            return

        regions = known_regions()
        if not regions:
            self.ctx.dialog.msgbox("Switch Preset", "No presets defined.")
            return

        snap = self._radio_fetch_state(refresh=False)
        if not snap.get("ok"):
            self.ctx.dialog.msgbox(
                "Daemon Unreachable",
                f"Couldn't read current radio state: {snap.get('error')}\n\n"
                "Start the MeshCore daemon and try again.",
            )
            return
        state = snap.get("radio") or {}
        cur_freq = state.get("radio_freq_mhz")
        cur_bw = state.get("radio_bw_khz")
        cur_sf = state.get("radio_sf")
        cur_cr = state.get("radio_cr")

        region_choices = [(r, r) for r in regions]
        region = self.ctx.dialog.menu(
            "Region",
            f"Currently: {self._fmt_freq(cur_freq)} {self._fmt_bw(cur_bw)} "
            f"sf={cur_sf} cr={cur_cr}\n\nPick a region:",
            region_choices,
        )
        if region is None:
            return

        preset_choices = []
        for p in known_presets(region):
            mapped = PRESETS[(region, p)]
            preset_choices.append(
                (p, f"{p:<14} {mapped[0]} MHz / {mapped[1]} kHz / sf{mapped[2]} cr{mapped[3]}")
            )
        if not preset_choices:
            self.ctx.dialog.msgbox(
                "Switch Preset",
                f"No presets defined for region {region}.",
            )
            return
        preset = self.ctx.dialog.menu(
            f"Preset ({region})", "Pick a preset:", preset_choices,
        )
        if preset is None:
            return

        target = PRESETS[(region, preset)]
        confirm = (
            f"Push preset to radio?\n\n"
            f"  Region:   {region}\n"
            f"  Preset:   {preset}\n"
            f"  Maps to:  {target[0]} MHz / {target[1]} kHz / sf{target[2]} cr{target[3]}\n"
            f"\nCurrent: {self._fmt_freq(cur_freq)} {self._fmt_bw(cur_bw)} "
            f"sf={cur_sf} cr={cur_cr}\n"
            "\nWrong region/preset can violate licence terms. Continue?"
        )
        if not self.ctx.dialog.yesno("Confirm Preset Switch", confirm, default_no=True):
            return
        if not self.ctx.dialog.yesno(
            "Really Switch?",
            "Final check — actually PUT this preset to the radio?",
            default_no=True,
        ):
            return

        result = self._radio_put("preset", {"region": region, "preset": preset})
        self._show_write_result(f"Preset {region}/{preset}", result)

    def _meshcore_firmware_info(self):
        """Show MeshCore firmware build / proto version. Hits GET /radio/firmware."""
        import json
        import urllib.error
        import urllib.request

        clear_screen()
        print("=== MeshCore Firmware Info ===\n")
        url = f"{self.CHAT_API_BASE}/radio/firmware"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = payload.get("error") or str(e)
            except (ValueError, OSError):
                msg = str(e)
            print(f"  Daemon error (HTTP {e.code}): {msg}")
            self.ctx.wait_for_enter()
            return
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            print(f"  Daemon's HTTP API on :8081 is not reachable: {e}")
            print("  Start the daemon: MeshCore → Daemon Control → Start daemon")
            self.ctx.wait_for_enter()
            return

        info = body.get("firmware") or {}
        node = info.get("node_name") or "(unknown)"
        model = info.get("model") or "(unknown)"
        fw = info.get("fw_build") or "(unknown)"
        fw_ver = info.get("fw_ver")
        source = info.get("source")
        ts = info.get("last_refresh_ts")

        print(f"  Node Name:   {node}")
        print(f"  Model:       {model}")
        if fw_ver is not None:
            print(f"  Firmware:    {fw}  (proto v{fw_ver})")
        else:
            print(f"  Firmware:    {fw}")
        if source == "simulator":
            print("  Source:      SIMULATOR (daemon is in simulation mode)")
        elif source:
            print(f"  Source:      {source}")
        if ts:
            import time as _time
            ago = max(0, int(_time.time() - ts))
            print(f"  Last read:   {ago}s ago")

        print()
        print("  Latest releases: https://github.com/meshcore-dev/MeshCore/releases")
        print("  OTA flash flow is not yet automated — flash via meshcore-cli.")
        self.ctx.wait_for_enter()

    def _meshcore_soft_reset(self):
        """POST /radio/reset to reboot the radio via wire protocol."""
        confirm = (
            "Soft-reset the MeshCore radio?\n\n"
            "The radio drops link mid-call; the bridge daemon's reconnect "
            "loop brings it back up automatically. Channel state and any "
            "unsent messages survive the reboot.\n\n"
            "Continue?"
        )
        if not self.ctx.dialog.yesno("Confirm Soft Reset", confirm, default_no=True):
            return

        import json
        import urllib.error
        import urllib.request

        url = f"{self.CHAT_API_BASE}/radio/reset"
        try:
            req = urllib.request.Request(
                url, data=b"{}", method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                err_payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = err_payload.get("error") or str(e)
            except (ValueError, OSError):
                msg = str(e)
            self.ctx.dialog.msgbox(
                "Soft Reset — Failed",
                f"HTTP {e.code}: {msg}",
            )
            return
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            self.ctx.dialog.msgbox(
                "Soft Reset — Failed",
                f"Daemon unreachable: {e}",
            )
            return

        radio = payload.get("radio") or {}
        note = radio.get("error") or "radio is restarting"
        self.ctx.dialog.msgbox(
            "Soft Reset — Sent",
            f"Reset command accepted.\n\n"
            f"Daemon note: {note}\n\n"
            "Wait ~10s for the radio to come back up, then use 'View' to "
            "confirm reconnection.",
        )
