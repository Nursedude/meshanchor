"""RNS/NomadNet node collection for coverage maps.

Extracted from map_data_collector.py for file size compliance (CLAUDE.md #6).

Expects the following on the host class:
- self._is_valid_coordinate(lat, lon): coordinate validator
- self._make_feature(...): GeoJSON feature builder
- self._is_node_online(last_heard, source): online status check
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')

logger = logging.getLogger(__name__)


def _rns_is_initialized() -> bool:
    """Return True if an RNS.Reticulum instance has already been constructed.

    RNS uses a process-wide singleton — ``RNS.Reticulum()`` raises
    ``OSError("Attempt to reinitialise Reticulum, when it was already
    running")`` on the second construction. The collector runs every
    cycle; we must init exactly once per process and read
    ``Transport.path_table`` directly on subsequent cycles.

    Uses the public ``RNS.Reticulum.get_instance()`` rather than peeking
    at the name-mangled ``_Reticulum__instance`` attribute, which is
    fragile across rns minor versions.
    """
    if not _HAS_RNS:
        return False
    try:
        return _RNS.Reticulum.get_instance() is not None
    except Exception:
        return False


class RNSDataCollectorMixin:
    """Mixin providing RNS data collection methods for MapDataCollector."""

    def _collect_rns_direct(self) -> List[Dict]:
        """Collect RNS nodes directly from rnsd shared instance.

        Queries the RNS path table for known destinations when rnsd is running.
        This supplements the temp cache file with live data from rnsd.

        Returns:
            List of GeoJSON features for RNS destinations with stored positions.
        """
        features = []

        # Quick check if rnsd shared instance is available
        try:
            from utils.service_check import check_rns_shared_instance
            if not check_rns_shared_instance():
                logger.debug("rnsd shared instance not available")
                return []
        except ImportError:
            pass  # Proceed without pre-check

        if not _HAS_RNS:
            logger.debug("RNS module not available for direct query")
            return []

        # Load RNS position cache for coordinate lookup
        rns_positions = self._load_rns_position_cache()

        try:
            # Initialize the Reticulum client ONCE per process. Subsequent
            # cycles read Transport.path_table directly — it's a class-level
            # singleton that stays live. Calling Reticulum(...) a second
            # time raises OSError (see _rns_is_initialized() above).
            if not _rns_is_initialized():
                # Connect as a client to the running rnsd shared instance.
                # Use a temp client-only config to avoid:
                # 1. Creating a default config at /root/.reticulum/ (MF001)
                # 2. Initializing interfaces that conflict with rnsd.
                import tempfile
                from utils.paths import ReticulumPaths
                from utils.rns_init import open_reticulum
                instance_name = ReticulumPaths.get_configured_instance_name()
                client_config_dir = Path(tempfile.gettempdir()) / "meshanchor_rns_client"
                client_config_dir.mkdir(exist_ok=True)
                client_config_file = client_config_dir / "config"
                client_config_file.write_text(
                    "[reticulum]\n"
                    "  share_instance = Yes\n"
                    "  shared_instance_port = 37428\n"
                    "  instance_control_port = 37429\n"
                    f"  instance_name = {instance_name}\n"
                )
                # Pure consumer: require_listener=True so a missing shared
                # instance NEVER makes the map collector become the @rns host
                # (the 2026-05-28 ~21h fleet outage shape). The chokepoint
                # also fails open on a wedged rnsd (#68) and fails loud on a
                # foreign @rns owner (#69), and handles the reinitialise /
                # signal-in-thread cases internally — returns None on degrade.
                if open_reticulum(str(client_config_dir), require_listener=True) is None:
                    logger.debug("RNS degraded this cycle — no shared instance")
                    return []

            # Check for known destinations in path table
            if hasattr(_RNS.Transport, 'path_table') and _RNS.Transport.path_table:
                for dest_hash, path_data in _RNS.Transport.path_table.items():
                    try:
                        if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                            hash_hex = dest_hash.hex()
                            node_id = f"rns_{hash_hex[:16]}"

                            # Extract hop count from path tuple if available
                            hops = 0
                            if isinstance(path_data, tuple) and len(path_data) > 1:
                                hops = path_data[1]

                            # Look up position from cache
                            pos = rns_positions.get(hash_hex[:16])
                            lat = pos.get("lat") if pos else None
                            lon = pos.get("lon") if pos else None
                            name = (pos.get("name") if pos else None) or f"RNS:{hash_hex[:8]}"

                            if self._is_valid_coordinate(lat, lon):
                                rns_last_heard = pos.get("last_heard", 0) if pos else 0
                                feature = self._make_feature(
                                    node_id=node_id,
                                    name=name,
                                    lat=lat, lon=lon,
                                    network="rns",
                                    is_online=self._is_node_online(rns_last_heard, source="rns"),
                                    last_heard=rns_last_heard,
                                )
                                features.append(feature)

                    except Exception as e:
                        logger.debug(f"Error processing RNS destination: {e}")

            # Also check NomadNet peer cache if available
            nomadnet_peers = self._load_nomadnet_peers()
            for peer in nomadnet_peers:
                feature = self._rns_peer_to_feature(peer)
                if feature:
                    features.append(feature)

            if features:
                logger.debug(f"RNS direct: {len(features)} nodes with position")
            else:
                # Log how many RNS destinations we found (even without position)
                path_count = len(_RNS.Transport.path_table) if hasattr(_RNS.Transport, 'path_table') and _RNS.Transport.path_table else 0
                if path_count:
                    logger.debug(
                        f"RNS: {path_count} destinations in path table, "
                        f"{len(rns_positions)} have cached positions"
                    )

        except Exception as e:
            logger.debug(f"RNS direct query error: {e}")

        return features

    def _load_rns_position_cache(self) -> Dict[str, Dict]:
        """Load RNS node position cache for coordinate lookup.

        Reads the RNS nodes cache (MeshAnchorPaths.rns_nodes_cache_path) and
        node_cache.json to build a hash -> {lat, lon, name} mapping.
        """
        positions: Dict[str, Dict] = {}

        # Source 1: RNS nodes cache (operator-owned; shared path via the writer)
        from utils.paths import MeshAnchorPaths
        rns_cache = MeshAnchorPaths.rns_nodes_cache_path()
        if rns_cache.exists():
            try:
                with open(rns_cache) as f:
                    data = json.load(f)
                nodes_list = data if isinstance(data, list) else data.get("nodes", [])
                for node in nodes_list:
                    rns_hash = node.get("id", node.get("rns_hash", ""))
                    if isinstance(rns_hash, str):
                        rns_hash = rns_hash.replace("rns_", "")[:16]
                    lat = node.get("latitude") or node.get("lat")
                    lon = node.get("longitude") or node.get("lon")
                    if lat is not None and lon is not None and rns_hash:
                        positions[rns_hash] = {
                            "lat": lat, "lon": lon,
                            "name": node.get("name", node.get("display_name", "")),
                            # Stamp freshness (coerced to epoch) so path-table
                            # nodes aren't rendered permanently offline: the cache
                            # never carried last_heard, so _is_node_online(0) was
                            # always False. An ISO-string last_seen is coerced,
                            # not passed raw (would TypeError-drop the node in
                            # _is_node_online). (QA audit 2026-07-06.)
                            "last_heard": self._coerce_epoch(
                                node.get("last_heard") or node.get("last_seen")
                                or node.get("timestamp") or 0),
                        }
            except Exception as e:
                logger.debug(f"RNS position cache load error: {e}")

        # Source 2: Node tracker cache (RNS entries)
        cache_path = get_real_user_home() / ".config" / "meshanchor" / "node_cache.json"

        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    data = json.load(f)
                nodes_list = data if isinstance(data, list) else data.get("nodes", [])
                for node in nodes_list:
                    if node.get("network") == "rns":
                        rns_hash = node.get("id", node.get("rns_hash", ""))
                        if isinstance(rns_hash, str):
                            rns_hash = rns_hash.replace("rns_", "")[:16]
                        lat = node.get("latitude") or node.get("lat")
                        lon = node.get("longitude") or node.get("lon")
                        if lat is not None and lon is not None and rns_hash:
                            positions[rns_hash] = {
                                "lat": lat, "lon": lon,
                                "name": node.get("name", ""),
                                "last_heard": self._coerce_epoch(
                                    node.get("last_heard") or node.get("last_seen")
                                    or node.get("timestamp") or 0),
                            }
            except Exception:
                pass

        return positions

    def _load_nomadnet_peers(self) -> List[Dict]:
        """Load known peers from NomadNet cache if available."""
        peers = []
        if not _HAS_MSGPACK:
            logger.debug("msgpack not available for NomadNet peer reading")
            return peers
        try:
            nomadnet_dir = get_real_user_home() / '.nomadnetwork'
            peer_file = nomadnet_dir / 'storage' / 'peers'
            if peer_file.exists():
                with open(peer_file, 'rb') as f:
                    data = _msgpack.unpack(f, raw=False)
                    if isinstance(data, dict):
                        for peer_hash, peer_data in data.items():
                            if isinstance(peer_data, dict):
                                peers.append({
                                    'hash': peer_hash.hex() if isinstance(peer_hash, bytes) else peer_hash,
                                    'name': peer_data.get('display_name', ''),
                                    'lat': peer_data.get('latitude'),
                                    'lon': peer_data.get('longitude'),
                                })
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"NomadNet peer loading error: {e}")
        return peers

    def _rns_peer_to_feature(self, peer: Dict) -> Optional[Dict]:
        """Convert NomadNet peer entry to GeoJSON feature."""
        lat = peer.get('lat')
        lon = peer.get('lon')

        if not self._is_valid_coordinate(lat, lon):
            return None

        peer_hash = peer.get('hash', 'unknown')
        return self._make_feature(
            node_id=f"rns_{peer_hash[:16]}",
            name=peer.get('name', f"RNS:{peer_hash[:8]}"),
            lat=lat, lon=lon,
            network="rns",
            is_online=True,
        )

    def _node_cache_to_feature(self, node: Dict) -> Optional[Dict]:
        """Convert a node cache entry to a GeoJSON feature."""
        lat = node.get("latitude") or node.get("lat")
        lon = node.get("longitude") or node.get("lon")

        if not self._is_valid_coordinate(lat, lon):
            pos = node.get("position", {})
            if pos:
                # Convert *I forms only when actually present — a missing axis
                # must stay None, not become 0.0 (which validate-accepts as a
                # one-axis-zero → a phantom equator/meridian node). (QA audit.)
                lat_i = pos.get("latitudeI")
                lon_i = pos.get("longitudeI")
                lat = pos.get("latitude")
                if lat is None and lat_i is not None:
                    lat = lat_i / 1e7
                lon = pos.get("longitude")
                if lon is None and lon_i is not None:
                    lon = lon_i / 1e7

        if not self._is_valid_coordinate(lat, lon):
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

        if not self._is_valid_coordinate(lat, lon):
            pos = node.get("position", {})
            if pos:
                # Only take axes actually present — defaulting a missing axis to
                # 0 makes a phantom prime-meridian node. (QA audit 2026-07-06.)
                if pos.get("latitude") is not None:
                    lat = pos.get("latitude")
                if pos.get("longitude") is not None:
                    lon = pos.get("longitude")

        if not self._is_valid_coordinate(lat, lon):
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
