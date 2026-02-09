"""
Web Client Mixin for MeshForge Launcher TUI.

Provides meshtasticd web client related methods extracted from main launcher
to reduce file size and improve maintainability.

Includes:
- Open web client menu
- Browser launch with proper sudo handling
- URL display for network access
- SSL certificate help
"""

import logging
import os
import socket
import subprocess
import threading

logger = logging.getLogger(__name__)


class WebClientMixin:
    """Mixin providing meshtasticd web client tools for the TUI launcher."""

    def _open_web_client(self):
        """Show/open meshtasticd web client for full radio configuration."""
        import webbrowser

        # Get local IP for network access
        local_ip = "localhost"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError as e:
            logger.debug("Local IP detection failed: %s", e)

        web_url = f"https://{local_ip}:9443"
        localhost_url = "https://localhost:9443"

        # Check if web server is responding (try localhost first, then IP)
        port_ok = False
        for check_host in ["localhost", local_ip]:
            if check_host == local_ip and local_ip == "localhost":
                continue  # Skip duplicate check
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(2)
                    if sock.connect_ex((check_host, 9443)) == 0:
                        port_ok = True
                        break
                finally:
                    sock.close()
            except Exception as e:
                logger.debug(f"Socket check for web client ({check_host}): {e}")

        if not port_ok:
            # Web client not responding - show setup help
            self.dialog.msgbox(
                "Web Client Not Running",
                "meshtasticd Web Client is NOT responding.\n\n"
                "To enable the web interface:\n\n"
                "1. Start meshtasticd:\n"
                "   sudo systemctl start meshtasticd\n\n"
                "2. Check status:\n"
                "   sudo systemctl status meshtasticd\n\n"
                "3. View logs for errors:\n"
                "   sudo journalctl -u meshtasticd -f\n\n"
                "Note: Ensure config has Webserver section with Port: 9443"
            )
            return

        # Web client is running - show options
        while True:
            choices = [
                ("open", "Open in Browser      Launch in default browser"),
                ("url", "Show URLs            Copy to access from other devices"),
                ("ssl", "SSL Certificate      Fix warnings / generate cert"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Meshtastic Web Client",
                "Web Client is RUNNING on port 9443\n\n"
                "Full radio configuration via browser:\n"
                "  Config → LoRa      Region, Preset, TX Power\n"
                "  Config → Channels  PSK keys, channel names\n"
                "  Config → Device    Node name, role, position\n\n"
                "Also: messaging, node map, telemetry",
                choices,
                height=20, width=65
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "open": ("Launch Browser", lambda: self._launch_web_client_browser(localhost_url)),
                "url": ("Show URLs", lambda: self._show_web_client_urls(local_ip)),
                "ssl": ("SSL Help", lambda: self._show_ssl_certificate_help(local_ip)),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _launch_web_client_browser(self, url: str):
        """Launch meshtasticd web client in browser."""
        import webbrowser

        def do_open():
            try:
                # When running as root, use sudo -u to run as real user
                real_user = os.environ.get('SUDO_USER')
                if os.geteuid() == 0 and real_user:
                    subprocess.run(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
                else:
                    subprocess.run(
                        ['xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass

        threading.Thread(target=do_open, daemon=True).start()

        self.dialog.msgbox(
            "Browser Launched",
            f"Opening: {url}\n\n"
            "If this is your first time:\n"
            "1. Browser will show SSL certificate warning\n"
            "2. Click 'Advanced' → 'Proceed' to accept\n"
            "3. This is normal for self-signed certificates\n\n"
            "The certificate is generated by meshtasticd\n"
            "and is safe to accept for local access."
        )

    def _show_web_client_urls(self, local_ip: str):
        """Show web client URLs for copying."""
        self.dialog.msgbox(
            "Web Client URLs",
            f"Access from THIS device:\n"
            f"  https://localhost:9443\n\n"
            f"Access from OTHER devices on network:\n"
            f"  https://{local_ip}:9443\n\n"
            f"Mobile/tablet (same network):\n"
            f"  Use the IP address above\n\n"
            f"Note: All devices must accept the\n"
            f"SSL certificate on first access.\n\n"
            f"Port 9443 = HTTPS Web UI\n"
            f"Port 4403 = TCP API (CLI/SDK)"
        )

    def _show_ssl_certificate_help(self, local_ip: str):
        """Show SSL certificate menu with generation option."""
        try:
            from utils.ssl_cert import is_cert_installed
            cert_installed = is_cert_installed()
        except ImportError:
            cert_installed = False

        if cert_installed:
            status_line = "Trusted certificate: INSTALLED"
        else:
            status_line = "Trusted certificate: NOT installed (using meshtasticd default)"

        while True:
            choices = [
                ("generate", "Generate Trusted Cert   Eliminate browser warnings"),
                ("manual", "Manual Accept Help     Browser-specific instructions"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "SSL Certificate",
                f"{status_line}\n\n"
                "meshtasticd uses HTTPS with a self-signed certificate.\n"
                "This causes warnings in browsers and blocks lynx/curl.\n\n"
                "Generate a trusted certificate to fix this permanently.",
                choices,
                height=18, width=65
            )

            if choice is None or choice == "back":
                break

            if choice == "generate":
                self._generate_trusted_cert()
                # Refresh status
                try:
                    cert_installed = is_cert_installed()
                except Exception:
                    pass
            elif choice == "manual":
                self._show_manual_ssl_help()

    def _generate_trusted_cert(self):
        """Generate and install a trusted localhost SSL certificate."""
        try:
            from utils.ssl_cert import generate_localhost_cert
        except ImportError:
            self.dialog.msgbox(
                "Error",
                "SSL certificate module not available.\n"
                "Ensure src/utils/ssl_cert.py exists."
            )
            return

        if os.geteuid() != 0:
            self.dialog.msgbox(
                "Root Required",
                "Certificate generation requires root privileges.\n\n"
                "MeshForge should already be running with sudo.\n"
                "If not, restart with: sudo python3 src/launcher_tui/main.py"
            )
            return

        self.dialog.infobox("SSL Certificate", "Generating trusted certificate...")

        success, msg = generate_localhost_cert()

        if success:
            # Update meshtasticd config to use the new cert
            config_updated = self._update_meshtasticd_ssl_config()

            restart_msg = ""
            if config_updated:
                restart_msg = (
                    "\nmeshtasticd config updated with new cert paths.\n"
                    "Restart meshtasticd to apply:\n"
                    "  sudo systemctl restart meshtasticd"
                )

            self.dialog.msgbox(
                "Certificate Generated",
                f"{msg}\n{restart_msg}"
            )
        else:
            self.dialog.msgbox("Certificate Error", msg)

    def _update_meshtasticd_ssl_config(self) -> bool:
        """Add SSL cert/key paths to meshtasticd config.yaml."""
        from pathlib import Path

        config_path = Path("/etc/meshtasticd/config.yaml")
        if not config_path.exists():
            return False

        try:
            content = config_path.read_text()

            # Check if SSLCert is already configured
            if "SSLCert:" in content and "meshforge" in content.lower():
                return True  # Already configured with our cert

            import re

            # Look for Webserver section and add/update SSL paths
            if "SSLCert:" in content:
                # Replace existing SSLCert/SSLKey lines
                content = re.sub(
                    r'(\s+)SSLCert:.*',
                    r'\1SSLCert: /etc/meshtasticd/ssl/certificate.pem',
                    content
                )
                content = re.sub(
                    r'(\s+)SSLKey:.*',
                    r'\1SSLKey: /etc/meshtasticd/ssl/private_key.pem',
                    content
                )
            elif "Webserver:" in content:
                # Add SSL paths after existing Webserver entries
                content = re.sub(
                    r'(Webserver:.*?\n(?:\s+\w+:.*\n)*)',
                    r'\1  SSLCert: /etc/meshtasticd/ssl/certificate.pem\n'
                    r'  SSLKey: /etc/meshtasticd/ssl/private_key.pem\n',
                    content
                )
            else:
                return False  # No Webserver section found

            config_path.write_text(content)
            logger.info("Updated meshtasticd config with SSL cert paths")
            return True

        except Exception as e:
            logger.error("Failed to update meshtasticd SSL config: %s", e)
            return False

    def _show_manual_ssl_help(self):
        """Show manual SSL certificate acceptance guidance."""
        self.dialog.msgbox(
            "Manual SSL Accept",
            "If you prefer to manually accept the certificate:\n\n"
            "Chrome/Edge:\n"
            "  1. Click 'Advanced'\n"
            "  2. Click 'Proceed to localhost (unsafe)'\n\n"
            "Firefox:\n"
            "  1. Click 'Advanced...'\n"
            "  2. Click 'Accept the Risk and Continue'\n\n"
            "Safari:\n"
            "  1. Click 'Show Details'\n"
            "  2. Click 'visit this website'\n\n"
            "lynx:\n"
            "  Type 'y' at the certificate prompt\n\n"
            "This must be done per-browser.\n"
            "Generate a trusted cert to avoid this entirely."
        )
