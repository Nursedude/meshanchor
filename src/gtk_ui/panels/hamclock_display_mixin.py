"""
HamClock Display Mixin for MeshForge.

Handles all display update methods for space weather, NOAA, VOACAP, and locations.
Extracted from HamClockPanel to reduce file size.
"""

import logging
from gi.repository import GLib

logger = logging.getLogger(__name__)


class HamClockDisplayMixin:
    """Mixin providing display update methods for HamClock panel."""

    def _update_weather_display(self, data):
        """Update the weather display with fetched data"""
        logger.debug(f"[HamClock] Updating display with: {data}")

        updated_count = 0

        # Update stat labels
        for key, label in self.stat_labels.items():
            if key in data:
                label.set_label(str(data[key]))
                updated_count += 1
            # Also check for capitalized versions
            elif key.upper() in data:
                label.set_label(str(data[key.upper()]))
                updated_count += 1

        # Update band condition labels
        for key, label in self.band_labels.items():
            if key in data:
                label.set_label(str(data[key]))
                updated_count += 1

        # Update conditions based on Kp
        if 'kp' in data:
            try:
                kp = float(data['kp'])
                if kp < 3:
                    self.stat_labels['conditions'].set_label("Good")
                elif kp < 5:
                    self.stat_labels['conditions'].set_label("Moderate")
                else:
                    self.stat_labels['conditions'].set_label("Disturbed")
                updated_count += 1
            except ValueError:
                pass

        if updated_count > 0:
            self.status_label.set_label(f"Updated {updated_count} values")
            logger.debug(f"[HamClock] Updated {updated_count} UI labels")
            self._record_update_time()
        else:
            self.status_label.set_label("No data received")
            logger.debug("[HamClock] No values to update")

    def _update_noaa_display(self, data):
        """Update display with SpaceWeatherData from NOAA SWPC"""
        try:
            # Solar Flux Index
            if data.solar_flux:
                self.stat_labels['sfi'].set_label(f"{int(data.solar_flux)}")

            # Sunspot number (if available)
            if data.sunspot_number:
                self.stat_labels['sunspots'].set_label(str(data.sunspot_number))

            # K-index and geomagnetic status
            if data.k_index is not None:
                k_str = f"K:{data.k_index}"
                if hasattr(data, 'geomag_storm') and data.geomag_storm:
                    k_str += f" ({data.geomag_storm.value})"
                if 'a_index' in self.stat_labels:
                    self.stat_labels['a_index'].set_label(k_str)

            # X-ray flux
            if data.xray_flux and 'xray' in self.stat_labels:
                self.stat_labels['xray'].set_label(data.xray_flux)

            # Band conditions from SpaceWeatherAPI assessment
            if data.band_conditions:
                band_mapping = {
                    '80m': '80m-40m', '40m': '80m-40m',
                    '30m': '30m-20m', '20m': '30m-20m',
                    '17m': '17m-15m', '15m': '17m-15m',
                    '12m': '12m-10m', '10m': '12m-10m',
                }

                pair_conditions = {}
                for band, condition in data.band_conditions.items():
                    pair_key = band_mapping.get(band, band)
                    cond_value = condition.value if hasattr(condition, 'value') else str(condition)
                    if pair_key not in pair_conditions:
                        pair_conditions[pair_key] = cond_value
                    else:
                        pair_conditions[pair_key] = f"{pair_conditions[pair_key]}/{cond_value}"

                for band_pair, condition in pair_conditions.items():
                    if band_pair in self.band_labels:
                        self.band_labels[band_pair].set_label(condition)

            # Overall conditions
            if data.solar_flux:
                sfi = data.solar_flux
                if sfi >= 150:
                    conditions = "Excellent"
                elif sfi >= 120:
                    conditions = "Good"
                elif sfi >= 90:
                    conditions = "Fair"
                else:
                    conditions = "Poor"

                if data.k_index and data.k_index >= 5:
                    conditions = "Disturbed"

                self.stat_labels['conditions'].set_label(conditions)

            # Update status with summary
            summary_parts = []
            if data.solar_flux:
                summary_parts.append(f"SFI:{int(data.solar_flux)}")
            if data.k_index is not None:
                summary_parts.append(f"K:{data.k_index}")
            if data.xray_flux:
                summary_parts.append(f"X-ray:{data.xray_flux}")

            self.status_label.set_label(f"NOAA SWPC: {' '.join(summary_parts)}")
            self._record_update_time()

        except Exception as e:
            logger.error(f"[HamClock] Error updating NOAA display: {e}")
            self.status_label.set_label(f"Parse error: {str(e)[:40]}")

    def _update_noaa_display_legacy(self, data):
        """Legacy update display with raw NOAA solar cycle data"""
        try:
            # Solar Flux Index
            if 'f10.7' in data:
                self.stat_labels['sfi'].set_label(str(data['f10.7']))

            # Sunspot number
            if 'ssn' in data:
                self.stat_labels['sunspots'].set_label(str(data['ssn']))

            # Estimate band conditions based on SFI
            sfi = float(data.get('f10.7', 0))
            if sfi >= 150:
                conditions = "Excellent"
                bands = {"80m-40m": "Good/Good", "30m-20m": "Excellent/Good",
                         "17m-15m": "Excellent/Fair", "12m-10m": "Good/Poor"}
            elif sfi >= 120:
                conditions = "Good"
                bands = {"80m-40m": "Good/Good", "30m-20m": "Good/Good",
                         "17m-15m": "Good/Fair", "12m-10m": "Fair/Poor"}
            elif sfi >= 90:
                conditions = "Fair"
                bands = {"80m-40m": "Good/Good", "30m-20m": "Fair/Fair",
                         "17m-15m": "Fair/Poor", "12m-10m": "Poor/Poor"}
            else:
                conditions = "Poor"
                bands = {"80m-40m": "Fair/Good", "30m-20m": "Poor/Fair",
                         "17m-15m": "Poor/Poor", "12m-10m": "Poor/Poor"}

            self.stat_labels['conditions'].set_label(conditions)

            for band, condition in bands.items():
                if band in self.band_labels:
                    self.band_labels[band].set_label(condition)

            self.status_label.set_label(f"NOAA data updated (SFI: {sfi})")
            self._record_update_time()
        except Exception as e:
            self.status_label.set_label(f"Parse error: {e}")

    def _update_voacap_display(self, data):
        """Update the VOACAP display with parsed data"""
        logger.debug(f"[HamClock] Updating VOACAP display: {data}")

        if not data.get('bands'):
            self.status_label.set_label("No VOACAP data available")
            for band_key, labels in self.voacap_labels.items():
                labels['reliability'].set_label("--")
                labels['snr'].set_label("--")
            self.voacap_path_label.set_label("Path: --")
            return

        updated = 0

        for band_key, labels in self.voacap_labels.items():
            if band_key in data['bands']:
                band_data = data['bands'][band_key]
                rel = band_data.get('reliability', 0)
                snr = band_data.get('snr', 0)

                # Color code reliability
                if rel >= 80:
                    rel_text = f"{rel}%"
                    labels['reliability'].remove_css_class("warning")
                    labels['reliability'].remove_css_class("error")
                    labels['reliability'].add_css_class("success")
                elif rel >= 50:
                    rel_text = f"{rel}%"
                    labels['reliability'].remove_css_class("success")
                    labels['reliability'].remove_css_class("error")
                    labels['reliability'].add_css_class("warning")
                elif rel > 0:
                    rel_text = f"{rel}%"
                    labels['reliability'].remove_css_class("success")
                    labels['reliability'].remove_css_class("warning")
                    labels['reliability'].add_css_class("error")
                else:
                    rel_text = "Closed"
                    labels['reliability'].remove_css_class("success")
                    labels['reliability'].remove_css_class("warning")
                    labels['reliability'].add_css_class("error")

                labels['reliability'].set_label(rel_text)
                labels['snr'].set_label(f"{snr} dB" if snr else "--")
                updated += 1
            else:
                labels['reliability'].set_label("--")
                labels['snr'].set_label("--")

        # Update path label
        path = data.get('path', '')
        utc = data.get('utc', '')
        if path:
            path_text = f"Path: {path}"
            if utc:
                path_text += f" @ {utc}Z"
            self.voacap_path_label.set_label(path_text)
        else:
            self.voacap_path_label.set_label("Path: --")

        if updated > 0:
            self.status_label.set_label(f"VOACAP: {updated} bands updated")
        else:
            self.status_label.set_label("VOACAP data incomplete")

    def _update_location_display(self, de_data, dx_data):
        """Update the location display with fetched data"""
        updated = 0

        if de_data:
            callsign = de_data.get('callsign', '--')
            grid = de_data.get('grid', '--')
            self.de_callsign_label.set_label(callsign)
            self.de_grid_label.set_label(grid)
            updated += 1

        if dx_data:
            callsign = dx_data.get('callsign', '--')
            grid = dx_data.get('grid', '--')
            self.dx_callsign_label.set_label(callsign)
            self.dx_grid_label.set_label(grid)
            updated += 1

            # Update path info if available
            distance = dx_data.get('distance', '')
            bearing = dx_data.get('bearing', '')
            if distance or bearing:
                path_parts = []
                if distance:
                    path_parts.append(f"{distance} km")
                if bearing:
                    path_parts.append(f"{bearing}°")
                self.path_info_label.set_label(" | ".join(path_parts))

        if updated > 0:
            self.status_label.set_label("Locations updated")
        else:
            self.status_label.set_label("No location data received")

    def _update_dx_spots_display(self, spots):
        """Update the DX spots text view"""
        if not spots:
            buffer = self.dx_spots_text.get_buffer()
            buffer.set_text("No DX spots available\n\nMake sure HamClock is connected to a DX cluster.")
            self.status_label.set_label("No DX spots found")
            return

        # Format spots as text
        lines = []
        lines.append(f"{'Call':<10} {'Freq':>10} {'Mode':<5} {'Time':>5}")
        lines.append("-" * 35)

        for spot in spots[:20]:  # Limit to 20 spots
            call = spot.get('callsign', '???')[:10]
            freq = spot.get('freq', '---')[:10]
            mode = spot.get('mode', '-')[:5]
            time_str = spot.get('time', '--')[:5]
            lines.append(f"{call:<10} {freq:>10} {mode:<5} {time_str:>5}")

        buffer = self.dx_spots_text.get_buffer()
        buffer.set_text("\n".join(lines))
        self.status_label.set_label(f"Loaded {len(spots)} DX spots")

    def _update_satellite_display(self, sat_data):
        """Update the satellite display with fetched data"""
        if not sat_data:
            for label in self.sat_labels.values():
                label.set_label("--")
            self.status_label.set_label("No satellite data")
            return

        updated = 0
        for key, label in self.sat_labels.items():
            if key in sat_data:
                label.set_label(str(sat_data[key]))
                updated += 1
            else:
                label.set_label("--")

        if updated > 0:
            self.status_label.set_label(f"Satellite: {sat_data.get('name', 'Unknown')}")
        else:
            self.status_label.set_label("Satellite data incomplete")

    def _show_sat_list(self, data):
        """Display satellite list in DX spots text area temporarily"""
        # Parse satellite names
        sats = []
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                if 'name' in key.lower() or 'sat' in key.lower():
                    sats.append(value.strip())
            elif line.strip():
                sats.append(line.strip())

        if sats:
            buffer = self.dx_spots_text.get_buffer()
            text = "Available Satellites:\n" + "-" * 30 + "\n"
            text += "\n".join(sats[:30])
            if len(sats) > 30:
                text += f"\n... and {len(sats) - 30} more"
            buffer.set_text(text)
            self.status_label.set_label(f"Found {len(sats)} satellites")
        else:
            self.status_label.set_label("No satellites in list")
