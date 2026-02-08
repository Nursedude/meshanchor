"""
Settings Menu Mixin - Application settings handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""


class SettingsMenuMixin:
    """Mixin providing application settings functionality."""

    def _settings_menu(self):
        """Settings menu."""
        choices = [
            ("connection", "Meshtastic Connection"),
            ("propagation", "Propagation Data Sources"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Settings",
                "Configure MeshForge:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "connection":
                self._configure_connection()
            elif choice == "propagation":
                self._configure_propagation_sources()

    def _configure_connection(self):
        """Configure Meshtastic connection."""
        choices = [
            ("localhost", "Local TCP (localhost:4403)"),
            ("serial", "Serial Port"),
            ("remote", "Remote Host"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu(
            "Meshtastic Connection",
            "Select connection type:",
            choices
        )

        if choice is None or choice == "back":
            return
        elif choice == "localhost":
            self._save_meshtasticd_connection("localhost", 4403)
            self.dialog.msgbox("Connection", "Connection set to localhost:4403")
        elif choice == "serial":
            port = self.dialog.inputbox("Serial Port", "Enter serial port:", "/dev/ttyUSB0")
            if port:
                self.dialog.msgbox("Connection", f"Connection set to {port}")
        elif choice == "remote":
            host_input = self.dialog.inputbox("Remote Host", "Enter host:port:", "192.168.1.100:4403")
            if host_input:
                # Parse host:port
                if ':' in host_input:
                    parts = host_input.rsplit(':', 1)
                    host = parts[0]
                    try:
                        port = int(parts[1])
                    except ValueError:
                        port = 4403
                else:
                    host = host_input
                    port = 4403
                self._save_meshtasticd_connection(host, port)
                self.dialog.msgbox("Connection", f"Connection set to {host}:{port}")

    def _save_meshtasticd_connection(self, host: str, port: int):
        """Save meshtasticd connection settings for MapDataCollector."""
        try:
            from utils.map_data_collector import MapDataCollector
            collector = MapDataCollector()
            collector.set_meshtasticd_connection(host, port)
        except ImportError:
            pass  # MapDataCollector not available

    def _configure_propagation_sources(self):
        """Configure propagation data sources.

        NOAA SWPC is always active (primary). Users can optionally
        enable HamClock or OpenHamClock for enhanced data.
        """
        while True:
            choices = [
                ("noaa", "NOAA SWPC (Primary - always active)"),
                ("openhamclock", "OpenHamClock (Optional)"),
                ("hamclock", "HamClock Legacy (Optional)"),
                ("test", "Test All Sources"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Propagation Data Sources",
                "NOAA is always active as primary source.\n"
                "Optionally configure HamClock/OpenHamClock for\n"
                "enhanced data (VOACAP, DX spots).",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "noaa":
                self._test_noaa_source()
            elif choice == "openhamclock":
                self._configure_openhamclock()
            elif choice == "hamclock":
                self._configure_hamclock_legacy()
            elif choice == "test":
                self._test_all_sources()

    def _test_noaa_source(self):
        """Test NOAA SWPC connectivity and show current data."""
        try:
            from commands import propagation
            result = propagation.check_source(propagation.DataSource.NOAA)
            if result.success:
                # Also get current conditions
                wx = propagation.get_space_weather()
                if wx.success:
                    d = wx.data
                    lines = [
                        "NOAA SWPC - Connected",
                        "",
                        f"Solar Flux (SFI): {d.get('solar_flux', 'N/A')}",
                        f"Kp Index: {d.get('k_index', 'N/A')}",
                        f"A Index: {d.get('a_index', 'N/A')}",
                        f"X-ray: {d.get('xray_flux', 'N/A')}",
                        f"Geomagnetic: {d.get('geomag_storm', 'N/A')}",
                        "",
                        "Band Conditions:",
                    ]
                    for band, cond in d.get('band_conditions', {}).items():
                        lines.append(f"  {band}: {cond}")
                    self.dialog.msgbox("NOAA Space Weather", "\n".join(lines))
                else:
                    self.dialog.msgbox("NOAA SWPC", "Connected but no data available.")
            else:
                self.dialog.msgbox("Error", f"Cannot reach NOAA SWPC:\n{result.message}")
        except ImportError:
            self.dialog.msgbox("Error", "Propagation module not available.")

    def _configure_openhamclock(self):
        """Configure OpenHamClock as optional data source."""
        host = self.dialog.inputbox(
            "OpenHamClock Host",
            "Enter OpenHamClock hostname or IP:\n"
            "(Docker: localhost, Remote: IP address)",
            "localhost"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        port = self.dialog.inputbox(
            "OpenHamClock Port",
            "Enter port (default 3000):",
            "3000"
        )

        if not port:
            return

        if not self._validate_port(port):
            self.dialog.msgbox("Error", "Invalid port number (1-65535).")
            return

        try:
            from commands import propagation
            result = propagation.configure_source(
                propagation.DataSource.OPENHAMCLOCK,
                host=host,
                port=int(port),
            )
            if result.success:
                # Test connectivity
                test = propagation.check_source(propagation.DataSource.OPENHAMCLOCK)
                if test.success:
                    self.dialog.msgbox(
                        "OpenHamClock Connected",
                        f"API: {host}:{port}\n\nOpenHamClock is now active as\n"
                        "an enhanced data source."
                    )
                else:
                    self.dialog.msgbox(
                        "OpenHamClock Configured",
                        f"Saved: {host}:{port}\n\n"
                        f"Connection test failed:\n{test.message}\n\n"
                        "Make sure OpenHamClock is running\n"
                        "(docker compose up)"
                    )
            else:
                self.dialog.msgbox("Error", result.message)
        except ImportError:
            self.dialog.msgbox("Error", "Propagation module not available.")

    def _configure_hamclock_legacy(self):
        """Configure legacy HamClock as optional data source."""
        host = self.dialog.inputbox(
            "HamClock Host",
            "Enter HamClock hostname or IP:\n"
            "(NOTE: Original HamClock sunsets June 2026)",
            "localhost"
        )

        if not host:
            return

        if not self._validate_hostname(host):
            self.dialog.msgbox("Error", "Invalid hostname or IP address.")
            return

        port = self.dialog.inputbox(
            "HamClock API Port",
            "Enter API port (default 8080):",
            "8080"
        )

        if not port:
            return

        if not self._validate_port(port):
            self.dialog.msgbox("Error", "Invalid port number (1-65535).")
            return

        try:
            from commands import propagation
            result = propagation.configure_source(
                propagation.DataSource.HAMCLOCK,
                host=host,
                port=int(port),
            )
            if result.success:
                test = propagation.check_source(propagation.DataSource.HAMCLOCK)
                if test.success:
                    self.dialog.msgbox(
                        "HamClock Connected",
                        f"API: {host}:{port}\n\nHamClock is now active as\n"
                        "an enhanced data source.\n\n"
                        "NOTE: Consider migrating to OpenHamClock\n"
                        "(original HamClock sunsets June 2026)"
                    )
                else:
                    self.dialog.msgbox(
                        "HamClock Configured",
                        f"Saved: {host}:{port}\n\n"
                        f"Connection test failed:\n{test.message}\n\n"
                        "Make sure HamClock is running."
                    )
            else:
                self.dialog.msgbox("Error", result.message)
        except ImportError:
            self.dialog.msgbox("Error", "Propagation module not available.")

    def _test_all_sources(self):
        """Test all configured propagation data sources."""
        lines = ["Propagation Source Status", "=" * 35, ""]

        try:
            from commands import propagation

            # NOAA (always)
            noaa = propagation.check_source(propagation.DataSource.NOAA)
            status = "Connected" if noaa.success else "Unreachable"
            lines.append(f"NOAA SWPC (primary): {status}")

            # OpenHamClock
            ohc = propagation.check_source(propagation.DataSource.OPENHAMCLOCK)
            if ohc.success:
                lines.append(f"OpenHamClock: Connected")
            else:
                lines.append(f"OpenHamClock: Not configured")

            # HamClock
            hc = propagation.check_source(propagation.DataSource.HAMCLOCK)
            if hc.success:
                lines.append(f"HamClock (legacy): Connected")
            else:
                lines.append(f"HamClock (legacy): Not configured")

            # Current data
            wx = propagation.get_space_weather()
            if wx.success:
                d = wx.data
                lines.append("")
                lines.append("-" * 35)
                lines.append("Current Conditions:")
                sfi = d.get('solar_flux')
                kp = d.get('k_index')
                if sfi:
                    lines.append(f"  SFI: {int(sfi)}")
                if kp is not None:
                    lines.append(f"  Kp: {kp}")
                lines.append(f"  {d.get('geomag_storm', '')}")

        except ImportError:
            lines.append("Propagation module not available.")

        self.dialog.msgbox("Source Status", "\n".join(lines))
