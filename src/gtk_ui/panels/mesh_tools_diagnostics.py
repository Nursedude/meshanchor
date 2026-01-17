"""
Diagnostics Tab Mixin - Extracted from mesh_tools.py

Handles network diagnostics including:
- System health monitoring
- Network tests (ping, traceroute, DNS, ports)
- Diagnostic report generation
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import threading
import subprocess
from pathlib import Path
from datetime import datetime

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import path utilities
from utils.paths import get_real_user_home


class DiagnosticsTabMixin:
    """
    Mixin providing Diagnostics tab functionality.

    Requires parent class to provide:
    - self._notebook: Gtk.Notebook to add tab to
    - self._path_entry: Entry widget with meshbot path
    - self._log_message(str): Method to log messages
    - self._set_log_text(str): Method to set log text
    - self._open_folder(str): Method to open folder
    """

    def _add_diagnostics_tab(self):
        """Add Diagnostics tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Health status
        health_frame = Gtk.Frame()
        health_frame.set_label("System Health")
        health_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        health_box.set_margin_start(15)
        health_box.set_margin_end(15)
        health_box.set_margin_top(10)
        health_box.set_margin_bottom(10)

        # Health cards grid
        health_grid = Gtk.Grid()
        health_grid.set_column_spacing(15)
        health_grid.set_row_spacing(10)

        self._health_cards = {}
        checks = [
            ("meshtastic", "Meshtastic", "network-wireless-symbolic"),
            ("reticulum", "Reticulum", "network-transmit-receive-symbolic"),
            ("meshbot", "MeshBot", "mail-send-symbolic"),
            ("network", "Network", "network-wired-symbolic"),
        ]

        for i, (key, label, icon) in enumerate(checks):
            card = self._create_health_card(label, icon)
            health_grid.attach(card, i % 2, i // 2, 1, 1)
            self._health_cards[key] = card

        health_box.append(health_grid)

        # Refresh button
        refresh_health_btn = Gtk.Button(label="Run Health Check")
        refresh_health_btn.connect("clicked", self._on_run_health_check)
        health_box.append(refresh_health_btn)

        health_frame.set_child(health_box)
        box.append(health_frame)

        # Quick tests
        tests_frame = Gtk.Frame()
        tests_frame.set_label("Network Tests")
        tests_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        tests_box.set_margin_start(15)
        tests_box.set_margin_end(15)
        tests_box.set_margin_top(10)
        tests_box.set_margin_bottom(10)

        tests_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        ping_btn = Gtk.Button(label="Ping Test")
        ping_btn.connect("clicked", self._on_ping_test)
        tests_row.append(ping_btn)

        traceroute_btn = Gtk.Button(label="Traceroute")
        traceroute_btn.connect("clicked", self._on_traceroute_test)
        tests_row.append(traceroute_btn)

        dns_btn = Gtk.Button(label="DNS Check")
        dns_btn.connect("clicked", self._on_dns_test)
        tests_row.append(dns_btn)

        port_btn = Gtk.Button(label="Port Scan")
        port_btn.connect("clicked", self._on_port_scan)
        tests_row.append(port_btn)

        tests_box.append(tests_row)
        tests_frame.set_child(tests_box)
        box.append(tests_frame)

        # Report generation
        report_frame = Gtk.Frame()
        report_frame.set_label("Diagnostic Reports")
        report_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        report_box.set_margin_start(15)
        report_box.set_margin_end(15)
        report_box.set_margin_top(10)
        report_box.set_margin_bottom(10)

        gen_report_btn = Gtk.Button(label="Generate Full Report")
        gen_report_btn.add_css_class("suggested-action")
        gen_report_btn.connect("clicked", self._on_generate_report)
        report_box.append(gen_report_btn)

        open_reports_btn = Gtk.Button(label="Open Reports Folder")
        open_reports_btn.connect("clicked", self._on_open_reports_folder)
        report_box.append(open_reports_btn)

        report_frame.set_child(report_box)
        box.append(report_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        tab_box.append(Gtk.Label(label="Diagnostics"))

        self._notebook.append_page(scrolled, tab_box)

    def _create_health_card(self, label: str, icon_name: str) -> Gtk.Box:
        """Create a health status card"""
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.add_css_class("card")
        card.set_margin_start(10)
        card.set_margin_end(10)
        card.set_margin_top(5)
        card.set_margin_bottom(5)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(24)
        card.append(icon)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=label)
        name.set_xalign(0)
        name.add_css_class("heading")
        info.append(name)

        status = Gtk.Label(label="Unknown")
        status.set_xalign(0)
        status.add_css_class("dim-label")
        status.set_name("status")
        info.append(status)

        card.append(info)

        # Store reference to status label
        card._status_label = status
        return card

    # =========================================================================
    # Health Check Handlers
    # =========================================================================

    def _update_health_cards(self):
        """Update health status cards"""
        for key, card in self._health_cards.items():
            card._status_label.set_label("Checking...")

        def check_health():
            results = {}

            # Check Meshtastic
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'meshtasticd'],
                    capture_output=True, timeout=5
                )
                results["meshtastic"] = "Running" if result.returncode == 0 else "Not Running"
            except Exception:
                results["meshtastic"] = "Unknown"

            # Check Reticulum
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, timeout=5
                )
                results["reticulum"] = "Running" if result.returncode == 0 else "Not Running"
            except Exception:
                results["reticulum"] = "Unknown"

            # Check MeshBot
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, timeout=5
                )
                results["meshbot"] = "Running" if result.returncode == 0 else "Not Running"
            except Exception:
                results["meshbot"] = "Unknown"

            # Check Network
            try:
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '2', '8.8.8.8'],
                    capture_output=True, timeout=5
                )
                results["network"] = "Connected" if result.returncode == 0 else "No Internet"
            except Exception:
                results["network"] = "Unknown"

            GLib.idle_add(self._apply_health_results, results)

        threading.Thread(target=check_health, daemon=True).start()

    def _apply_health_results(self, results):
        """Apply health check results to cards"""
        for key, status in results.items():
            if key in self._health_cards:
                card = self._health_cards[key]
                card._status_label.set_label(status)

                if "Running" in status or "Connected" in status:
                    card._status_label.remove_css_class("error")
                    card._status_label.add_css_class("success")
                elif "Not" in status or "No " in status:
                    card._status_label.remove_css_class("success")
                    card._status_label.add_css_class("error")

    def _on_run_health_check(self, button):
        """Run full health check"""
        self._log_message("Running health check...")
        self._update_health_cards()

    # =========================================================================
    # Network Test Handlers
    # =========================================================================

    def _on_ping_test(self, button):
        """Run ping test"""
        self._run_network_test("Ping Test", ['ping', '-c', '4', '8.8.8.8'])

    def _on_traceroute_test(self, button):
        """Run traceroute - try multiple tools"""
        self._log_message("Running route trace...")

        def do_trace():
            trace_cmds = [
                (['traceroute', '-m', '10', '8.8.8.8'], 'traceroute'),
                (['mtr', '-r', '-c', '3', '8.8.8.8'], 'mtr'),
                (['tracepath', '8.8.8.8'], 'tracepath'),
            ]

            for cmd, name in trace_cmds:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0 or result.stdout:
                        output = result.stdout if result.stdout else result.stderr
                        GLib.idle_add(self._log_message, f"\n=== {name} ===\n{output}")
                        return
                except FileNotFoundError:
                    continue
                except Exception as e:
                    GLib.idle_add(self._log_message, f"{name} error: {e}")
                    continue

            GLib.idle_add(self._log_message, "No trace tools found. Install: sudo apt install traceroute")

        threading.Thread(target=do_trace, daemon=True).start()

    def _on_dns_test(self, button):
        """Run DNS check - try multiple tools"""
        self._log_message("Running DNS Check...")

        def do_dns_test():
            import socket
            test_hosts = ['google.com', 'cloudflare.com', '1.1.1.1']
            results = []

            for host in test_hosts:
                try:
                    ip = socket.gethostbyname(host)
                    results.append(f"  {host} -> {ip}")
                except socket.gaierror as e:
                    results.append(f"  {host} -> FAILED ({e})")

            output = "=== DNS Resolution Test ===\n" + "\n".join(results)

            try:
                with open('/etc/resolv.conf', 'r') as f:
                    resolv = f.read()
                    nameservers = [line for line in resolv.split('\n') if line.startswith('nameserver')]
                    if nameservers:
                        output += "\n\n=== Nameservers ===\n" + "\n".join(nameservers)
            except Exception:
                pass

            GLib.idle_add(self._log_message, output)

        threading.Thread(target=do_dns_test, daemon=True).start()

    def _on_port_scan(self, button):
        """Run port scan on localhost"""
        self._run_network_test("Port Scan", ['ss', '-tuln'])

    def _run_network_test(self, name: str, cmd: list):
        """Run a network test and show output"""
        self._log_message(f"Running {name}...")

        def do_test():
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                output = result.stdout if result.stdout else result.stderr
                GLib.idle_add(self._log_message, f"\n=== {name} ===\n{output}")
            except subprocess.TimeoutExpired:
                GLib.idle_add(self._log_message, f"{name}: Timed out")
            except Exception as e:
                GLib.idle_add(self._log_message, f"{name} error: {e}")

        threading.Thread(target=do_test, daemon=True).start()

    # =========================================================================
    # Report Generation Handlers
    # =========================================================================

    def _on_generate_report(self, button):
        """Generate diagnostic report"""
        self._log_message("Generating diagnostic report...")

        def do_report():
            import socket

            report_lines = []
            report_lines.append("=" * 60)
            report_lines.append("MeshForge Diagnostic Report")
            report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            report_lines.append("=" * 60)

            # System info
            report_lines.append("\n[System Info]")
            try:
                uname = subprocess.run(['uname', '-a'], capture_output=True, text=True, timeout=5)
                report_lines.append(f"  {uname.stdout.strip()}")
            except Exception:
                pass

            # MeshBot status
            report_lines.append("\n[MeshBot Status]")
            meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
            if Path(meshbot_path).exists():
                report_lines.append(f"  Installed: Yes ({meshbot_path})")
                venv = Path(meshbot_path) / "venv"
                report_lines.append(f"  Venv: {'Yes' if venv.exists() else 'No'}")
            else:
                report_lines.append("  Installed: No")

            try:
                result = subprocess.run(['pgrep', '-f', 'mesh_bot.py'], capture_output=True, timeout=5)
                report_lines.append(f"  Running: {'Yes' if result.returncode == 0 else 'No'}")
            except Exception:
                pass

            # Network
            report_lines.append("\n[Network]")
            try:
                ip = socket.gethostbyname('google.com')
                report_lines.append(f"  DNS: Working ({ip})")
            except Exception:
                report_lines.append("  DNS: Failed")

            # Meshtasticd
            report_lines.append("\n[Meshtasticd]")
            try:
                result = subprocess.run(['systemctl', 'is-active', 'meshtasticd'], capture_output=True, text=True, timeout=5)
                report_lines.append(f"  Service: {result.stdout.strip()}")
            except Exception:
                report_lines.append("  Service: Unknown")

            # Check TCP port
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('localhost', 4403))
                report_lines.append(f"  TCP 4403: {'Open' if result == 0 else 'Closed'}")
            except Exception:
                pass
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            report_lines.append("\n" + "=" * 60)

            report_text = "\n".join(report_lines)
            GLib.idle_add(self._set_log_text, report_text)
            GLib.idle_add(self._log_message, "Report generated")

        threading.Thread(target=do_report, daemon=True).start()

    def _on_open_reports_folder(self, button):
        """Open reports folder"""
        reports_dir = get_real_user_home() / ".local" / "share" / "meshforge" / "diagnostics"
        reports_dir.mkdir(parents=True, exist_ok=True)
        self._open_folder(str(reports_dir))
