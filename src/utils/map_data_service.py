"""
Map Data Service - Unified node GeoJSON from all available sources.

Collects node data from meshtasticd, MQTT, and RNS node tracker,
merges into a single GeoJSON FeatureCollection, and optionally
serves via HTTP for the live map to consume.

Usage:
    # Collector only (for TUI integration)
    from utils.map_data_service import MapDataCollector
    collector = MapDataCollector()
    geojson = collector.collect()

    # Full HTTP server (for browser access)
    from utils.map_data_service import MapServer
    server = MapServer(port=5000)
    server.start()  # Serves map + API at http://localhost:5000
"""

import json
import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MapDataCollector:
    """Collects node data from all available sources into unified GeoJSON.

    Sources (tried in order, all merged):
    1. meshtasticd TCP (localhost:4403) — local mesh nodes
    2. MQTT subscriber — global/regional nodes
    3. Node tracker cache — previously discovered RNS + Meshtastic nodes
    4. Last-known cache — persisted state from previous runs
    """

    def __init__(self, cache_dir: Optional[Path] = None, enable_history: bool = True):
        if cache_dir:
            self._cache_dir = cache_dir
        else:
            try:
                from utils.paths import get_real_user_home
                self._cache_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            except ImportError:
                self._cache_dir = Path("/tmp/meshforge")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "map_nodes.geojson"
        self._last_collect: Optional[float] = None
        self._cached_geojson: Optional[Dict] = None

        # Node history database for position/state tracking over time
        self._history = None
        if enable_history:
            try:
                from utils.node_history import NodeHistoryDB
                db_path = self._cache_dir / "node_history.db"
                self._history = NodeHistoryDB(db_path=db_path)
            except Exception as e:
                logger.debug(f"Node history disabled: {e}")

    def collect(self, max_age_seconds: int = 30) -> Dict[str, Any]:
        """Collect nodes from all sources, merge, and return GeoJSON.

        Args:
            max_age_seconds: Use cached data if collected within this window.

        Returns:
            GeoJSON FeatureCollection with all known nodes.
        """
        # Use cache if fresh enough
        if (self._cached_geojson and self._last_collect and
                time.time() - self._last_collect < max_age_seconds):
            return self._cached_geojson

        features: Dict[str, Dict] = {}  # id -> feature (dedup by id)

        # Source 1: meshtasticd TCP
        tcp_features = self._collect_meshtasticd()
        for f in tcp_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 2: MQTT subscriber (if running)
        mqtt_features = self._collect_mqtt()
        for f in mqtt_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f
            elif fid and fid in features:
                # Merge: prefer newer data
                self._merge_feature(features[fid], f)

        # Source 3: Node tracker cache files
        tracker_features = self._collect_node_tracker()
        for f in tracker_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4: Last-known cache (fill gaps)
        if not features:
            cache_features = self._load_cache()
            for f in cache_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        geojson = {
            "type": "FeatureCollection",
            "features": list(features.values()),
            "properties": {
                "collected_at": datetime.now().isoformat(),
                "source_count": len(features),
                "sources": self._get_source_summary(tcp_features, mqtt_features, tracker_features)
            }
        }

        # Cache result
        self._cached_geojson = geojson
        self._last_collect = time.time()
        self._save_cache(geojson)

        # Record to history database
        if self._history and geojson["features"]:
            try:
                self._history.record_observations(geojson["features"])
            except Exception as e:
                logger.debug(f"History recording error: {e}")

        return geojson

    def _collect_meshtasticd(self) -> List[Dict]:
        """Collect nodes from meshtasticd via TCP:4403.

        Strategy:
        1. Try the Python TCP interface (structured data, most reliable)
        2. Fall back to CLI parsing if Python module unavailable
        """
        features = []

        # Quick check if port is open before attempting connection
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', 4403))
            sock.close()
            if result != 0:
                return []
        except OSError:
            return []

        # Strategy 1: Use Python TCP interface via connection manager
        features = self._collect_via_tcp_interface()
        if features:
            return features

        # Strategy 2: Fall back to CLI parsing
        features = self._collect_via_cli()
        if features:
            logger.debug(f"meshtasticd (CLI): {len(features)} nodes with position")

        return features

    def _collect_via_tcp_interface(self) -> List[Dict]:
        """Collect nodes using the meshtastic Python TCP interface.

        Uses MeshtasticConnectionManager for safe locking and cleanup.
        Returns list of GeoJSON features for nodes with valid positions.
        """
        try:
            from utils.meshtastic_connection import get_connection_manager
        except ImportError:
            logger.debug("meshtastic_connection module not available")
            return []

        features = []
        manager = get_connection_manager()

        # Don't block if someone else holds the connection
        if not manager.acquire_lock(timeout=5.0):
            logger.debug("Could not acquire meshtasticd lock (in use)")
            return []

        try:
            manager._wait_for_cooldown()
            interface = manager._create_interface()

            try:
                if hasattr(interface, 'nodes') and interface.nodes:
                    now = time.time()
                    for node_id, node_data in interface.nodes.items():
                        feature = self._parse_tcp_node(node_id, node_data, now)
                        if feature:
                            features.append(feature)

                    if features:
                        logger.debug(f"meshtasticd (TCP): {len(features)} nodes with position")
            finally:
                from utils.meshtastic_connection import safe_close_interface
                safe_close_interface(interface)

        except Exception as e:
            logger.debug(f"TCP interface collection error: {e}")
        finally:
            manager.release_lock()

        return features

    def _parse_tcp_node(self, node_id: str, data: dict, now: float) -> Optional[Dict]:
        """Parse a single node from the TCP interface nodes dict.

        Handles both float (latitude) and integer (latitudeI) coordinate formats.
        """
        position = data.get('position', {})
        if not position:
            return None

        # Extract coordinates - prefer float, fall back to integer / 1e7
        lat = position.get('latitude')
        if lat is None:
            lat_i = position.get('latitudeI')
            lat = lat_i / 1e7 if lat_i is not None else None

        lon = position.get('longitude')
        if lon is None:
            lon_i = position.get('longitudeI')
            lon = lon_i / 1e7 if lon_i is not None else None

        # Skip nodes without valid coordinates
        if lat is None or lon is None:
            return None
        if abs(lat) < 0.001 and abs(lon) < 0.001:
            return None

        # Extract user info
        user = data.get('user', {})
        device_metrics = data.get('deviceMetrics', {})

        # Determine online status from lastHeard (15 min threshold)
        last_heard = data.get('lastHeard', 0)
        is_online = (now - last_heard) < 900 if last_heard else False

        # Format last_seen as human-readable
        if last_heard:
            age_seconds = int(now - last_heard)
            if age_seconds < 60:
                last_seen = f"{age_seconds}s ago"
            elif age_seconds < 3600:
                last_seen = f"{age_seconds // 60}m ago"
            elif age_seconds < 86400:
                last_seen = f"{age_seconds // 3600}h ago"
            else:
                last_seen = f"{age_seconds // 86400}d ago"
        else:
            last_seen = "unknown"

        # Format node_id
        node_num = data.get('num', 0)
        if isinstance(node_id, str) and node_id.startswith('!'):
            formatted_id = node_id
        elif node_num:
            formatted_id = f"!{node_num:08x}"
        else:
            formatted_id = str(node_id)

        return self._make_feature(
            node_id=formatted_id,
            name=user.get('longName', '') or user.get('shortName', ''),
            lat=lat,
            lon=lon,
            network='meshtastic',
            is_online=is_online,
            snr=data.get('snr'),
            battery=device_metrics.get('batteryLevel'),
            hardware=user.get('hwModel', ''),
            role=user.get('role', ''),
            is_gateway=user.get('role', '') in ('ROUTER', 'ROUTER_CLIENT'),
            via_mqtt=data.get('viaMqtt', False),
            is_local=(data.get('hopsAway', 99) == 0),
            last_seen=last_seen,
        )

    def _collect_via_cli(self) -> List[Dict]:
        """Fall back to CLI parsing when Python TCP interface unavailable."""
        try:
            result = subprocess.run(
                ['meshtastic', '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                return []
            return self._parse_meshtastic_info(result.stdout)
        except FileNotFoundError:
            logger.debug("meshtastic CLI not found")
        except subprocess.TimeoutExpired:
            logger.debug("meshtastic CLI timed out")
        except Exception as e:
            logger.debug(f"CLI collection error: {e}")
        return []

    def _parse_meshtastic_info(self, output: str) -> List[Dict]:
        """Parse meshtastic --info output for node positions.

        Handles JSON node data that some versions of the CLI output.
        This is a fallback — the TCP interface is preferred.
        """
        features = []
        lines = output.split('\n')

        for line in lines:
            # Try to parse JSON-like node data from --info output
            if '{' in line and ('position' in line.lower() or 'latitude' in line.lower()):
                try:
                    start = line.index('{')
                    data = json.loads(line[start:])
                    if 'position' in data:
                        pos = data['position']
                        lat = pos.get('latitude')
                        if lat is None:
                            lat_i = pos.get('latitudeI')
                            lat = lat_i / 1e7 if lat_i else None
                        lon = pos.get('longitude')
                        if lon is None:
                            lon_i = pos.get('longitudeI')
                            lon = lon_i / 1e7 if lon_i else None

                        if lat and lon and not (abs(lat) < 0.001 and abs(lon) < 0.001):
                            user = data.get('user', {})
                            device_metrics = data.get('deviceMetrics', {})
                            feature = self._make_feature(
                                node_id=data.get('num', data.get('id', 'unknown')),
                                name=user.get('longName', ''),
                                lat=lat, lon=lon,
                                network='meshtastic',
                                is_online=True,
                                snr=data.get('snr'),
                                battery=device_metrics.get('batteryLevel'),
                                hardware=user.get('hwModel', ''),
                                role=user.get('role', ''),
                            )
                            features.append(feature)
                except (json.JSONDecodeError, ValueError, IndexError):
                    continue

        return features

    def _collect_mqtt(self) -> List[Dict]:
        """Collect nodes from MQTT subscriber if available."""
        try:
            from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
            # Check if there's a running instance with cached data
            # The subscriber stores nodes in memory, so we need a running instance
            # For now, check if there's a cached MQTT node file
            mqtt_cache = self._cache_dir / "mqtt_nodes.json"
            if mqtt_cache.exists():
                age = time.time() - mqtt_cache.stat().st_mtime
                if age < 300:  # Less than 5 minutes old
                    with open(mqtt_cache) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        return data.get("features", [])
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"MQTT collection error: {e}")

        return []

    def _collect_node_tracker(self) -> List[Dict]:
        """Collect nodes from UnifiedNodeTracker cache files."""
        features = []

        # Check node_cache.json
        try:
            from utils.paths import get_real_user_home
            cache_path = get_real_user_home() / ".config" / "meshforge" / "node_cache.json"
        except ImportError:
            import os as _os
            sudo_user = _os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                cache_path = Path(f'/home/{sudo_user}/.config/meshforge/node_cache.json')
            else:
                # Avoid Path.home() which returns /root under sudo (MF001)
                cache_path = Path('/tmp/meshforge/node_cache.json')

        if cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
                if age < 3600:  # Less than 1 hour old
                    with open(cache_path) as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        for node in data:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict) and "nodes" in data:
                        for node in data["nodes"]:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
            except Exception as e:
                logger.debug(f"Node cache read error: {e}")

        # Check RNS nodes temp file
        rns_cache = Path("/tmp/meshforge_rns_nodes.json")
        if rns_cache.exists():
            try:
                age = time.time() - rns_cache.stat().st_mtime
                if age < 600:  # Less than 10 minutes old
                    with open(rns_cache) as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        for node in data:
                            feature = self._rns_cache_to_feature(node)
                            if feature:
                                features.append(feature)
            except Exception as e:
                logger.debug(f"RNS cache read error: {e}")

        return features

    def _node_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert a node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not lat or not lon:
            pos = node.get("position", {})
            if pos:
                lat = pos.get("latitude") or (pos.get("latitudeI", 0) / 1e7)
                lon = pos.get("longitude") or (pos.get("longitudeI", 0) / 1e7)

        if not lat or not lon or (abs(lat) < 0.001 and abs(lon) < 0.001):
            return None

        return self._make_feature(
            node_id=node.get("id", node.get("node_id", "unknown")),
            name=node.get("name", node.get("long_name", "")),
            lat=lat, lon=lon,
            network=node.get("network", "meshtastic"),
            is_online=node.get("is_online", False),
            snr=node.get("snr"),
            battery=node.get("battery", node.get("battery_level")),
            hardware=node.get("hardware", node.get("hardware_model", "")),
            role=node.get("role", ""),
            is_gateway=node.get("is_gateway", False),
            via_mqtt=node.get("via_mqtt", False),
            last_seen=node.get("last_seen", ""),
        )

    def _rns_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert an RNS node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not lat or not lon:
            pos = node.get("position", {})
            if pos:
                lat = pos.get("latitude", 0)
                lon = pos.get("longitude", 0)

        if not lat or not lon or (abs(lat) < 0.001 and abs(lon) < 0.001):
            return None

        return self._make_feature(
            node_id=node.get("id", node.get("rns_hash", "unknown")),
            name=node.get("name", node.get("display_name", "")),
            lat=lat, lon=lon,
            network="rns",
            is_online=node.get("is_online", False),
            snr=node.get("snr"),
            battery=node.get("battery"),
            hardware=node.get("hardware_model", ""),
            role=node.get("role", ""),
            is_gateway=node.get("is_gateway", False),
            last_seen=node.get("last_seen", ""),
        )

    def _make_feature(self, node_id: str, name: str, lat: float, lon: float,
                      network: str = "meshtastic", is_online: bool = True,
                      snr: Optional[float] = None, battery: Optional[int] = None,
                      hardware: str = "", role: str = "",
                      is_gateway: bool = False, via_mqtt: bool = False,
                      is_local: bool = False, last_seen: str = "") -> Dict:
        """Create a GeoJSON Feature for a node."""
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": {
                "id": str(node_id),
                "name": name or str(node_id),
                "network": network,
                "is_online": is_online,
                "is_local": is_local,
                "is_gateway": is_gateway,
                "via_mqtt": via_mqtt,
                "snr": snr,
                "battery": battery,
                "last_seen": last_seen or ("online" if is_online else "unknown"),
                "hardware": hardware,
                "role": role,
            }
        }

    def _merge_feature(self, existing: Dict, new: Dict) -> None:
        """Merge new feature data into existing (prefer non-null values)."""
        for key, value in new["properties"].items():
            if value is not None and value != "" and value != "unknown":
                existing_val = existing["properties"].get(key)
                if existing_val is None or existing_val == "" or existing_val == "unknown":
                    existing["properties"][key] = value

    def _load_cache(self) -> List[Dict]:
        """Load last-known node state from disk cache."""
        if self._cache_file.exists():
            try:
                age = time.time() - self._cache_file.stat().st_mtime
                if age < 86400:  # Less than 24 hours old
                    with open(self._cache_file) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        # Mark all cached nodes as potentially offline
                        for feature in data.get("features", []):
                            if age > 900:  # 15 minutes
                                feature["properties"]["is_online"] = False
                                feature["properties"]["last_seen"] = "cached"
                        return data.get("features", [])
            except Exception as e:
                logger.debug(f"Cache load error: {e}")
        return []

    def _save_cache(self, geojson: Dict) -> None:
        """Persist current node state to disk."""
        try:
            with open(self._cache_file, 'w') as f:
                json.dump(geojson, f)
        except Exception as e:
            logger.debug(f"Cache save error: {e}")

    def _get_source_summary(self, tcp: List, mqtt: List, tracker: List) -> Dict:
        """Summarize which sources contributed data."""
        return {
            "meshtasticd": len(tcp),
            "mqtt": len(mqtt),
            "node_tracker": len(tracker),
        }


class MapRequestHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves the map HTML and node GeoJSON API."""

    collector: Optional[MapDataCollector] = None
    web_dir: Optional[str] = None

    def do_GET(self):
        if self.path == '/api/nodes/geojson' or self.path == '/api/nodes/geojson/':
            self._serve_geojson()
        elif self.path == '/' or self.path == '/index.html':
            self._serve_map()
        elif self.path == '/api/status':
            self._serve_status()
        elif self.path == '/api/nodes/history':
            self._serve_history_stats()
        elif self.path.startswith('/api/nodes/trajectory/'):
            node_id = self.path.split('/api/nodes/trajectory/', 1)[1].rstrip('/')
            self._serve_trajectory(node_id)
        else:
            # Serve static files from web/ directory
            if self.web_dir:
                self.directory = self.web_dir
            super().do_GET()

    def _serve_geojson(self):
        """Serve live node GeoJSON."""
        if self.collector:
            geojson = self.collector.collect()
        else:
            geojson = {"type": "FeatureCollection", "features": []}

        data = json.dumps(geojson).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        origin = self.headers.get('Origin', '')
        if origin.startswith(('http://localhost', 'http://127.0.0.1',
                              'https://localhost', 'https://127.0.0.1')):
            self.send_header('Access-Control-Allow-Origin', origin)
        else:
            self.send_header('Access-Control-Allow-Origin', 'http://localhost:5000')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _serve_map(self):
        """Serve the node_map.html file."""
        if self.web_dir:
            map_path = Path(self.web_dir) / "node_map.html"
        else:
            map_path = Path(__file__).parent.parent.parent / "web" / "node_map.html"

        if map_path.exists():
            with open(map_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404, f"Map file not found: {map_path}")

    def _serve_status(self):
        """Serve server status."""
        status = {
            "status": "running",
            "time": datetime.now().isoformat(),
            "collector": self.collector is not None,
        }

        # Include history stats if available
        if self.collector and self.collector._history:
            try:
                status["history"] = self.collector._history.get_stats()
            except Exception:
                status["history"] = None

        data = json.dumps(status).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_history_stats(self):
        """Serve node history summary and unique nodes list."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available", "nodes": []})
            return

        history = self.collector._history
        result = {
            "stats": history.get_stats(),
            "nodes": history.get_unique_nodes(hours=24),
        }
        self._serve_json(result)

    def _serve_trajectory(self, node_id: str):
        """Serve trajectory GeoJSON for a specific node."""
        if not self.collector or not self.collector._history:
            self._serve_json({"error": "history not available"})
            return

        # URL decode the node_id (! becomes %21 in URLs)
        from urllib.parse import unquote
        node_id = unquote(node_id)

        history = self.collector._history
        geojson = history.get_trajectory_geojson(node_id, hours=24)
        self._serve_json(geojson)

    def _serve_json(self, obj: Any):
        """Helper to serve a JSON response."""
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        origin = self.headers.get('Origin', '')
        if origin.startswith(('http://localhost', 'http://127.0.0.1',
                              'https://localhost', 'https://127.0.0.1')):
            self.send_header('Access-Control-Allow-Origin', origin)
        else:
            self.send_header('Access-Control-Allow-Origin', 'http://localhost:5000')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        """Suppress default request logging (too noisy)."""
        logger.debug(f"MapServer: {args[0]}")


