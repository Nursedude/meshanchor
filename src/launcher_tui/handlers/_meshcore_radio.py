"""Radio config flows for the MeshCore TUI handler.

Extracted from ``meshcore.py`` (2026-09-21, roadmap 1a) — one module per
pane, the same mixin pattern as ``_meshcore_radio_ops.py`` and the
daemon-side ``gateway/meshcore_*_mixin.py`` family. This is the read +
write side of Radio Config (status, LoRa params, TX power, channels);
the preset / firmware / identity flows live in ``_meshcore_radio_ops.py``
and lean on the helpers defined here.

The methods here run on a ``MeshCoreHandler`` instance that already
provides ``self.ctx`` and ``self.CHAT_API_BASE`` — the latter stays on
the host class because several panes consume it (this one, chat,
``meshcore_cli``, ``meshcore_positions``, and a test).
"""

from __future__ import annotations

from backend import clear_screen


class MeshCoreRadioMixin:
    """Mixin: radio status, LoRa/TX-power/channel writes, formatting helpers."""

    def _meshcore_radio_menu(self):
        """Phase 4b + Session 4: Radio Config sub-submenu (view + writes + control)."""
        while True:
            choices = [
                ("view", "View                Current LoRa / channels / TX power"),
                ("identity", "Identity & Position Set node name, lat/lon, send advertisement"),
                ("lora", "Set LoRa Params     Frequency / bandwidth / SF / coding rate"),
                ("txp", "Set TX Power        Region-aware cap enforced"),
                ("channel", "Set Channel Slot    Name + secret per slot"),
                ("preset", "Switch Preset       Pick (region, preset) from the table"),
                ("firmware", "Firmware Info       Build, model, proto version"),
                ("reset", "Soft Reset          Reboot the radio via wire protocol"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "MeshCore Radio Config",
                "Inspect or change LoRa parameters, channel slots, and TX power. "
                "Writes are double-confirmed and validated against region caps.",
                choices,
            )
            if choice is None or choice == "back":
                return
            dispatch = {
                "view": ("MeshCore Radio (view)", self._meshcore_radio_status),
                "identity": ("Identity & Position", self._meshcore_identity_menu),
                "lora": ("Set LoRa Parameters", self._meshcore_set_lora),
                "txp": ("Set TX Power", self._meshcore_set_tx_power),
                "channel": ("Set Channel Slot", self._meshcore_set_channel),
                "preset": ("Switch Preset", self._meshcore_switch_preset),
                "firmware": ("Firmware Info", self._meshcore_firmware_info),
                "reset": ("Soft Reset", self._meshcore_soft_reset),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "MeshCoreHandler._meshcore_radio_menu")

    def _meshcore_radio_status(self):
        """Read-only display of MeshCore LoRa radio config.

        Hits the daemon's GET /radio?refresh=1 endpoint, which re-reads
        SELF_INFO + DEVICE_INFO + CHANNEL_INFO from the device. Falls
        back to the cached snapshot if refresh times out.
        """
        clear_screen()
        print("=== MeshCore Radio Configuration ===\n")

        result = self._radio_fetch_state(refresh=True)
        if not result.get("ok"):
            err = result.get("error") or "unknown error"
            status = result.get("status")
            if status == 503:
                print(f"  Daemon reports MeshCore not active: {err}")
                print("  Start it: MeshCore → Daemon Control → Start daemon")
            elif status is None:
                print(f"  Daemon's HTTP API on :8081 is not reachable.")
                print(f"  ({err})")
                print("  Start the daemon: MeshCore → Daemon Control → Start daemon")
            else:
                print(f"  Daemon error ({status}): {err}")
            self.ctx.wait_for_enter()
            return

        state = result.get("radio") or {}
        if state.get("error"):
            print(f"  Note from daemon: {state['error']}\n")

        source = state.get("source")
        if source == "simulator":
            print("  [SIMULATOR] — daemon is in simulation mode, values are fake.\n")
        elif source is None and not state.get("last_refresh_ts"):
            print("  Radio state has not been read yet.")
            print("  The daemon populates this cache on connect; verify the")
            print("  MeshCore device is plugged in and the daemon is running.")
            self.ctx.wait_for_enter()
            return

        # Identity
        node = state.get("node_name") or "(unknown)"
        model = state.get("model") or "(unknown)"
        fw = state.get("fw_build") or "(unknown)"
        fw_ver = state.get("fw_ver")
        print(f"  Node Name:      {node}")
        print(f"  Model:          {model}")
        if fw_ver is not None:
            print(f"  Firmware:       {fw} (proto v{fw_ver})")
        else:
            print(f"  Firmware:       {fw}")

        # LoRa parameters
        freq = state.get("radio_freq_mhz")
        bw = state.get("radio_bw_khz")
        sf = state.get("radio_sf")
        cr = state.get("radio_cr")
        print("\n  LoRa Parameters:")
        print(f"    Frequency:    {self._fmt_freq(freq)}")
        print(f"    Bandwidth:    {self._fmt_bw(bw)}")
        print(f"    Spreading:    {sf if sf is not None else '?'}")
        print(f"    Coding Rate:  {cr if cr is not None else '?'}")
        preset = self._radio_preset_name(freq, bw, sf, cr)
        if preset:
            print(f"    Common name:  ≈ {preset}")

        # TX power
        tx = state.get("tx_power_dbm")
        max_tx = state.get("max_tx_power_dbm")
        print("\n  TX Power:")
        print(f"    Current:      {tx if tx is not None else '?'} dBm")
        print(f"    Maximum:      {max_tx if max_tx is not None else '?'} dBm")

        # Channels
        channels = state.get("channels") or []
        max_ch = state.get("max_channels")
        max_label = max_ch if max_ch is not None else "?"
        print(f"\n  Channels ({len(channels)} configured / max {max_label}):")
        if not channels:
            print("    (no channels configured)")
        for ch in channels:
            name = ch.get("name") or "(unnamed)"
            idx = ch.get("idx")
            h = ch.get("hash") or "??"
            print(f"    [{idx}] {name:<20} hash={h}")

        ts = state.get("last_refresh_ts")
        if ts:
            import time as _time
            ago = max(0, int(_time.time() - ts))
            print(f"\n  Last refreshed: {ago}s ago")

        self.ctx.wait_for_enter()

    def _radio_fetch_state(self, refresh: bool = False) -> dict:
        """GET /radio[?refresh=1] from the daemon. Returns a result dict.

        Shape on success: {"ok": True, "radio": {...}}.
        Shape on failure: {"ok": False, "status": int|None, "error": str}.
        """
        import json
        import urllib.error
        import urllib.request

        url = f"{self.CHAT_API_BASE}/radio"
        if refresh:
            url += "?refresh=1"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
                radio = body.get("radio") if isinstance(body, dict) else None
                return {"ok": True, "radio": radio or {}}
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = payload.get("error") or str(e)
            except Exception:
                msg = str(e)
            return {"ok": False, "status": e.code, "error": msg}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": None, "error": str(e)}

    def _radio_put(self, sub_path: str, body: dict) -> dict:
        """PUT to /radio/<sub_path>. Returns shaped result dict.

        Shape on success: {"ok": True, "radio": {...}}.
        Shape on failure: {"ok": False, "status": int|None, "error": str}.
        """
        import json
        import urllib.error
        import urllib.request

        url = f"{self.CHAT_API_BASE}/radio/{sub_path.lstrip('/')}"
        data = json.dumps(body).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                radio = payload.get("radio") if isinstance(payload, dict) else None
                return {"ok": True, "radio": radio or {}}
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = payload.get("error") or str(e)
            except Exception:
                msg = str(e)
            return {"ok": False, "status": e.code, "error": msg}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": None, "error": str(e)}

    # ── Phase 4b setters ─────────────────────────────────────────────
    #
    # Each setter:
    #   1. Pulls the current snapshot (no refresh — assume View was just used)
    #   2. Prompts for new value(s), seeded with current
    #   3. Computes a region cap warning where applicable
    #   4. Double-confirm dialog
    #   5. PUT /radio/...; show result via msgbox
    # No auto-write on Enter — wrong frequency or excessive TX power can
    # brick a radio for that region or violate licence terms.

    def _meshcore_set_lora(self):
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

        intro = (
            f"Current: freq={self._fmt_freq(cur_freq)}  "
            f"bw={self._fmt_bw(cur_bw)}  sf={cur_sf}  cr={cur_cr}\n\n"
            "Enter new value (blank = keep current)."
        )

        new_freq_str = self.ctx.dialog.inputbox(
            "Frequency (MHz)", intro, init=str(cur_freq) if cur_freq else "",
        )
        if new_freq_str is None:
            return
        new_bw_str = self.ctx.dialog.inputbox(
            "Bandwidth (kHz)",
            "Supported: 7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500",
            init=str(cur_bw) if cur_bw else "",
        )
        if new_bw_str is None:
            return
        new_sf_str = self.ctx.dialog.inputbox(
            "Spreading Factor",
            "Range 5..12 (higher SF = longer range, slower).",
            init=str(cur_sf) if cur_sf else "",
        )
        if new_sf_str is None:
            return
        new_cr_str = self.ctx.dialog.inputbox(
            "Coding Rate",
            "Range 5..8 (4/5..4/8). 5 = highest throughput, 8 = most robust.",
            init=str(cur_cr) if cur_cr else "",
        )
        if new_cr_str is None:
            return

        try:
            freq = float(new_freq_str.strip()) if new_freq_str.strip() else cur_freq
            bw = float(new_bw_str.strip()) if new_bw_str.strip() else cur_bw
            sf = int(new_sf_str.strip()) if new_sf_str.strip() else cur_sf
            cr = int(new_cr_str.strip()) if new_cr_str.strip() else cur_cr
        except (TypeError, ValueError) as e:
            self.ctx.dialog.msgbox("Bad Input", f"Could not parse value: {e}")
            return

        if None in (freq, bw, sf, cr):
            self.ctx.dialog.msgbox(
                "Incomplete",
                "All four LoRa parameters must have a value (blank kept current, "
                "but the radio reported no current value to keep).",
            )
            return

        warn = self._region_warning_for_freq(freq)
        confirm_text = (
            f"Push these LoRa parameters to the radio?\n\n"
            f"  Frequency:  {freq} MHz\n"
            f"  Bandwidth:  {bw} kHz\n"
            f"  Spreading:  {sf}\n"
            f"  Coding:     {cr}\n"
        )
        if warn:
            confirm_text += f"\n{warn}\n"
        confirm_text += (
            "\nWrong frequency for your region can violate licence terms or "
            "brick the radio for that region. Continue?"
        )

        if not self.ctx.dialog.yesno("Confirm LoRa Write", confirm_text, default_no=True):
            return
        # Second confirm — explicit double-tap, can't be skipped.
        if not self.ctx.dialog.yesno(
            "Really Write?",
            "Final check — actually PUT these values to the radio?",
            default_no=True,
        ):
            return

        result = self._radio_put(
            "lora", {"freq": freq, "bw": bw, "sf": sf, "cr": cr},
        )
        self._show_write_result("LoRa Parameters", result)

    def _meshcore_set_tx_power(self):
        snap = self._radio_fetch_state(refresh=False)
        if not snap.get("ok"):
            self.ctx.dialog.msgbox(
                "Daemon Unreachable",
                f"Couldn't read current radio state: {snap.get('error')}",
            )
            return
        state = snap.get("radio") or {}
        cur_tx = state.get("tx_power_dbm")
        max_tx = state.get("max_tx_power_dbm")
        cur_freq = state.get("radio_freq_mhz")

        intro = (
            f"Current: {cur_tx if cur_tx is not None else '?'} dBm  "
            f"(radio max: {max_tx if max_tx is not None else '?'} dBm)\n"
        )
        warn = self._region_tx_warning(cur_freq)
        if warn:
            intro += f"\n{warn}\n"
        intro += "\nEnter new TX power in dBm:"

        new_tx_str = self.ctx.dialog.inputbox(
            "TX Power (dBm)", intro, init=str(cur_tx) if cur_tx is not None else "",
        )
        if new_tx_str is None:
            return
        try:
            new_tx = int(new_tx_str.strip())
        except (TypeError, ValueError):
            self.ctx.dialog.msgbox("Bad Input", f"TX power must be an integer dBm value.")
            return

        confirm_text = (
            f"Push TX power = {new_tx} dBm to the radio?\n\n"
            f"  Current:        {cur_tx} dBm\n"
            f"  Radio max:      {max_tx} dBm\n"
            f"  Current freq:   {self._fmt_freq(cur_freq)}\n"
        )
        if warn:
            confirm_text += f"\n{warn}\n"
        if not self.ctx.dialog.yesno("Confirm TX Power Write", confirm_text, default_no=True):
            return
        if not self.ctx.dialog.yesno(
            "Really Write?",
            "Final check — actually PUT this TX power to the radio?",
            default_no=True,
        ):
            return

        result = self._radio_put("tx_power", {"value": new_tx})
        self._show_write_result("TX Power", result)

    def _meshcore_set_channel(self):
        snap = self._radio_fetch_state(refresh=False)
        if not snap.get("ok"):
            self.ctx.dialog.msgbox(
                "Daemon Unreachable",
                f"Couldn't read current radio state: {snap.get('error')}",
            )
            return
        state = snap.get("radio") or {}
        max_ch = state.get("max_channels")
        channels = state.get("channels") or []

        # Build slot picker — show occupied slots labelled, free slots numbered.
        max_label = max_ch if max_ch is not None else 32
        try:
            slot_count = int(max_label)
        except (TypeError, ValueError):
            slot_count = 32
        occupied = {int(c.get("idx", -1)): c for c in channels if isinstance(c, dict)}
        slot_choices = []
        for i in range(slot_count):
            ch = occupied.get(i)
            if ch:
                desc = f"[{i}] {ch.get('name', '(unnamed)'):<20} hash={ch.get('hash', '??')}"
            else:
                desc = f"[{i}] (empty)"
            slot_choices.append((str(i), desc))

        idx_str = self.ctx.dialog.menu(
            "Select Channel Slot",
            f"Slots 0..{slot_count - 1}. Writes overwrite existing slots.",
            slot_choices,
        )
        if idx_str is None:
            return
        try:
            idx = int(idx_str)
        except ValueError:
            return

        cur = occupied.get(idx)
        cur_name = cur.get("name", "") if cur else ""

        new_name = self.ctx.dialog.inputbox(
            f"Channel Slot [{idx}] Name",
            "Channel name. Prefix with # to auto-derive the secret as "
            "sha256(name)[:16] (matches meshcore_py).",
            init=cur_name,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            self.ctx.dialog.msgbox("Bad Input", "Channel name cannot be empty.")
            return

        secret_hex = self.ctx.dialog.inputbox(
            "Channel Secret (hex, optional)",
            "Leave blank to auto-derive from a #-prefixed name. "
            "Otherwise provide 32 hex chars (16 bytes).",
            init="",
        )
        if secret_hex is None:
            return
        secret_hex = secret_hex.strip() or None

        confirm_text = (
            f"Push channel slot [{idx}] to the radio?\n\n"
            f"  Name:    {new_name}\n"
            f"  Secret:  "
            + ("(auto: sha256(name)[:16])" if not secret_hex else "(user-provided)")
            + "\n"
        )
        if not secret_hex and not new_name.startswith("#"):
            confirm_text += (
                "\nNOTE: name has no '#' prefix — daemon will reject this without "
                "an explicit secret.\n"
            )
        if cur:
            confirm_text += f"\nThis OVERWRITES the existing slot ({cur.get('name')}).\n"

        if not self.ctx.dialog.yesno("Confirm Channel Write", confirm_text, default_no=True):
            return
        if not self.ctx.dialog.yesno(
            "Really Write?",
            "Final check — actually PUT this channel to the radio?",
            default_no=True,
        ):
            return

        body = {"name": new_name}
        if secret_hex:
            body["secret"] = secret_hex
        result = self._radio_put(f"channel/{idx}", body)
        self._show_write_result(f"Channel [{idx}]", result)

    def _show_write_result(self, label: str, result: dict) -> None:
        if result.get("ok"):
            radio = result.get("radio") or {}
            note = radio.get("error")
            msg = f"{label} write accepted.\n\n"
            if note:
                msg += f"Daemon note: {note}\n\n"
            msg += "Use 'View' to confirm the radio reports the new value."
            self.ctx.dialog.msgbox(f"{label} — Done", msg)
        else:
            err = result.get("error") or "unknown error"
            status = result.get("status")
            self.ctx.dialog.msgbox(
                f"{label} — Failed",
                f"HTTP {status if status is not None else 'n/a'}: {err}",
            )

    @staticmethod
    def _region_warning_for_freq(freq) -> str:
        """Return a human-readable region note for the chosen freq, or empty."""
        try:
            from gateway.meshcore_radio_config import region_for_freq
        except ImportError:
            return ""
        try:
            band = region_for_freq(float(freq))
        except (TypeError, ValueError):
            return ""
        if band is None:
            return (
                f"NOTE: {freq} MHz isn't in any known regional band — verify it "
                "is legal where you operate."
            )
        return f"Region: {band.label} (TX cap = {band.max_tx_dbm} dBm — {band.source})"

    @staticmethod
    def _region_tx_warning(freq) -> str:
        """Return the region cap line for a TX-power write context."""
        try:
            from gateway.meshcore_radio_config import region_for_freq
        except ImportError:
            return ""
        try:
            band = region_for_freq(float(freq))
        except (TypeError, ValueError):
            return ""
        if band is None:
            return f"NOTE: {freq} MHz isn't in any known regional band."
        return f"Region: {band.label} caps TX at {band.max_tx_dbm} dBm ({band.source})."

    @staticmethod
    def _fmt_freq(freq) -> str:
        if freq is None:
            return "? MHz"
        return f"{float(freq):.3f} MHz"

    @staticmethod
    def _fmt_bw(bw) -> str:
        if bw is None:
            return "? kHz"
        return f"{float(bw):g} kHz"

    @staticmethod
    def _radio_preset_name(freq, bw, sf, cr):
        """Map well-known (freq, bw, sf, cr) tuples to MeshCore preset names.

        Returns None when no match — Phase 4a stays conservative; users can
        always read the four numbers above. List can grow when the upstream
        preset table evolves.
        """
        if None in (freq, bw, sf, cr):
            return None
        try:
            key = (round(float(freq), 3), round(float(bw), 1), int(sf), int(cr))
        except (TypeError, ValueError):
            return None
        # Names follow common MeshCore convention; tolerant ±0.5 MHz / ±5 kHz
        # match would help with rounding but exact-match is fine for v1.
        table = {
            (869.525, 250.0, 11, 5): "EU 869 MHz (Default LF)",
            (915.000, 250.0, 11, 5): "US 915 MHz (Default LF)",
            (915.000, 250.0, 10, 5): "US 915 MHz (MediumFast)",
            (433.000, 250.0, 11, 5): "433 MHz (Default LF)",
        }
        return table.get(key)
