"""
MeshCore Handler — MeshCore companion radio management.

Converted from meshcore_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

_detect_meshcore_devices, _HAS_DETECT = safe_import(
    'gateway.meshcore_handler', 'detect_meshcore_devices'
)
_GatewayConfig, _MeshCoreConfig, _HAS_GW_CONFIG = safe_import(
    'gateway.config', 'GatewayConfig', 'MeshCoreConfig'
)
_get_node_tracker, _HAS_NODE_TRACKER = safe_import(
    'gateway.node_tracker', 'get_node_tracker'
)
_is_gateway_running, _get_gateway_stats, _HAS_GW_CLI = safe_import(
    'gateway.gateway_cli', 'is_gateway_running', 'get_gateway_stats'
)


class MeshCoreHandler(BaseHandler):
    """TUI handler for MeshCore companion radio management."""

    handler_id = "meshcore"
    menu_section = "meshcore"

    def menu_items(self):
        return [
            ("meshcore", "MeshCore            Companion radio, config", "meshcore"),
        ]

    def execute(self, action):
        if action == "meshcore":
            self._meshcore_menu()

    def _meshcore_menu(self):
        """MeshCore companion radio setup and monitoring."""
        while True:
            status_line = self._meshcore_status_line()

            choices = [
                ("status", "Connection Status   MeshCore radio state"),
                ("detect", "Detect Devices      Scan for serial devices"),
                ("config", "Configure           Connection settings"),
                ("radio", "Radio Config        LoRa params, channels, TX power"),
                ("enable", "Enable/Disable      Toggle MeshCore in gateway"),
                ("nodes", "View Nodes          MeshCore network nodes"),
                ("stats", "Statistics          Message & connection stats"),
                ("peek", "Peek Chat           Last 50 messages (one-shot)"),
                ("daemon", "Daemon Control      Status / start / stop / journal"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshCore Radio",
                status_line,
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("MeshCore Status", self._meshcore_status),
                "detect": ("Detect Devices", self._meshcore_detect),
                "config": ("MeshCore Config", self._meshcore_configure),
                "radio": ("MeshCore Radio Config", self._meshcore_radio_menu),
                "enable": ("Enable/Disable", self._meshcore_toggle),
                "nodes": ("MeshCore Nodes", self._meshcore_nodes),
                "stats": ("MeshCore Stats", self._meshcore_stats),
                "peek": ("Recent MeshCore Chat", self._chat_view_recent),
                "daemon": ("Daemon Control", self._meshcore_daemon_control),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _meshcore_status_line(self) -> str:
        """Build status line for MeshCore menu subtitle."""
        if not _HAS_GW_CONFIG:
            return "MeshCore companion radio management"

        try:
            config = _GatewayConfig.load()
            mc = getattr(config, 'meshcore', None)
            if not mc or not mc.enabled:
                return "MeshCore: DISABLED in gateway config"
            conn = mc.connection_type
            device = mc.device_path if conn == "serial" else f"{mc.tcp_host}:{mc.tcp_port}"
            return f"MeshCore: ENABLED ({conn} -> {device})"
        except Exception:
            return "MeshCore companion radio management"

    def _meshcore_status(self):
        """Show MeshCore connection status."""
        clear_screen()
        print("=== MeshCore Connection Status ===\n")

        if not _HAS_GW_CONFIG:
            print("  Gateway config module not available.")
            self.ctx.wait_for_enter()
            return

        try:
            config = _GatewayConfig.load()
        except Exception as e:
            print(f"  Could not load gateway config: {e}")
            self.ctx.wait_for_enter()
            return

        mc = getattr(config, 'meshcore', None)
        if not mc:
            print("  MeshCore not configured.")
            print("  Use 'Configure' to set up connection.")
            self.ctx.wait_for_enter()
            return

        print(f"  Enabled:          {'Yes' if mc.enabled else 'No'}")
        print(f"  Connection Type:  {mc.connection_type}")
        if mc.connection_type == "serial":
            print(f"  Device Path:      {mc.device_path}")
            print(f"  Baud Rate:        {mc.baud_rate}")

            import os
            exists = os.path.exists(mc.device_path)
            print(f"  Device Present:   {'Yes' if exists else 'No (not plugged in?)'}")
        elif mc.connection_type == "tcp":
            print(f"  TCP Host:         {mc.tcp_host}")
            print(f"  TCP Port:         {mc.tcp_port}")
        print(f"  Bridge Channels:  {'Yes' if mc.bridge_channels else 'No'}")
        print(f"  Bridge DMs:       {'Yes' if mc.bridge_dms else 'No'}")
        print(f"  Simulation Mode:  {'Yes' if mc.simulation_mode else 'No'}")
        print(f"  Auto-Fetch Msgs:  {'Yes' if mc.auto_fetch_messages else 'No'}")

        try:
            import meshcore as _mc_check  # noqa: F401
            print(f"\n  meshcore_py:      Installed")
        except ImportError:
            print(f"\n  meshcore_py:      NOT installed")
            print(f"  Install:          pip install meshcore")

        self.ctx.wait_for_enter()

    def _meshcore_detect(self):
        """Scan for MeshCore-compatible serial devices."""
        clear_screen()
        print("=== MeshCore Device Detection ===\n")

        if not _HAS_DETECT:
            print("  Device detection module not available.")
            self.ctx.wait_for_enter()
            return

        devices = _detect_meshcore_devices()

        if not devices:
            print("  No serial devices found.")
            print("\n  Check:")
            print("  - Is the radio plugged in via USB?")
            print("  - Does it show up with: ls /dev/ttyUSB* /dev/ttyACM*")
            print("  - Is the user in the 'dialout' group?")
            self.ctx.wait_for_enter()
            return

        print(f"  Found {len(devices)} serial device(s):\n")
        for i, dev in enumerate(devices, 1):
            print(f"  {i}. {dev}")

        print("\n  Note: These are serial ports that MAY be MeshCore radios.")
        print("  Verify by connecting and checking firmware response.")

        if _HAS_GW_CONFIG and len(devices) >= 1:
            print(f"\n  Set {devices[0]} as MeshCore device? (Configure menu)")

        self.ctx.wait_for_enter()

    def _meshcore_configure(self):
        """Configure MeshCore connection settings."""
        if not _HAS_GW_CONFIG:
            self.ctx.dialog.msgbox(
                "Module Missing",
                "Gateway configuration module not found.\n\n"
                "Ensure src/gateway/config.py exists."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            config = _GatewayConfig()

        mc = getattr(config, 'meshcore', None)
        if mc is None:
            mc = _MeshCoreConfig()
            config.meshcore = mc

        while True:
            choices = [
                ("type", f"Connection Type     {mc.connection_type}"),
                ("device", f"Device Path         {mc.device_path}"),
                ("baud", f"Baud Rate           {mc.baud_rate}"),
                ("tcp_host", f"TCP Host            {mc.tcp_host or '(not set)'}"),
                ("tcp_port", f"TCP Port            {mc.tcp_port}"),
                ("channels", f"Bridge Channels     {'Yes' if mc.bridge_channels else 'No'}"),
                ("dms", f"Bridge DMs          {'Yes' if mc.bridge_dms else 'No'}"),
                ("sim", f"Simulation Mode     {'Yes' if mc.simulation_mode else 'No'}"),
                ("save", "Save Configuration"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshCore Configuration",
                "Configure MeshCore companion radio connection:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "type":
                type_choice = self.ctx.dialog.menu(
                    "Connection Type",
                    "How is the MeshCore radio connected?",
                    [
                        ("serial", "USB Serial          Direct USB connection"),
                        ("tcp", "TCP                 Network connection"),
                        ("ble", "Bluetooth LE        BLE connection"),
                    ]
                )
                if type_choice:
                    mc.connection_type = type_choice

            elif choice == "device":
                devices = []
                if _HAS_DETECT:
                    devices = _detect_meshcore_devices()

                if devices:
                    dev_choices = [(d, d) for d in devices]
                    dev_choices.append(("custom", "Enter custom path"))
                    selected = self.ctx.dialog.menu(
                        "Select Device",
                        "Detected serial devices:",
                        dev_choices
                    )
                    if selected and selected != "custom":
                        mc.device_path = selected
                    elif selected == "custom":
                        path = self.ctx.dialog.inputbox(
                            "Device Path",
                            "Enter serial device path:",
                            mc.device_path
                        )
                        if path:
                            mc.device_path = path
                else:
                    path = self.ctx.dialog.inputbox(
                        "Device Path",
                        "No devices detected. Enter path manually:",
                        mc.device_path
                    )
                    if path:
                        mc.device_path = path

            elif choice == "baud":
                baud = self.ctx.dialog.inputbox(
                    "Baud Rate",
                    "Enter baud rate (typically 115200):",
                    str(mc.baud_rate)
                )
                if baud:
                    try:
                        mc.baud_rate = int(baud)
                    except ValueError:
                        self.ctx.dialog.msgbox("Invalid Input", "Baud rate must be a number.")

            elif choice == "tcp_host":
                host = self.ctx.dialog.inputbox(
                    "TCP Host",
                    "Enter TCP host for MeshCore connection:",
                    mc.tcp_host or "localhost"
                )
                if host and self.ctx.validate_hostname(host):
                    mc.tcp_host = host
                elif host:
                    self.ctx.dialog.msgbox("Invalid Host", "Invalid hostname or IP address.")

            elif choice == "tcp_port":
                port = self.ctx.dialog.inputbox(
                    "TCP Port",
                    "Enter TCP port (default 4000):",
                    str(mc.tcp_port)
                )
                if port and self.ctx.validate_port(port):
                    mc.tcp_port = int(port)
                elif port:
                    self.ctx.dialog.msgbox("Invalid Port", "Port must be 1-65535.")

            elif choice == "channels":
                mc.bridge_channels = not mc.bridge_channels

            elif choice == "dms":
                mc.bridge_dms = not mc.bridge_dms

            elif choice == "sim":
                mc.simulation_mode = not mc.simulation_mode

            elif choice == "save":
                try:
                    config.save()
                    self.ctx.dialog.msgbox(
                        "Saved",
                        "MeshCore configuration saved.\n\n"
                        "Restart the gateway bridge for changes to take effect."
                    )
                except Exception as e:
                    self.ctx.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")

    def _meshcore_toggle(self):
        """Enable or disable MeshCore in gateway config."""
        if not _HAS_GW_CONFIG:
            self.ctx.dialog.msgbox(
                "Module Missing",
                "Gateway configuration module not found."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            config = _GatewayConfig()

        mc = getattr(config, 'meshcore', None)
        if mc is None:
            mc = _MeshCoreConfig()
            config.meshcore = mc

        mc.enabled = not mc.enabled
        action = "enabled" if mc.enabled else "disabled"

        try:
            config.save()
            self.ctx.dialog.msgbox(
                f"MeshCore {action.title()}",
                f"MeshCore is now {action}.\n\n"
                f"Restart the gateway bridge for changes to take effect."
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")

    def _meshcore_nodes(self):
        """Show MeshCore nodes from the live node tracker."""
        clear_screen()
        print("=== MeshCore Nodes ===\n")

        if not _HAS_NODE_TRACKER:
            print("  Node tracker module not available.")
            self.ctx.wait_for_enter()
            return

        try:
            tracker = _get_node_tracker()
            nodes = tracker.get_meshcore_nodes()
        except Exception as e:
            print(f"  Error reading node tracker: {e}")
            self.ctx.wait_for_enter()
            return

        if not nodes:
            print("  No MeshCore nodes discovered yet.\n")
            print("  Nodes appear when the gateway bridge is running")
            print("  with MeshCore enabled and a radio connected.")
            self.ctx.wait_for_enter()
            return

        online = [n for n in nodes if n.is_online]
        offline = [n for n in nodes if not n.is_online]
        print(f"  {len(nodes)} node(s) discovered "
              f"({len(online)} online, {len(offline)} offline):\n")

        for node in sorted(nodes, key=lambda n: (not n.is_online, n.name or n.id)):
            name = node.name or node.short_name or "(unnamed)"
            status = "ONLINE" if node.is_online else "offline"
            role = node.meshcore_role or ""
            hops = f"hops:{node.meshcore_hops}" if node.meshcore_hops is not None else ""

            signal = ""
            if node.rssi is not None:
                signal = f"RSSI:{node.rssi}"
            if node.snr is not None:
                signal += f" SNR:{node.snr:.1f}"

            last = ""
            if node.last_seen:
                from datetime import datetime as dt
                delta = (dt.now() - node.last_seen).total_seconds()
                if delta < 60:
                    last = f"{int(delta)}s ago"
                elif delta < 3600:
                    last = f"{int(delta / 60)}m ago"
                else:
                    last = f"{delta / 3600:.1f}h ago"

            detail = "  ".join(filter(None, [role, hops, signal, last]))
            print(f"  {name:<20s} [{status}]  {detail}")
            if node.meshcore_pubkey:
                print(f"    pubkey: {node.meshcore_pubkey}")

        self.ctx.wait_for_enter()

    def _meshcore_stats(self):
        """Show MeshCore statistics from the live bridge."""
        clear_screen()
        print("=== MeshCore Statistics ===\n")

        if not _HAS_GW_CLI:
            print("  Gateway CLI module not available.")
            self.ctx.wait_for_enter()
            return

        if not _is_gateway_running():
            print("  Gateway bridge is not running.\n")
            print("  Start the bridge to collect MeshCore statistics.")
            self.ctx.wait_for_enter()
            return

        try:
            gw_stats = _get_gateway_stats()
        except Exception as e:
            print(f"  Error reading gateway stats: {e}")
            self.ctx.wait_for_enter()
            return

        stats = gw_stats.get('statistics', gw_stats)
        connected = gw_stats.get('meshcore_connected', False)

        print(f"  Connection:  {'CONNECTED' if connected else 'DISCONNECTED'}")
        print(f"  Bridge:      {gw_stats.get('status', 'unknown')}\n")

        print(f"  Messages RX:    {stats.get('meshcore_rx', 0)}")
        print(f"  Messages TX:    {stats.get('meshcore_tx', 0)}")
        print(f"  Delivery ACKs:  {stats.get('meshcore_acks', 0)}")

        mc_to_mesh = stats.get('messages_meshcore_to_mesh', 0)
        mc_to_rns = stats.get('messages_meshcore_to_rns', 0)
        mesh_to_mc = stats.get('messages_mesh_to_meshcore', 0)
        rns_to_mc = stats.get('messages_rns_to_meshcore', 0)
        if any([mc_to_mesh, mc_to_rns, mesh_to_mc, rns_to_mc]):
            print(f"\n  Bridged:")
            print(f"    MeshCore -> Meshtastic:  {mc_to_mesh}")
            print(f"    MeshCore -> RNS:         {mc_to_rns}")
            print(f"    Meshtastic -> MeshCore:  {mesh_to_mc}")
            print(f"    RNS -> MeshCore:         {rns_to_mc}")

        errors = stats.get('errors', 0)
        bounced = stats.get('bounced', 0)
        if errors or bounced:
            print(f"\n  Errors:   {errors}")
            print(f"  Bounced:  {bounced}")

        uptime = gw_stats.get('uptime_seconds')
        if uptime:
            h, rem = divmod(int(uptime), 3600)
            m, s = divmod(rem, 60)
            print(f"\n  Uptime: {h}h {m}m {s}s")

        self.ctx.wait_for_enter()

    # ─────────────────────────────────────────────────────────────────
    # Daemon HTTP API — chat + radio config both share the :8081 base.
    # The daemon owns p4's serial port; the TUI runs in a separate
    # process and can't open it directly. The daemon's MeshCoreHandler
    # mirrors RX + TX into a ring buffer (chat) and a RadioState cache
    # (radio config) that these menus read.
    # ─────────────────────────────────────────────────────────────────

    CHAT_API_BASE = "http://127.0.0.1:8081"

    def _meshcore_radio_menu(self):
        """Phase 4b: Radio Config sub-submenu (view + writes)."""
        while True:
            choices = [
                ("view", "View                Current LoRa / channels / TX power"),
                ("lora", "Set LoRa Params     Frequency / bandwidth / SF / coding rate"),
                ("txp", "Set TX Power        Region-aware cap enforced"),
                ("channel", "Set Channel Slot    Name + secret per slot"),
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
                "lora": ("Set LoRa Parameters", self._meshcore_set_lora),
                "txp": ("Set TX Power", self._meshcore_set_tx_power),
                "channel": ("Set Channel Slot", self._meshcore_set_channel),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

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

    # Live bidirectional chat lives in the tmux pane (ChatPaneHandler →
    # utils/chat_client.py). The methods below are a one-shot peek: render
    # the daemon's last 50 chat-ring-buffer entries and return. Everything
    # else (sending, live tail, slash commands) is the tmux client's job.

    def _chat_api_reachable(self) -> bool:
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.CHAT_API_BASE}/chat/messages?since=0",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2):
                return True
        except urllib.error.HTTPError as e:
            # 503 means daemon is up but MeshCore not active — still
            # "reachable" enough that the menu is useful for diagnosis.
            return e.code in (503,)
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def _chat_fetch_messages(self, since_id: int = 0):
        import json as _json
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.CHAT_API_BASE}/chat/messages?since={since_id}",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return _json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"error": f"connection: {e}"}

    def _chat_format_entry(self, entry: dict) -> str:
        from datetime import datetime as _dt
        ts = entry.get("ts", 0)
        ts_str = _dt.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"
        direction = entry.get("direction", "?")
        arrow = "<<" if direction == "rx" else ">>"
        chan = entry.get("channel")
        chan_str = f"CHAN{chan}" if chan is not None else "DM"
        sender = entry.get("sender") or entry.get("destination") or "?"
        text = entry.get("text", "")
        return f"[{ts_str}] {arrow} {chan_str:<6} {str(sender)[:12]:<12} {text}"

    def _chat_view_recent(self):
        clear_screen()
        print("=== Recent MeshCore Chat ===\n")
        result = self._chat_fetch_messages(0)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            self.ctx.wait_for_enter()
            return
        messages = result.get("messages", [])
        if not messages:
            print("  (no messages yet)")
        for entry in messages[-50:]:
            print(f"  {self._chat_format_entry(entry)}")
        print(f"\n  Showing last {min(len(messages), 50)} of {len(messages)} entries.")
        self.ctx.wait_for_enter()

    # ─────────────────────────────────────────────────────────────────
    # Daemon control — meshanchor-daemon.service hosts the gateway
    # bridge, MeshCore handler, MQTT subscriber, config_api, etc.
    # Operators were dropping to a shell to manage it; bring the basics
    # into the TUI so service ops live alongside the radio menu.
    # ─────────────────────────────────────────────────────────────────

    DAEMON_SERVICE = "meshanchor-daemon.service"

    def _meshcore_daemon_control(self):
        while True:
            status_line = self._daemon_status_summary()
            choices = [
                ("status", "Service Status      systemctl is-active + show"),
                ("start", "Start               sudo systemctl start"),
                ("stop", "Stop                sudo systemctl stop (kills chat)"),
                ("restart", "Restart             sudo systemctl restart"),
                ("journal", "Journal (last 50)   journalctl -n 50"),
                ("tail", "Live Tail           journalctl -f (Ctrl-C to stop)"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "Daemon Control",
                status_line,
                choices,
            )
            if choice is None or choice == "back":
                return
            dispatch = {
                "status": self._daemon_show_status,
                "start": self._daemon_start,
                "stop": self._daemon_stop,
                "restart": self._daemon_restart,
                "journal": self._daemon_journal_recent,
                "tail": self._daemon_journal_tail,
            }
            fn = dispatch.get(choice)
            if fn:
                fn()

    def _daemon_status_summary(self) -> str:
        """One-line status for the menu subtitle. is-active is fast and
        works without sudo, so we use it instead of a full status dump."""
        import subprocess
        try:
            result = subprocess.run(
                ["systemctl", "is-active", self.DAEMON_SERVICE],
                capture_output=True, text=True, timeout=5,
            )
            state = result.stdout.strip() or result.stderr.strip() or "unknown"
        except Exception as e:
            state = f"error: {e}"
        return f"{self.DAEMON_SERVICE}: {state}"

    def _daemon_show_status(self):
        import subprocess
        clear_screen()
        print(f"=== {self.DAEMON_SERVICE} status ===\n")
        try:
            result = subprocess.run(
                ["systemctl", "status", self.DAEMON_SERVICE,
                 "--no-pager", "--lines", "10"],
                capture_output=True, text=True, timeout=10,
            )
            # systemctl status returns nonzero when the service is inactive
            # but stdout still has useful info; print regardless.
            output = result.stdout.strip() or result.stderr.strip()
            print(output or "  (no output)")
        except FileNotFoundError:
            print("  systemctl not found (not a systemd box?)")
        except subprocess.TimeoutExpired:
            print("  systemctl timed out")
        except Exception as e:
            print(f"  Error: {e}")
        self.ctx.wait_for_enter()

    def _daemon_start(self):
        self._daemon_run_action("start", confirm=False)

    def _daemon_stop(self):
        # Stopping the daemon kills the chat API and the gateway bridge.
        # Don't surprise the operator with that.
        confirmed = self.ctx.dialog.yesno(
            "Stop Daemon",
            f"Stopping {self.DAEMON_SERVICE} kills the chat API, gateway "
            f"bridge, and node tracker until restarted. Continue?",
        )
        if not confirmed:
            return
        self._daemon_run_action("stop", confirm=False)

    def _daemon_restart(self):
        self._daemon_run_action("restart", confirm=False)

    def _daemon_run_action(self, action: str, confirm: bool = False):
        try:
            from utils.service_check import (
                start_service, stop_service, restart_service,
            )
        except ImportError as e:
            self.ctx.dialog.msgbox(
                "Daemon Control",
                f"service_check helpers unavailable: {e}",
            )
            return

        clear_screen()
        print(f"=== Daemon {action} ===\n")
        fn = {
            "start": start_service, "stop": stop_service,
            "restart": restart_service,
        }[action]
        try:
            ok, msg = fn(self.DAEMON_SERVICE)
        except Exception as e:
            ok, msg = False, str(e)
        marker = "OK" if ok else "FAILED"
        print(f"  [{marker}] {msg}")
        # Re-show is-active so the operator gets confirmation without
        # navigating back to the menu.
        print(f"\n  {self._daemon_status_summary()}")
        self.ctx.wait_for_enter()

    def _daemon_journal_recent(self):
        import subprocess
        clear_screen()
        print(f"=== {self.DAEMON_SERVICE} — last 50 ===\n")
        try:
            result = subprocess.run(
                ["journalctl", "-u", self.DAEMON_SERVICE,
                 "-n", "50", "--no-pager"],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stdout.strip()
            print(output or "  (no journal entries)")
        except FileNotFoundError:
            print("  journalctl not found (not a systemd box?)")
        except subprocess.TimeoutExpired:
            print("  journalctl timed out")
        except Exception as e:
            print(f"  Error: {e}")
        self.ctx.wait_for_enter()

    def _daemon_journal_tail(self):
        import subprocess
        clear_screen()
        print(f"=== {self.DAEMON_SERVICE} — live tail (Ctrl-C to stop) ===\n")
        try:
            # Stream until interrupted; subprocess.run inherits the
            # terminal so Ctrl-C reaches journalctl directly.
            subprocess.run(
                ["journalctl", "-u", self.DAEMON_SERVICE,
                 "-f", "-n", "20", "--no-pager"],
                timeout=None,
            )
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            print("  journalctl not found (not a systemd box?)")
            self.ctx.wait_for_enter()
        except Exception as e:
            print(f"  Error: {e}")
            self.ctx.wait_for_enter()
