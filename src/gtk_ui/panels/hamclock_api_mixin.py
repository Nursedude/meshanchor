"""
HamClock API Mixin for MeshForge.

Provides HamClock data fetching and parsing methods extracted from
HamClockPanel to reduce file size and improve maintainability.
"""

import json
import threading
import urllib.request
import urllib.error
import logging
from gi.repository import GLib

logger = logging.getLogger(__name__)

# Import Space Weather API for NOAA data
try:
    from utils.space_weather import SpaceWeatherAPI, SpaceWeatherData
    HAS_SPACE_WEATHER = True
except ImportError:
    HAS_SPACE_WEATHER = False
    SpaceWeatherAPI = None
    SpaceWeatherData = None


class HamClockAPIMixin:
    """Mixin providing HamClock API data fetching methods."""

    def _fetch_space_weather(self):
        """Fetch space weather data from HamClock"""
        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            logger.debug("[HamClock] No URL configured, skipping fetch")
            return

        def fetch():
            api_url = f"{url}:{api_port}"
            weather_data = {}
            success_count = 0

            logger.debug(f"[HamClock] Fetching from {api_url}...")

            # Try various HamClock endpoints
            endpoints = [
                ("get_sys.txt", self._parse_sys),
                ("get_spacewx.txt", self._parse_spacewx),
                ("get_bc.txt", self._parse_band_conditions),
            ]

            for endpoint, parser in endpoints:
                try:
                    full_url = f"{api_url}/{endpoint}"
                    logger.debug(f"[HamClock] Trying {full_url}...")

                    req = urllib.request.Request(full_url, method='GET')
                    req.add_header('User-Agent', 'MeshForge/1.0')
                    with urllib.request.urlopen(req, timeout=5) as response:
                        data = response.read().decode('utf-8')
                        logger.debug(f"[HamClock] {endpoint} response: {data[:100]}...")
                        parsed = parser(data)
                        weather_data.update(parsed)
                        success_count += 1
                except urllib.error.HTTPError as e:
                    logger.debug(f"[HamClock] {endpoint}: HTTP {e.code} - {e.reason}")
                except urllib.error.URLError as e:
                    logger.debug(f"[HamClock] {endpoint}: URL Error - {e.reason}")
                except Exception as e:
                    logger.debug(f"[HamClock] {endpoint}: Error - {e}")

            logger.debug(f"[HamClock] Fetched {success_count} endpoints, {len(weather_data)} values")
            GLib.idle_add(self._update_weather_display, weather_data)

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_band_conditions(self, data):
        """Parse band conditions response from HamClock API (get_bc.txt)"""
        result = {}
        logger.debug(f"[HamClock] Parsing band conditions: {data[:200]}...")

        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                # Map band condition keys
                if '80' in key_lower or '40' in key_lower:
                    result['80m-40m'] = value
                elif '30' in key_lower or '20' in key_lower:
                    result['30m-20m'] = value
                elif '17' in key_lower or '15' in key_lower:
                    result['17m-15m'] = value
                elif '12' in key_lower or '10' in key_lower:
                    result['12m-10m'] = value

        return result

    def _parse_sys(self, data):
        """Parse system info response"""
        result = {}
        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                result[key.strip()] = value.strip()
        return result

    def _parse_spacewx(self, data):
        """Parse space weather response from HamClock API"""
        result = {}
        logger.debug(f"[HamClock] Parsing spacewx data: {data[:200]}...")

        for line in data.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                key_lower = key.strip().lower()
                value = value.strip()

                # Map HamClock keys to our display keys
                if key_lower == 'sfi' or 'flux' in key_lower:
                    result['sfi'] = value
                elif key_lower == 'kp':
                    result['kp'] = value
                elif key_lower == 'a' or 'a_index' in key_lower:
                    result['a'] = value
                elif key_lower == 'xray':
                    result['xray'] = value
                elif key_lower == 'ssn' or 'sunspot' in key_lower:
                    result['sunspots'] = value
                elif key_lower == 'proton' or 'pf' in key_lower:
                    result['proton'] = value
                elif key_lower == 'aurora' or 'aur' in key_lower:
                    result['aurora'] = value

        logger.debug(f"[HamClock] Parsed values: {result}")
        return result

    def _on_fetch_noaa(self, button):
        """Fetch space weather data from NOAA using SpaceWeatherAPI"""
        logger.info("[HamClock] NOAA fetch button clicked")
        self.status_label.set_label("Fetching NOAA SWPC data...")

        def fetch():
            try:
                if HAS_SPACE_WEATHER and SpaceWeatherAPI:
                    # Use centralized SpaceWeatherAPI
                    api = SpaceWeatherAPI(timeout=15)
                    data = api.get_current_conditions()
                    GLib.idle_add(self._update_noaa_display, data)
                else:
                    # Fallback to direct API call
                    noaa_url = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"
                    req = urllib.request.Request(noaa_url)
                    req.add_header('User-Agent', 'MeshForge/1.0')

                    with urllib.request.urlopen(req, timeout=10) as response:
                        raw_data = json.loads(response.read().decode('utf-8'))

                    if raw_data and len(raw_data) > 0:
                        latest = raw_data[-1]
                        GLib.idle_add(self._update_noaa_display_legacy, latest)
                    else:
                        GLib.idle_add(lambda: self.status_label.set_label("No NOAA data"))

            except Exception as e:
                logger.error(f"NOAA fetch error: {e}")
                GLib.idle_add(lambda: self.status_label.set_label(f"NOAA error: {str(e)[:50]}"))

        threading.Thread(target=fetch, daemon=True).start()

    def _on_fetch_voacap(self, button):
        """Fetch VOACAP propagation predictions from HamClock"""
        logger.info("[HamClock] VOACAP fetch button clicked")

        url = self._settings.get("url", "").rstrip('/')
        api_port = self._settings.get("api_port", 8080)

        if not url:
            self.status_label.set_label("Configure HamClock URL first")
            return

        self.status_label.set_label("Fetching VOACAP data...")

        def fetch():
            api_url = f"{url}:{api_port}"
            voacap_data = {}

            try:
                # HamClock VOACAP endpoint
                full_url = f"{api_url}/get_voacap.txt"
                logger.debug(f"[HamClock] Fetching VOACAP: {full_url}")

                req = urllib.request.Request(full_url, method='GET')
                req.add_header('User-Agent', 'MeshForge/1.0')
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = response.read().decode('utf-8')
                    logger.debug(f"[HamClock] VOACAP response: {data[:500]}...")
                    voacap_data = self._parse_voacap(data)

            except urllib.error.HTTPError as e:
                logger.debug(f"[HamClock] VOACAP HTTP error: {e.code}")
                GLib.idle_add(lambda: self.status_label.set_label(f"VOACAP: HTTP {e.code}"))
                return
            except urllib.error.URLError as e:
                logger.debug(f"[HamClock] VOACAP URL error: {e.reason}")
                GLib.idle_add(lambda: self.status_label.set_label("VOACAP: Connection failed"))
                return
            except Exception as e:
                logger.error(f"[HamClock] VOACAP error: {e}")
                GLib.idle_add(lambda: self.status_label.set_label(f"VOACAP error: {str(e)[:40]}"))
                return

            GLib.idle_add(self._update_voacap_display, voacap_data)

        threading.Thread(target=fetch, daemon=True).start()

    def _parse_voacap(self, data):
        """
        Parse VOACAP response from HamClock.

        HamClock VOACAP response format (example):
            Path=DE to DX
            UTC=14
            80m=23,12
            40m=67,24
            30m=89,32
            20m=95,38
            17m=78,28
            15m=45,18
            12m=12,8
            10m=5,2

        Where values are reliability%,SNR_dB

        Args:
            data: Raw text response from HamClock

        Returns:
            Dictionary with parsed VOACAP data
        """
        result = {
            'bands': {},
            'path': '',
            'utc': '',
            'raw': data
        }

        logger.debug("[HamClock] Parsing VOACAP data...")

        for line in data.strip().split('\n'):
            line = line.strip()
            if not line or '=' not in line:
                continue

            key, value = line.split('=', 1)
            key = key.strip().lower()
            value = value.strip()

            if key == 'path':
                result['path'] = value
            elif key == 'utc':
                result['utc'] = value
            elif 'm' in key:
                # Band data (e.g., "80m", "40m")
                band_key = key.replace('m', 'm')  # Normalize
                try:
                    if ',' in value:
                        rel, snr = value.split(',', 1)
                        result['bands'][band_key] = {
                            'reliability': int(rel.strip()),
                            'snr': int(snr.strip())
                        }
                    else:
                        # Just reliability
                        result['bands'][band_key] = {
                            'reliability': int(value),
                            'snr': 0
                        }
                except ValueError as e:
                    logger.debug(f"[HamClock] Could not parse band {key}: {value} - {e}")

        logger.debug(f"[HamClock] Parsed VOACAP: {len(result['bands'])} bands, path={result['path']}")
        return result