class MapServer:
    """Simple HTTP server for the live network map.

    Serves:
    - GET /              → node_map.html (the live map)
    - GET /api/nodes/geojson  → live node GeoJSON from all sources
    - GET /api/nodes/history  → node history stats + unique nodes (24h)
    - GET /api/nodes/trajectory/<id> → trajectory GeoJSON for a node
    - GET /api/status    → server health check + history stats
    - GET /*             → static files from web/

    Usage:
        server = MapServer(port=5000)
        server.start()     # Blocks
        # or
        server.start_background()  # Returns immediately
    """

    def __init__(self, port: int = 5000, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.collector = MapDataCollector()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

        # Find web directory
        src_dir = Path(__file__).parent.parent
        self.web_dir = str(src_dir.parent / "web")

    def start(self):
        """Start server (blocking)."""
        MapRequestHandler.collector = self.collector
        MapRequestHandler.web_dir = self.web_dir

        self._server = HTTPServer((self.host, self.port), MapRequestHandler)
        logger.info(f"Map server starting on http://{self.host}:{self.port}")
        print(f"MeshForge Map Server: http://localhost:{self.port}")
        print(f"  Map:  http://localhost:{self.port}/")
        print(f"  API:  http://localhost:{self.port}/api/nodes/geojson")
        print("  Press Ctrl+C to stop")

        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down map server...")
            self._server.shutdown()

    def start_background(self):
        """Start server in background thread."""
        MapRequestHandler.collector = self.collector
        MapRequestHandler.web_dir = self.web_dir

        self._server = HTTPServer((self.host, self.port), MapRequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Map server running in background on port {self.port}")

    def stop(self):
        """Stop the server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def url(self) -> str:
        """Get the server URL."""
        return f"http://localhost:{self.port}"


def main():
    """Run the map server standalone."""
    import argparse

    parser = argparse.ArgumentParser(description="MeshForge Live Map Server")
    parser.add_argument("-p", "--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--collect-only", action="store_true", help="Just collect and print GeoJSON")
    args = parser.parse_args()

    if args.collect_only:
        collector = MapDataCollector()
        geojson = collector.collect()
        print(json.dumps(geojson, indent=2))
        print(f"\n# {len(geojson['features'])} nodes collected", flush=True)
    else:
        server = MapServer(port=args.port, host=args.host)
        server.start()


if __name__ == "__main__":
    main()
