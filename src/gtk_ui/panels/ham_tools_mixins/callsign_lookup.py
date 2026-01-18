"""Callsign Lookup Mixin - Callook, HamQTH, QRZ.com API support

Provides callsign lookup functionality via multiple services:
- Callook.info (FCC data, no auth required)
- HamQTH.com (free, requires registration)
- QRZ.com (subscription for full XML access)

This mixin requires the following attributes on the class:
- _settings: dict with hamqth_username, hamqth_password, qrz_username, qrz_password
- _hamqth_user_entry, _hamqth_pass_entry: GTK Entry widgets
- _qrz_user_entry, _qrz_pass_entry: GTK Entry widgets
- _callsign_entry: GTK Entry for callsign input
- _lookup_source: GTK ComboBoxText for source selection
- _callsign_results: GTK TextView for results
- _recent_store: GTK ListStore for recent lookups
- _output_message(message): Method to output log messages
- _save_settings(): Method to persist settings
"""

import threading
import urllib.request
import urllib.error
import urllib.parse
import json

from gi.repository import GLib


class CallsignLookupMixin:
    """Mixin providing callsign lookup functionality."""

    # Session cache for API services (not persisted)
    _hamqth_session_id = None
    _qrz_session_key = None

    def _on_save_credentials(self, button):
        """Save API credentials"""
        self._settings["hamqth_username"] = self._hamqth_user_entry.get_text().strip()
        self._settings["hamqth_password"] = self._hamqth_pass_entry.get_text()
        self._settings["qrz_username"] = self._qrz_user_entry.get_text().strip()
        self._settings["qrz_password"] = self._qrz_pass_entry.get_text()
        self._save_settings()
        # Clear cached sessions when credentials change
        CallsignLookupMixin._hamqth_session_id = None
        CallsignLookupMixin._qrz_session_key = None
        self._output_message("Credentials saved")

    def _on_lookup_callsign(self, widget):
        """Lookup callsign"""
        callsign = self._callsign_entry.get_text().strip().upper()
        if not callsign:
            return

        source = self._lookup_source.get_active_id()
        self._output_message(f"Looking up {callsign} via {source}...")

        def do_lookup():
            try:
                if source == "callook":
                    self._lookup_callook(callsign)
                elif source == "hamqth":
                    self._lookup_hamqth(callsign)
                elif source == "qrz":
                    self._lookup_qrz(callsign)
                else:
                    GLib.idle_add(self._output_message, f"Unknown source: {source}")

            except Exception as e:
                GLib.idle_add(self._output_message, f"Lookup error: {e}")

        threading.Thread(target=do_lookup, daemon=True).start()

    def _lookup_callook(self, callsign: str):
        """Lookup callsign via Callook.info (FCC data, no auth required)"""
        url = f"https://callook.info/{callsign}/json"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            GLib.idle_add(self._display_callsign_result, callsign, data, "callook")

    def _lookup_hamqth(self, callsign: str):
        """Lookup callsign via HamQTH API"""
        import xml.etree.ElementTree as ET

        username = self._settings.get("hamqth_username", "")
        password = self._settings.get("hamqth_password", "")

        if not username or not password:
            GLib.idle_add(self._output_message, "HamQTH requires credentials. Enter them in API Credentials section.")
            return

        # Get or refresh session
        session_id = CallsignLookupMixin._hamqth_session_id
        if not session_id:
            GLib.idle_add(self._output_message, "Authenticating with HamQTH...")
            session_id = self._hamqth_get_session(username, password)
            if not session_id:
                return

        # Lookup callsign
        lookup_url = f"https://www.hamqth.com/xml.php?id={session_id}&callsign={callsign}&prg=MeshForge"
        req = urllib.request.Request(lookup_url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('utf-8')

        root = ET.fromstring(data)

        # Check for errors (session expired, etc.)
        error = root.findtext('.//error')
        if error:
            if 'session' in error.lower():
                # Session expired, re-authenticate
                CallsignLookupMixin._hamqth_session_id = None
                GLib.idle_add(self._output_message, "Session expired, re-authenticating...")
                session_id = self._hamqth_get_session(username, password)
                if session_id:
                    # Retry lookup
                    self._lookup_hamqth(callsign)
                return
            else:
                GLib.idle_add(self._output_message, f"HamQTH error: {error}")
                return

        # Parse search result
        search = root.find('.//search')
        if search is not None:
            result = {
                'callsign': search.findtext('callsign', ''),
                'nick': search.findtext('nick', ''),
                'name': search.findtext('adr_name', ''),
                'qth': search.findtext('qth', ''),
                'country': search.findtext('country', ''),
                'grid': search.findtext('grid', ''),
                'latitude': search.findtext('latitude', ''),
                'longitude': search.findtext('longitude', ''),
                'continent': search.findtext('continent', ''),
                'utc_offset': search.findtext('utc_offset', ''),
                'email': search.findtext('email', ''),
                'qsl_via': search.findtext('qsl_via', ''),
            }
            GLib.idle_add(self._display_callsign_result, callsign, result, "hamqth")
        else:
            GLib.idle_add(self._output_message, f"Callsign {callsign} not found in HamQTH")

    def _hamqth_get_session(self, username: str, password: str) -> str:
        """Get HamQTH session ID"""
        import xml.etree.ElementTree as ET

        auth_url = f"https://www.hamqth.com/xml.php?u={urllib.parse.quote(username)}&p={urllib.parse.quote(password)}"
        req = urllib.request.Request(auth_url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read().decode('utf-8')

            root = ET.fromstring(data)
            session_id = root.findtext('.//session_id')
            error = root.findtext('.//error')

            if session_id:
                CallsignLookupMixin._hamqth_session_id = session_id
                GLib.idle_add(self._output_message, "HamQTH session established")
                return session_id
            elif error:
                GLib.idle_add(self._output_message, f"HamQTH auth error: {error}")
                return None
            else:
                GLib.idle_add(self._output_message, "HamQTH auth failed: no session returned")
                return None

        except Exception as e:
            GLib.idle_add(self._output_message, f"HamQTH auth error: {e}")
            return None

    def _lookup_qrz(self, callsign: str):
        """Lookup callsign via QRZ.com XML API"""
        import xml.etree.ElementTree as ET

        username = self._settings.get("qrz_username", "")
        password = self._settings.get("qrz_password", "")

        if not username or not password:
            GLib.idle_add(self._output_message, "QRZ requires credentials. Enter them in API Credentials section.")
            GLib.idle_add(self._output_message, "Note: QRZ XML requires a subscription (free tier has limits).")
            return

        # Get or refresh session
        session_key = CallsignLookupMixin._qrz_session_key
        if not session_key:
            GLib.idle_add(self._output_message, "Authenticating with QRZ.com...")
            session_key = self._qrz_get_session(username, password)
            if not session_key:
                return

        # Lookup callsign
        lookup_url = f"https://xmldata.qrz.com/xml/current/?s={session_key};callsign={callsign}"
        req = urllib.request.Request(lookup_url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('utf-8')

        root = ET.fromstring(data)
        ns = {'qrz': 'http://xmldata.qrz.com'}

        # Check for errors
        session = root.find('.//Session', ns) or root.find('.//Session')
        if session is not None:
            error = session.findtext('Error', None, ns) or session.findtext('Error')
            if error:
                if 'session' in error.lower() or 'invalid' in error.lower():
                    # Session expired
                    CallsignLookupMixin._qrz_session_key = None
                    GLib.idle_add(self._output_message, "Session expired, re-authenticating...")
                    session_key = self._qrz_get_session(username, password)
                    if session_key:
                        self._lookup_qrz(callsign)
                    return
                else:
                    GLib.idle_add(self._output_message, f"QRZ error: {error}")
                    return

        # Parse callsign data
        callsign_elem = root.find('.//Callsign', ns) or root.find('.//Callsign')
        if callsign_elem is not None:
            # Helper to find text with or without namespace
            def get_text(elem, tag, default=''):
                val = elem.findtext(tag, None, ns)
                if val is None:
                    val = elem.findtext(tag)
                return val if val else default

            result = {
                'call': get_text(callsign_elem, 'call'),
                'name': f"{get_text(callsign_elem, 'fname')} {get_text(callsign_elem, 'name')}".strip(),
                'addr1': get_text(callsign_elem, 'addr1'),
                'addr2': get_text(callsign_elem, 'addr2'),
                'state': get_text(callsign_elem, 'state'),
                'zip': get_text(callsign_elem, 'zip'),
                'country': get_text(callsign_elem, 'country'),
                'grid': get_text(callsign_elem, 'grid'),
                'lat': get_text(callsign_elem, 'lat'),
                'lon': get_text(callsign_elem, 'lon'),
                'class': get_text(callsign_elem, 'class'),
                'email': get_text(callsign_elem, 'email'),
                'qsl_mgr': get_text(callsign_elem, 'qslmgr'),
            }
            GLib.idle_add(self._display_callsign_result, callsign, result, "qrz")
        else:
            GLib.idle_add(self._output_message, f"Callsign {callsign} not found in QRZ")

    def _qrz_get_session(self, username: str, password: str) -> str:
        """Get QRZ.com session key"""
        import xml.etree.ElementTree as ET

        auth_url = f"https://xmldata.qrz.com/xml/current/?username={urllib.parse.quote(username)};password={urllib.parse.quote(password)};agent=MeshForge"
        req = urllib.request.Request(auth_url)
        req.add_header('User-Agent', 'MeshForge/1.0')

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read().decode('utf-8')

            root = ET.fromstring(data)
            ns = {'qrz': 'http://xmldata.qrz.com'}

            # Try with and without namespace
            session = root.find('.//Session', ns) or root.find('.//Session')
            if session is not None:
                key = session.findtext('Key', None, ns) or session.findtext('Key')
                error = session.findtext('Error', None, ns) or session.findtext('Error')

                if key:
                    CallsignLookupMixin._qrz_session_key = key
                    GLib.idle_add(self._output_message, "QRZ session established")
                    return key
                elif error:
                    GLib.idle_add(self._output_message, f"QRZ auth error: {error}")
                    return None

            GLib.idle_add(self._output_message, "QRZ auth failed: no session returned")
            return None

        except Exception as e:
            GLib.idle_add(self._output_message, f"QRZ auth error: {e}")
            return None

    def _display_callsign_result(self, callsign: str, data: dict, source: str = "callook"):
        """Display callsign lookup result from various sources"""
        buffer = self._callsign_results.get_buffer()

        if source == "callook":
            # Callook.info (FCC) format
            if data.get('status') == 'VALID':
                name = data.get('name', 'Unknown')
                addr = data.get('address', {})
                location = f"{addr.get('city', '')}, {addr.get('state', '')}"
                lic_class = data.get('current', {}).get('operClass', 'Unknown')
                grant_date = data.get('current', {}).get('grantDate', 'Unknown')
                grid = data.get('location', {}).get('gridsquare', '')

                result = f"""=== Callook.info (FCC) ===
Callsign: {callsign}
Name: {name}
Location: {location}
Grid: {grid}
Class: {lic_class}
Grant Date: {grant_date}
"""
                buffer.set_text(result)
                self._recent_store.insert(0, [callsign, name, location])
                self._output_message(f"Found: {callsign} - {name}")
            else:
                buffer.set_text(f"Callsign {callsign} not found in FCC database")
                self._output_message(f"Callsign {callsign} not found")

        elif source == "hamqth":
            # HamQTH format
            name = data.get('name', '') or data.get('nick', '') or 'Unknown'
            qth = data.get('qth', '')
            country = data.get('country', '')
            location = f"{qth}, {country}".strip(', ')
            grid = data.get('grid', '')
            lat = data.get('latitude', '')
            lon = data.get('longitude', '')
            email = data.get('email', '')
            qsl = data.get('qsl_via', '')

            result = f"""=== HamQTH ===
Callsign: {data.get('callsign', callsign)}
Name: {name}
QTH: {qth}
Country: {country}
Grid: {grid}
"""
            if lat and lon:
                result += f"Coordinates: {lat}, {lon}\n"
            if email:
                result += f"Email: {email}\n"
            if qsl:
                result += f"QSL Via: {qsl}\n"

            buffer.set_text(result)
            self._recent_store.insert(0, [callsign, name, location])
            self._output_message(f"Found: {callsign} - {name}")

        elif source == "qrz":
            # QRZ.com format
            name = data.get('name', 'Unknown')
            addr1 = data.get('addr1', '')
            addr2 = data.get('addr2', '')
            state = data.get('state', '')
            country = data.get('country', '')
            location = ', '.join(filter(None, [addr2, state, country]))
            grid = data.get('grid', '')
            lat = data.get('lat', '')
            lon = data.get('lon', '')
            lic_class = data.get('class', '')
            email = data.get('email', '')
            qsl_mgr = data.get('qsl_mgr', '')

            result = f"""=== QRZ.com ===
Callsign: {data.get('call', callsign)}
Name: {name}
Address: {addr1}
Location: {location}
Grid: {grid}
Class: {lic_class}
"""
            if lat and lon:
                result += f"Coordinates: {lat}, {lon}\n"
            if email:
                result += f"Email: {email}\n"
            if qsl_mgr:
                result += f"QSL Manager: {qsl_mgr}\n"

            buffer.set_text(result)
            self._recent_store.insert(0, [callsign, name, location])
            self._output_message(f"Found: {callsign} - {name}")

        else:
            buffer.set_text(f"Unknown source: {source}")
            self._output_message(f"Unknown source: {source}")
