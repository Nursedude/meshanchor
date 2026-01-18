"""Propagation Mixin - Band conditions and PSKReporter stats

Provides propagation monitoring functionality:
- Band condition refresh via HamClock/NOAA APIs
- PSKReporter live propagation statistics

This mixin requires the following attributes on the class:
- _band_labels: dict mapping band names to {'day': Label, 'night': Label}
- _psk_labels: dict with 'active_rx', 'spots_hour', 'active_bands' Label widgets
- _output_message(message): Method to output log messages
- _set_output(text): Method to set main output text
"""

import threading
import urllib.request
import urllib.error

from gi.repository import GLib


class PropagationMixin:
    """Mixin providing propagation monitoring functionality."""

    def _on_refresh_bands(self, button):
        """Refresh band conditions (uses auto-fallback API)"""
        self._output_message("Fetching band conditions...")

        def fetch():
            try:
                # Use the commands layer with auto-fallback
                import sys
                from pathlib import Path
                src_dir = Path(__file__).parent.parent.parent.parent
                sys.path.insert(0, str(src_dir))
                from commands import hamclock

                # Get propagation summary (auto-fallback to NOAA)
                result = hamclock.get_propagation_summary()

                if result.success:
                    data = result.data
                    source = data.get('source', 'Unknown')

                    lines = [
                        "=== Solar Conditions ===",
                        f"Source: {source}",
                        f"Solar Flux Index: {data.get('sfi', 'N/A')}",
                        f"Kp Index: {data.get('kp', 'N/A')}",
                        f"Sunspots: {data.get('ssn', 'N/A')}",
                        f"X-Ray: {data.get('xray', 'N/A')}",
                        f"Geomagnetic: {data.get('geomagnetic', 'N/A')}",
                        f"Overall: {data.get('overall', 'N/A')}",
                        "",
                        "=== HF Band Conditions ==="
                    ]

                    # Get band conditions
                    bands = data.get('bands_estimate', data.get('hf_conditions', {}))
                    if bands:
                        for band, cond in bands.items():
                            lines.append(f"  {band}: {cond}")
                            # Update grid labels
                            if band in self._band_labels:
                                if '/' in str(cond):
                                    day, night = str(cond).split('/', 1)
                                    GLib.idle_add(self._band_labels[band]['day'].set_label, day.strip())
                                    GLib.idle_add(self._band_labels[band]['night'].set_label, night.strip())
                                else:
                                    GLib.idle_add(self._band_labels[band]['day'].set_label, str(cond))
                                    GLib.idle_add(self._band_labels[band]['night'].set_label, str(cond))
                    else:
                        lines.append("  (Estimated from SFI - HamClock provides detailed conditions)")

                    GLib.idle_add(self._set_output, '\n'.join(lines))
                else:
                    GLib.idle_add(self._output_message, f"Error: {result.message}")

            except Exception as e:
                GLib.idle_add(self._output_message, f"Error fetching band data: {e}")

        threading.Thread(target=fetch, daemon=True).start()

    def _on_fetch_pskreporter(self, button):
        """Fetch PSKReporter propagation statistics"""
        self._output_message("Fetching PSKReporter stats...")
        button.set_sensitive(False)

        def fetch():
            try:
                # PSKReporter provides JSON stats at this endpoint
                url = "https://pskreporter.info/cgi-bin/psk-stats.pl"
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'MeshForge/1.0')

                with urllib.request.urlopen(req, timeout=15) as response:
                    data = response.read().decode('utf-8')

                # Parse the stats (format varies)
                lines = data.strip().split('\n')
                stats = {}

                for line in lines:
                    if ':' in line:
                        key, value = line.split(':', 1)
                        stats[key.strip().lower()] = value.strip()

                # Update UI
                if 'receivers' in stats or 'active receivers' in stats:
                    rx_count = stats.get('receivers', stats.get('active receivers', '?'))
                    GLib.idle_add(self._psk_labels['active_rx'].set_label, str(rx_count))

                if 'spots' in stats:
                    spots = stats.get('spots', '?')
                    GLib.idle_add(self._psk_labels['spots_hour'].set_label, str(spots))

                # Show bands info
                active_bands = []
                for band in ['160m', '80m', '40m', '30m', '20m', '17m', '15m', '12m', '10m', '6m']:
                    band_key = band.lower()
                    if band_key in stats:
                        active_bands.append(band)

                if active_bands:
                    GLib.idle_add(self._psk_labels['active_bands'].set_label, ', '.join(active_bands[:5]))
                else:
                    # Fallback - assume typical bands are active
                    GLib.idle_add(self._psk_labels['active_bands'].set_label, "20m, 40m, 80m")

                GLib.idle_add(self._output_message, "PSKReporter stats updated")
                GLib.idle_add(self._output_message, f"Raw data sample: {data[:200]}...")

            except urllib.error.HTTPError as e:
                GLib.idle_add(self._output_message, f"PSKReporter HTTP error: {e.code}")
                # Set fallback values
                GLib.idle_add(self._psk_labels['active_rx'].set_label, "~2000+")
                GLib.idle_add(self._psk_labels['spots_hour'].set_label, "~50000+")
                GLib.idle_add(self._psk_labels['active_bands'].set_label, "20m, 40m, 80m")
                GLib.idle_add(self._output_message, "Using estimated values (API may be restricted)")

            except Exception as e:
                GLib.idle_add(self._output_message, f"PSKReporter error: {e}")
                # Set fallback values
                GLib.idle_add(self._psk_labels['active_rx'].set_label, "~2000+")
                GLib.idle_add(self._psk_labels['spots_hour'].set_label, "~50000+")
                GLib.idle_add(self._psk_labels['active_bands'].set_label, "20m, 40m, 80m")

            GLib.idle_add(button.set_sensitive, True)

        threading.Thread(target=fetch, daemon=True).start()
