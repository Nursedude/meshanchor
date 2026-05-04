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
                ("enable", "Enable/Disable      Toggle MeshCore in gateway"),
                ("nodes", "View Nodes          MeshCore network nodes"),
                ("stats", "Statistics          Message & connection stats"),
                ("chat", "Chat                Send / view messages via daemon"),
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
                "enable": ("Enable/Disable", self._meshcore_toggle),
                "nodes": ("MeshCore Nodes", self._meshcore_nodes),
                "stats": ("MeshCore Stats", self._meshcore_stats),
                "chat": ("MeshCore Chat", self._meshcore_chat),
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
    # Chat — talks to daemon's /chat/* HTTP API on :8081
    #
    # The daemon owns p4's serial port; the TUI runs in a separate
    # process and can't open it directly. The daemon's MeshCoreHandler
    # mirrors RX + TX into a ring buffer that this menu reads/writes.
    # ─────────────────────────────────────────────────────────────────

    CHAT_API_BASE = "http://127.0.0.1:8081"
    CHAT_POLL_INTERVAL = 2.0

    def _meshcore_chat(self):
        """Interactive chat menu against the daemon's chat API."""
        clear_screen()
        print("=== MeshCore Chat ===\n")

        if not self._chat_api_reachable():
            print("  Daemon's chat API on :8081 is not reachable.")
            print("  Start the daemon: sudo systemctl start meshanchor-daemon.service")
            self.ctx.wait_for_enter()
            return

        while True:
            choices = [
                ("recent", "View Recent Messages   Last ~200 entries"),
                ("send_chan", "Send to Channel       Broadcast on slot N"),
                ("send_dm", "Send DM               Direct-message a node"),
                ("watch", "Watch (poll every 2s)  Live tail until Ctrl-C"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "MeshCore Chat",
                "Daemon-mediated chat through p4 (via :8081)",
                choices,
            )
            if choice is None or choice == "back":
                return
            if choice == "recent":
                self._chat_view_recent()
            elif choice == "send_chan":
                self._chat_send_channel()
            elif choice == "send_dm":
                self._chat_send_dm()
            elif choice == "watch":
                self._chat_watch_tail()

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

    def _chat_post_send(self, text: str, channel: int = 0,
                        destination=None) -> dict:
        import json as _json
        import urllib.error
        import urllib.request
        body = _json.dumps({
            "text": text, "channel": channel, "destination": destination,
        }).encode()
        try:
            req = urllib.request.Request(
                f"{self.CHAT_API_BASE}/chat/send",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
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

    def _chat_send_channel(self):
        slot = self.ctx.dialog.inputbox(
            "Send to Channel",
            "Channel slot (0=Public, 1/2=meshanchor on p4):",
            "1",
        )
        if not slot:
            return
        try:
            slot_int = int(slot.strip())
        except ValueError:
            self.ctx.dialog.msgbox("Send to Channel", "Slot must be an integer.")
            return
        text = self.ctx.dialog.inputbox(
            "Send to Channel",
            f"Message text (broadcast on slot {slot_int}):",
            "",
        )
        if not text:
            return
        result = self._chat_post_send(text, channel=slot_int)
        if "error" in result:
            self.ctx.dialog.msgbox("Send Result", f"FAILED: {result['error']}")
        else:
            self.ctx.dialog.msgbox(
                "Send Result",
                f"Queued on CHAN{slot_int}: {text[:60]}",
            )

    def _chat_send_dm(self):
        dest = self.ctx.dialog.inputbox(
            "Send DM",
            "Destination (pubkey-prefix hex or contact name):",
            "",
        )
        if not dest:
            return
        text = self.ctx.dialog.inputbox(
            "Send DM",
            f"Message text (DM to {dest}):",
            "",
        )
        if not text:
            return
        result = self._chat_post_send(text, destination=dest.strip())
        if "error" in result:
            self.ctx.dialog.msgbox("Send Result", f"FAILED: {result['error']}")
        else:
            self.ctx.dialog.msgbox(
                "Send Result",
                f"Queued DM to {dest}: {text[:60]}",
            )

    def _chat_watch_tail(self):
        """Live-tail messages until Ctrl-C."""
        import time
        clear_screen()
        print("=== Watching MeshCore Chat (Ctrl-C to stop) ===\n")
        last_id = 0
        try:
            while True:
                result = self._chat_fetch_messages(last_id)
                if "error" in result:
                    print(f"  ERROR: {result['error']}")
                    time.sleep(self.CHAT_POLL_INTERVAL)
                    continue
                for entry in result.get("messages", []):
                    print(f"  {self._chat_format_entry(entry)}")
                    last_id = max(last_id, entry.get("id", last_id))
                time.sleep(self.CHAT_POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n  (stopped)")
            self.ctx.wait_for_enter()

    # ─────────────────────────────────────────────────────────────────
    # Daemon control — meshanchor-daemon.service hosts the gateway
    # bridge, MeshCore handler, MQTT subscriber, config_api, etc.
    # Operators were dropping to a shell to manage it; bring the basics
    # into the TUI so the chat menu and the daemon controls live in
    # one place.
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
