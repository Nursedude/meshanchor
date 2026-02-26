"""
Propagation Mixin for MeshForge Launcher TUI.

Provides space weather, HF band conditions, DX spots, ionosonde data,
VOACAP predictions, and data source configuration.
Wires the commands/propagation.py backend into the TUI.
"""

from commands import propagation
from commands.propagation import DataSource


class PropagationMixin:
    """Mixin providing space weather & HF propagation tools for the TUI."""

    def _propagation_menu(self):
        """Space Weather & Propagation submenu."""
        while True:
            choices = [
                ("summary", "Propagation Summary   Quick overview"),
                ("weather", "Space Weather         SFI, Kp, A-index"),
                ("bands", "Band Conditions       HF band assessment"),
                ("alerts", "NOAA Alerts           Active warnings"),
                ("dx", "DX Spots              Telnet DX cluster"),
                ("ionosonde", "Ionosonde Data        foF2 & MUF"),
                ("voacap", "VOACAP Prediction     Point-to-point HF"),
                ("sources", "Configure Sources     Data source setup"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Space Weather & Propagation",
                "HF propagation and space weather tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "summary": ("Propagation Summary", self._show_propagation_summary),
                "weather": ("Space Weather", self._show_space_weather),
                "bands": ("Band Conditions", self._show_band_conditions),
                "alerts": ("NOAA Alerts", self._show_noaa_alerts),
                "dx": ("DX Spots", self._show_dx_spots),
                "ionosonde": ("Ionosonde Data", self._show_ionosonde),
                "voacap": ("VOACAP Prediction", self._show_voacap),
                "sources": ("Configure Sources", self._configure_prop_sources),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    # --- Display methods ---

    def _show_propagation_summary(self):
        """Show one-line propagation summary with overall assessment."""
        result = propagation.get_propagation_summary()
        if not result.success:
            self.dialog.msgbox("Error", result.message)
            return

        d = result.data
        lines = [
            result.message,
            "",
            f"Overall:      {d.get('overall', 'Unknown')}",
            f"Solar Flux:   {d.get('solar_flux', 'N/A')} SFU",
            f"K-index:      {d.get('k_index', 'N/A')}",
            f"A-index:      {d.get('a_index', 'N/A')}",
            f"Geomag:       {d.get('geomag_storm', 'N/A')}",
            "",
            f"Source: {d.get('source', 'NOAA SWPC')}",
        ]
        self.dialog.msgbox("Propagation Summary", "\n".join(lines))

    def _show_space_weather(self):
        """Show detailed space weather conditions."""
        result = propagation.get_space_weather()
        if not result.success:
            self.dialog.msgbox("Error", result.message)
            return

        d = result.data
        lines = [
            "Current Space Weather Conditions",
            "=" * 40,
            "",
            f"Solar Flux Index (SFI):  {d.get('solar_flux', 'N/A')} SFU",
            f"Sunspot Number:          {d.get('sunspot_number', 'N/A')}",
            f"K-index (Kp):            {d.get('k_index', 'N/A')}",
            f"A-index:                 {d.get('a_index', 'N/A')}",
            f"X-ray Flux:              {d.get('xray_class', 'N/A')}",
            f"Geomagnetic Storm:       {d.get('geomag_storm', 'N/A')}",
        ]

        updated = d.get('updated')
        if updated:
            lines.extend(["", f"Updated: {updated}"])

        lines.extend(["", f"Source: {d.get('source', 'NOAA SWPC')}"])
        self.dialog.msgbox("Space Weather", "\n".join(lines))

    def _show_band_conditions(self):
        """Show per-band HF condition assessment."""
        result = propagation.get_band_conditions()
        if not result.success:
            self.dialog.msgbox("Error", result.message)
            return

        d = result.data
        bands = d.get('bands', {})

        lines = [
            f"Overall: {d.get('overall', 'Unknown')}",
            f"SFI={d.get('solar_flux', 'N/A')}  "
            f"Kp={d.get('k_index', 'N/A')}  "
            f"A={d.get('a_index', 'N/A')}",
            "",
            "Band        Condition",
            "-" * 30,
        ]

        # Sort bands by frequency (descending = higher bands first)
        band_order = [
            '10m', '12m', '15m', '17m', '20m',
            '30m', '40m', '60m', '80m', '160m',
        ]
        for band in band_order:
            if band in bands:
                lines.append(f"{band:<12}{bands[band]}")
        # Any remaining bands not in our order
        for band, cond in sorted(bands.items()):
            if band not in band_order:
                lines.append(f"{band:<12}{cond}")

        lines.extend(["", f"Source: {d.get('source', 'NOAA SWPC')}"])
        self.dialog.msgbox("HF Band Conditions", "\n".join(lines))

    def _show_noaa_alerts(self):
        """Show active NOAA space weather alerts."""
        result = propagation.get_alerts()
        if not result.success:
            self.dialog.msgbox("Error", result.message)
            return

        d = result.data
        alerts = d.get('alerts', [])

        if not alerts:
            self.dialog.msgbox("NOAA Alerts", "No active space weather alerts.")
            return

        lines = [f"{d.get('count', 0)} Active Space Weather Alerts", ""]
        for i, alert in enumerate(alerts, 1):
            msg = alert.get('message', '').strip()
            issued = alert.get('issue_datetime', '')
            # Truncate long alert messages for display
            if len(msg) > 500:
                msg = msg[:500] + "..."
            lines.append(f"--- Alert {i} ({issued}) ---")
            lines.append(msg)
            lines.append("")

        self.dialog.msgbox(
            "NOAA Space Weather Alerts",
            "\n".join(lines),
            height=22,
            width=76,
        )

    def _show_dx_spots(self):
        """Show DX cluster spots via telnet."""
        result = propagation.get_dx_spots_telnet()
        if not result.success:
            self.dialog.msgbox("DX Spots", result.message)
            return

        d = result.data
        spots = d.get('spots', [])

        lines = [
            f"{d.get('count', 0)} spots from {d.get('server', 'DX Cluster')}",
            "",
            f"{'Freq':>10}  {'DX Call':<12} {'Spotter':<12} {'Comment'}",
            "-" * 60,
        ]
        for spot in spots:
            freq = spot.get('frequency', '')
            dx_call = spot.get('dx_call', '')
            spotter = spot.get('spotter', '')
            comment = spot.get('comment', '')
            time_z = spot.get('time', '')
            line = f"{freq:>10}  {dx_call:<12} {spotter:<12} {comment}"
            if time_z:
                line += f" {time_z}"
            lines.append(line)

        self.dialog.msgbox(
            "DX Cluster Spots",
            "\n".join(lines),
            height=22,
            width=76,
        )

    def _show_ionosonde(self):
        """Show real-time ionosonde data (foF2 & MUF)."""
        result = propagation.get_ionosonde_data()
        if not result.success:
            self.dialog.msgbox("Error", result.message)
            return

        d = result.data
        stations = d.get('stations', [])

        lines = [
            f"{d.get('count', 0)} ionosonde stations",
        ]
        avg_fof2 = d.get('avg_fof2')
        avg_muf = d.get('avg_muf')
        if avg_fof2:
            lines.append(f"Average foF2: {avg_fof2:.1f} MHz")
        if avg_muf:
            lines.append(f"Average MUF:  {avg_muf:.1f} MHz")

        lines.extend([
            "",
            f"{'Station':<20} {'foF2':>8} {'MUF':>8}",
            "-" * 40,
        ])
        for stn in stations[:20]:
            name = stn.get('name', '?')[:18]
            fof2 = f"{stn['fof2']:.1f}" if stn.get('fof2') else "  --"
            muf = f"{stn['muf']:.1f}" if stn.get('muf') else "  --"
            lines.append(f"{name:<20} {fof2:>8} {muf:>8}")

        lines.extend(["", f"Source: {d.get('source', 'prop.kc2g.com')}"])
        self.dialog.msgbox(
            "Ionosonde Data",
            "\n".join(lines),
            height=22,
            width=76,
        )

    def _show_voacap(self):
        """Show VOACAP point-to-point HF prediction with coordinate input."""
        tx_lat = self.dialog.inputbox(
            "VOACAP Prediction", "Transmitter latitude (decimal degrees):", "21.3"
        )
        if tx_lat is None:
            return
        tx_lon = self.dialog.inputbox(
            "VOACAP Prediction", "Transmitter longitude (decimal degrees):", "-157.8"
        )
        if tx_lon is None:
            return
        rx_lat = self.dialog.inputbox(
            "VOACAP Prediction", "Receiver latitude (decimal degrees):", "37.8"
        )
        if rx_lat is None:
            return
        rx_lon = self.dialog.inputbox(
            "VOACAP Prediction", "Receiver longitude (decimal degrees):", "-122.4"
        )
        if rx_lon is None:
            return

        try:
            result = propagation.get_voacap_online(
                tx_lat=float(tx_lat),
                tx_lon=float(tx_lon),
                rx_lat=float(rx_lat),
                rx_lon=float(rx_lon),
            )
        except ValueError:
            self.dialog.msgbox("Error", "Invalid coordinate format. Use decimal degrees.")
            return

        if not result.success:
            self.dialog.msgbox("Error", result.message)
            return

        d = result.data
        bands = d.get('bands', {})
        tx = d.get('tx', {})
        rx = d.get('rx', {})

        lines = [
            f"TX: {tx.get('lat', '?')}, {tx.get('lon', '?')}",
            f"RX: {rx.get('lat', '?')}, {rx.get('lon', '?')}",
            "",
        ]

        if bands:
            lines.append(f"{'Band':<10} {'Reliability'}")
            lines.append("-" * 30)
            for band, value in sorted(bands.items()):
                lines.append(f"{band:<10} {value}")
        else:
            lines.append("No band predictions available.")

        lines.extend(["", f"Source: {d.get('source', 'VOACAP Online')}"])
        self.dialog.msgbox(
            "VOACAP Prediction",
            "\n".join(lines),
            height=20,
            width=60,
        )

    # --- Source configuration ---

    def _configure_prop_sources(self):
        """Configure optional propagation data sources."""
        while True:
            # Get current source status
            src_result = propagation.get_sources()
            sources = src_result.data.get('sources', {}) if src_result.success else {}

            noaa_status = "ON (always)"
            ohc = sources.get('openhamclock', {})
            ohc_status = "ON" if ohc.get('enabled') else "OFF"
            hc = sources.get('hamclock', {})
            hc_status = "ON" if hc.get('enabled') else "OFF"
            pskr = sources.get('pskreporter', {})
            pskr_status = "ON" if pskr.get('enabled') else "OFF"

            choices = [
                ("noaa", f"NOAA SWPC           {noaa_status}"),
                ("ohc", f"OpenHamClock        {ohc_status}"),
                ("hc", f"HamClock (legacy)   {hc_status}"),
                ("pskr", f"PSK Reporter        {pskr_status}"),
                ("test", "Test Connectivity    Check all sources"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Propagation Sources",
                "Configure data sources (NOAA is always primary):",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "noaa":
                self.dialog.msgbox(
                    "NOAA SWPC",
                    "NOAA Space Weather Prediction Center is the primary\n"
                    "data source and is always enabled.\n\n"
                    "No configuration needed — uses public API."
                )
            elif choice == "ohc":
                self._toggle_rest_source(
                    DataSource.OPENHAMCLOCK, "OpenHamClock", 3000
                )
            elif choice == "hc":
                self._toggle_rest_source(
                    DataSource.HAMCLOCK, "HamClock", 8080
                )
            elif choice == "pskr":
                self._toggle_psk_reporter()
            elif choice == "test":
                self._test_prop_sources()

    def _toggle_rest_source(
        self, source: DataSource, name: str, default_port: int
    ):
        """Toggle a REST-based data source (OpenHamClock/HamClock)."""
        src_result = propagation.get_sources()
        sources = src_result.data.get('sources', {}) if src_result.success else {}
        current = sources.get(source.value, {})
        is_enabled = current.get('enabled', False)

        if is_enabled:
            propagation.configure_source(source, enabled=False)
            self.dialog.msgbox(name, f"{name} disabled.")
        else:
            host = self.dialog.inputbox(
                name, f"{name} host:", current.get('host', 'localhost')
            )
            if host is None:
                return
            port = self.dialog.inputbox(
                name, f"{name} port:", str(current.get('port', default_port))
            )
            if port is None:
                return
            try:
                result = propagation.configure_source(
                    source, host=host, port=int(port), enabled=True
                )
                self.dialog.msgbox(name, result.message)
            except ValueError:
                self.dialog.msgbox("Error", "Invalid port number.")

    def _toggle_psk_reporter(self):
        """Toggle PSK Reporter MQTT source."""
        src_result = propagation.get_sources()
        sources = src_result.data.get('sources', {}) if src_result.success else {}
        current = sources.get('pskreporter', {})
        is_enabled = current.get('enabled', False)

        if is_enabled:
            propagation.configure_source(DataSource.PSKREPORTER, enabled=False)
            self.dialog.msgbox("PSK Reporter", "PSK Reporter disabled.")
        else:
            callsign = self.dialog.inputbox(
                "PSK Reporter", "Your callsign (optional filter):", ""
            )
            if callsign is None:
                return
            result = propagation.configure_source(
                DataSource.PSKREPORTER,
                enabled=True,
                callsign=callsign,
            )
            self.dialog.msgbox("PSK Reporter", result.message)

    def _test_prop_sources(self):
        """Test connectivity to all configured sources."""
        lines = ["Testing data sources...", ""]

        for source, name in [
            (DataSource.NOAA, "NOAA SWPC"),
            (DataSource.OPENHAMCLOCK, "OpenHamClock"),
            (DataSource.HAMCLOCK, "HamClock"),
            (DataSource.PSKREPORTER, "PSK Reporter"),
        ]:
            result = propagation.check_source(source)
            status = "OK" if result.success else "FAIL"
            lines.append(f"  {name:<20} [{status}] {result.message}")

        self.dialog.msgbox("Source Connectivity", "\n".join(lines))
