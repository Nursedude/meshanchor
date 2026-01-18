"""
HamClock Features Mixin for MeshForge.

Handles location fetching, DX spots, and satellite tracking features.
Extracted from HamClockPanel to reduce file size.
"""

import threading
import urllib.request
import urllib.error
import logging
from gi.repository import GLib

logger = logging.getLogger(__name__)


class HamClockFeaturesMixin:
    """Mixin providing location, DX spots, and satellite features for HamClock panel."""

    # ==================== DE/DX Location Methods ====================

    def _on_fetch_locations(self, button):
        """Fetch DE and DX locations from HamClock"""
        logger.info("[HamClock] Fetch Locations button clicked")

        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            self.status_label.set_label("Configure HamClock URL first")
            return

        self.status_label.set_label("Fetching locations...")

        def fetch():
            api_url = f"{url}:{api_port}"
            de_data = {}
            dx_data = {}

            # Fetch DE location
            try:
                de_url = f"{api_url}/get_de.txt"
                req = urllib.request.Request(de_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = response.read().decode('utf-8')
                    de_data = self._parse_location(data)
            except Exception as e:
                logger.debug(f"[HamClock] DE fetch error: {e}")

            # Fetch DX location
            try:
                dx_url = f"{api_url}/get_dx.txt"
                req = urllib.request.Request(dx_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = response.read().decode('utf-8')
                    dx_data = self._parse_location(data)
            except Exception as e:
                logger.debug(f"[HamClock] DX fetch error: {e}")

            GLib.idle_add(self._update_location_display, de_data, dx_data)

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_location(self, data):
        """Parse DE or DX location response"""
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                if 'call' in key_lower:
                    result['callsign'] = value
                elif 'grid' in key_lower:
                    result['grid'] = value
                elif 'lat' in key_lower:
                    result['lat'] = value
                elif 'lng' in key_lower or 'lon' in key_lower:
                    result['lon'] = value
                elif 'dist' in key_lower:
                    result['distance'] = value
                elif 'bear' in key_lower or 'az' in key_lower:
                    result['bearing'] = value

        return result

    # ==================== DX Spots Methods ====================

    def _on_fetch_dx_spots(self, button):
        """Fetch DX cluster spots from HamClock"""
        logger.info("[HamClock] Fetch DX Spots button clicked")

        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            self.status_label.set_label("Configure HamClock URL first")
            return

        self.status_label.set_label("Fetching DX spots...")

        def fetch():
            api_url = f"{url}:{api_port}"
            try:
                dx_url = f"{api_url}/get_dxspots.txt"
                req = urllib.request.Request(dx_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = response.read().decode('utf-8')
                    spots = self._parse_dx_spots(data)
                    GLib.idle_add(self._update_dx_spots_display, spots)
            except urllib.error.HTTPError as e:
                logger.debug(f"[HamClock] DX spots HTTP error: {e.code}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"DX spots not available (HTTP {e.code})")
                )
            except Exception as e:
                logger.debug(f"[HamClock] DX spots fetch error: {e}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"DX spots error: {e}")
                )

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_dx_spots(self, data):
        """Parse DX spots response from HamClock

        Expected format varies, but typically:
            Spot_0_call=XX0XX
            Spot_0_freq=14.205
            Spot_0_time=1234
            ...
        """
        spots = []
        current_spot = {}

        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                if 'call' in key_lower:
                    if current_spot and 'callsign' in current_spot:
                        spots.append(current_spot)
                        current_spot = {}
                    current_spot['callsign'] = value
                elif 'freq' in key_lower:
                    current_spot['freq'] = value
                elif 'time' in key_lower:
                    current_spot['time'] = value
                elif 'mode' in key_lower:
                    current_spot['mode'] = value
                elif 'spotter' in key_lower:
                    current_spot['spotter'] = value

        # Don't forget the last spot
        if current_spot and 'callsign' in current_spot:
            spots.append(current_spot)

        return spots

    def _on_open_dx_cluster(self, button):
        """Open DX Summit web cluster in browser"""
        logger.info("[HamClock] DX Summit button clicked")
        url = "https://dxsummit.fi/"
        self._open_url_in_browser(url)

    def _on_open_dx_propagation(self, button):
        """Open DX propagation charts in browser"""
        logger.info("[HamClock] DX Propagation button clicked")
        url = "https://www.hamqsl.com/solar.html"
        self._open_url_in_browser(url)

    # ==================== Satellite Tracking Methods ====================

    def _on_fetch_satellite(self, button):
        """Fetch current satellite info from HamClock"""
        logger.info("[HamClock] Fetch Satellite button clicked")

        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            self.status_label.set_label("Configure HamClock URL first")
            return

        self.status_label.set_label("Fetching satellite info...")

        def fetch():
            api_url = f"{url}:{api_port}"
            try:
                sat_url = f"{api_url}/get_satellite.txt"
                req = urllib.request.Request(sat_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = response.read().decode('utf-8')
                    sat_data = self._parse_satellite(data)
                    GLib.idle_add(self._update_satellite_display, sat_data)
            except urllib.error.HTTPError as e:
                logger.debug(f"[HamClock] Satellite HTTP error: {e.code}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"Satellite not available (HTTP {e.code})")
                )
            except Exception as e:
                logger.debug(f"[HamClock] Satellite fetch error: {e}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"Satellite error: {e}")
                )

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_satellite(self, data):
        """Parse satellite response from HamClock"""
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                if 'name' in key_lower or 'sat' in key_lower:
                    result['name'] = value
                elif 'az' in key_lower:
                    result['az'] = f"{value}°"
                elif 'el' in key_lower:
                    result['el'] = f"{value}°"
                elif 'range' in key_lower or 'rng' in key_lower:
                    result['range'] = f"{value} km"
                elif 'rise' in key_lower or 'aos' in key_lower:
                    result['next_pass'] = f"AOS: {value}"
                elif 'set' in key_lower or 'los' in key_lower:
                    if 'next_pass' not in result:
                        result['next_pass'] = f"LOS: {value}"
                elif 'up' in key_lower and 'link' not in key_lower:
                    result['uplink'] = value
                elif 'down' in key_lower and 'link' not in key_lower:
                    result['downlink'] = value

        return result

    def _on_fetch_sat_list(self, button):
        """Fetch list of available satellites from HamClock"""
        logger.info("[HamClock] Satellite List button clicked")

        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            self.status_label.set_label("Configure HamClock URL first")
            return

        self.status_label.set_label("Fetching satellite list...")

        def fetch():
            api_url = f"{url}:{api_port}"
            try:
                sat_url = f"{api_url}/get_satlist.txt"
                req = urllib.request.Request(sat_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = response.read().decode('utf-8')
                    GLib.idle_add(self._show_sat_list, data)
            except urllib.error.HTTPError as e:
                logger.debug(f"[HamClock] Sat list HTTP error: {e.code}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"Sat list not available (HTTP {e.code})")
                )
            except Exception as e:
                logger.debug(f"[HamClock] Sat list fetch error: {e}")
                GLib.idle_add(
                    lambda: self.status_label.set_label(f"Sat list error: {e}")
                )

        threading.Thread(target=fetch, daemon=True).start()

    def _on_open_heavens_above(self, button):
        """Open Heavens-Above satellite tracking website"""
        logger.info("[HamClock] Heavens-Above button clicked")
        url = "https://www.heavens-above.com/"
        self._open_url_in_browser(url)
