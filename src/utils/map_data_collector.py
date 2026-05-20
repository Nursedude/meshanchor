"""
Map Data Collector - Unified node GeoJSON from all available sources.

Collects node data from meshtasticd, MQTT, RNS node tracker, and AREDN,
merges into a single GeoJSON FeatureCollection.

This module provides the data collection logic. For the HTTP server,
see map_data_service.py.

Usage:
    from utils.map_data_collector import MapDataCollector
    collector = MapDataCollector()
    geojson = collector.collect()
"""

import json
import logging
import math
import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Imports ---
from utils.safe_import import safe_import

from utils.paths import get_real_user_home
from utils.common import SettingsManager
from gateway.node_tracker import get_node_tracker
from monitoring.mqtt_subscriber import get_local_subscriber
from utils.aredn import AREDNScanner, AREDNClient

# External/optional dependencies
_RNS, _HAS_RNS = safe_import('RNS')
_msgpack, _HAS_MSGPACK = safe_import('msgpack')


from utils._map_collector_rns import RNSDataCollectorMixin
from utils._map_collector_meshtastic import MeshtasticDataCollectorMixin
from utils._map_collector_meshcore_public import MeshCorePublicCollectorMixin
from utils.meshcore_positions import get_position_store


class MapDataCollector(
    MeshtasticDataCollectorMixin,
    RNSDataCollectorMixin,
    MeshCorePublicCollectorMixin,
):
    """Collects node data from all available sources into unified GeoJSON.

    Sources (tried in order, all merged):
    1. meshtasticd TCP (localhost:4403) - local mesh nodes
    2. MQTT subscriber - global/regional nodes
    3. Node tracker cache - previously discovered RNS + Meshtastic nodes
    4. Last-known cache - persisted state from previous runs

    Settings (in ~/.config/meshanchor/map_settings.json):
    - node_cache_max_age_hours: Max age for node_cache.json (default: 48)
    - rns_cache_max_age_hours: Max age for RNS temp cache (default: 1)
    - online_status_threshold_minutes: Minutes since lastHeard to consider online (default: 15)
    """

    # Default cache ages in hours
    DEFAULT_NODE_CACHE_MAX_AGE_HOURS = 48
    DEFAULT_RNS_CACHE_MAX_AGE_HOURS = 24  # Increased from 1 hour
    DEFAULT_ONLINE_THRESHOLD_MINUTES = 15
    # Per-source online thresholds (minutes) — configurable via map_settings.json
    DEFAULT_MESHTASTIC_THRESHOLD_MINUTES = 15
    DEFAULT_MQTT_THRESHOLD_MINUTES = 15
    DEFAULT_RNS_THRESHOLD_MINUTES = 30   # RNS announces less frequently
    DEFAULT_AREDN_THRESHOLD_MINUTES = 60  # AREDN scans are infrequent
    # Periodic background refresh — without this, _collect_locked() runs
    # only when /api/nodes/geojson is hit. A box whose map UI isn'''t
    # visited accumulates no node-history writes; the directory freezes
    # at the last visit. Verified 2026-05-20 on meshanchor-server:
    # 8.5 d stall with the daemon running, because nothing was calling
    # the trigger endpoint. 300 s keeps directory.last_seen well inside
    # the fleet-collector'''s 60 s poll budget with headroom. Set to 0 in
    # map_settings.json to disable (tests, or boxes that drive collect()
    # from elsewhere).
    DEFAULT_PERIODIC_REFRESH_SECONDS = 300
    # Meshtasticd connection defaults
    DEFAULT_MESHTASTICD_HOST = "localhost"
    DEFAULT_MESHTASTICD_PORT = 4403
    # Daemon /radio HTTP endpoint (cross-process self-feature lookup).
    DAEMON_RADIO_URL = "http://127.0.0.1:8081/radio"
    DAEMON_RADIO_TTL_SECONDS = 10.0

    def __init__(self, cache_dir: Optional[Path] = None, enable_history: bool = True,
                 meshtastic_enabled: bool = True, meshforge_maps_enabled: bool = True):
        # Phase 1 of MeshCore-primary TUI rework: when the active deployment
        # profile disables Meshtastic, skip meshtasticd polling entirely.
        # MeshCore is treated as a first-class source via _collect_meshcore().
        self._meshtastic_enabled = meshtastic_enabled
        # Phase 6.1: optional pull from meshforge-maps' aggregated /api/nodes/geojson.
        # Defaults to True so localhost-meshforge-maps installs benefit without
        # explicit opt-in; safe even when meshforge-maps isn't running because
        # MeshforgeMapsClient.fetch_nodes() returns None on any failure and the
        # collector treats None as "no contribution this cycle".
        self._meshforge_maps_enabled = meshforge_maps_enabled

        if cache_dir:
            self._cache_dir = cache_dir
        else:
            self._cache_dir = get_real_user_home() / ".local" / "share" / "meshanchor"

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "map_nodes.geojson"
        self._last_collect: Optional[float] = None
        self._cached_geojson: Optional[Dict] = None
        # Serializes expensive collect() runs when ThreadingHTTPServer
        # dispatches concurrent requests. Without this, every worker
        # re-runs the full path_table walk + MeshCore/MQTT fetch at once.
        self._collect_lock = threading.Lock()

        # Periodic background refresh state — started via
        # start_periodic_refresh() from the daemon entry point so unit
        # tests / CLI usage that construct a collector don'''t spawn a
        # surprise thread.
        self._periodic_refresh_thread: Optional[threading.Thread] = None
        self._periodic_refresh_stop = threading.Event()
        self._periodic_refresh_interval: float = 0.0

        # User-configurable cache age settings
        self._settings = SettingsManager(
            "map_settings",
            defaults={
                "node_cache_max_age_hours": self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS,
                "rns_cache_max_age_hours": self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS,
                "online_status_threshold_minutes": self.DEFAULT_ONLINE_THRESHOLD_MINUTES,
                "meshtasticd_host": self.DEFAULT_MESHTASTICD_HOST,
                "meshtasticd_port": self.DEFAULT_MESHTASTICD_PORT,
                "aredn_node_ips": [],  # e.g. ["10.54.25.1", "10.1.0.1"]
                # Per-source online thresholds (minutes)
                "meshtastic_threshold_minutes": self.DEFAULT_MESHTASTIC_THRESHOLD_MINUTES,
                "mqtt_threshold_minutes": self.DEFAULT_MQTT_THRESHOLD_MINUTES,
                "rns_threshold_minutes": self.DEFAULT_RNS_THRESHOLD_MINUTES,
                "aredn_threshold_minutes": self.DEFAULT_AREDN_THRESHOLD_MINUTES,
                # Map-serve filter: drop features older than N days at the
                # /api/nodes/geojson endpoint. 0 = no filter. Default 30 days
                # because the public meshcore.dev fetcher returns 40k+
                # historical nodes most of which are dead; without a cutoff,
                # browser cluster-index build dominates page-load time.
                "max_age_days": 30,
                # Map-serve filter: drop features outside the named region.
                # Keys: us | na | hi | eu | as | oc | world. Default ``us``
                # for North-America-band radios; switch to ``world`` to see
                # everything or ``hi``/``eu``/etc. to scope tighter.
                "region": "us",
                # Drives the periodic _collect_locked() heartbeat — see
                # DEFAULT_PERIODIC_REFRESH_SECONDS comment for the why.
                "periodic_refresh_seconds": self.DEFAULT_PERIODIC_REFRESH_SECONDS,
            }
        )

        # Daemon /radio HTTP cache — see _fetch_daemon_radio_state.
        # Cached for DAEMON_RADIO_TTL_SECONDS so concurrent map requests
        # don't hammer the daemon every collect cycle.
        self._daemon_radio_state: Optional[Dict[str, Any]] = None
        self._daemon_radio_state_at: float = 0.0

        # Track nodes without GPS for reporting
        self._nodes_without_position: List[Dict] = []
        self._total_nodes_seen: int = 0  # Total from meshtasticd (with + without GPS)

        # Node history database for position/state tracking over time
        self._history = None
        if enable_history:
            try:
                from utils.node_history import NodeHistoryDB
                db_path = self._cache_dir / "node_history.db"
                self._history = NodeHistoryDB(db_path=db_path)
            except Exception as e:
                logger.debug(f"Node history disabled: {e}")

    @staticmethod
    def _is_valid_coordinate(lat, lon) -> bool:
        """Validate geographic coordinates.

        Rejects:
        - None values
        - NaN or Infinity
        - Out-of-range (lat must be -90..90, lon must be -180..180)
        - Default zero (both lat AND lon are exactly 0 — unset GPS)

        Accepts:
        - Nodes near the equator/prime meridian where only ONE coord is near zero
        - Any valid coordinate pair within range
        """
        if lat is None or lon is None:
            return False
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if lat < -90 or lat > 90 or lon < -180 or lon > 180:
            return False
        # Reject default-zero GPS (both exactly 0.0 = unset), but allow
        # nodes where only one axis is near zero (legitimate equator/meridian)
        if lat == 0.0 and lon == 0.0:
            return False
        return True

    def get_node_cache_max_age_seconds(self) -> int:
        """Get max age for node_cache.json in seconds."""
        if self._settings:
            hours = self._settings.get("node_cache_max_age_hours", self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS)
        else:
            hours = self.DEFAULT_NODE_CACHE_MAX_AGE_HOURS
        return int(hours * 3600)

    def get_rns_cache_max_age_seconds(self) -> int:
        """Get max age for RNS temp cache in seconds."""
        if self._settings:
            hours = self._settings.get("rns_cache_max_age_hours", self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS)
        else:
            hours = self.DEFAULT_RNS_CACHE_MAX_AGE_HOURS
        return int(hours * 3600)

    def set_node_cache_max_age_hours(self, hours: int) -> None:
        """Set max age for node_cache.json in hours."""
        if self._settings:
            self._settings.set("node_cache_max_age_hours", hours)
            self._settings.save()
            logger.info(f"Node cache max age set to {hours} hours")

    def set_rns_cache_max_age_hours(self, hours: int) -> None:
        """Set max age for RNS temp cache in hours."""
        if self._settings:
            self._settings.set("rns_cache_max_age_hours", hours)
            self._settings.save()
            logger.info(f"RNS cache max age set to {hours} hours")

    def get_online_threshold_seconds(self) -> int:
        """Get online status threshold in seconds.

        Nodes heard within this threshold are considered online.
        Default: 15 minutes (900 seconds).
        """
        if self._settings:
            minutes = self._settings.get("online_status_threshold_minutes", self.DEFAULT_ONLINE_THRESHOLD_MINUTES)
        else:
            minutes = self.DEFAULT_ONLINE_THRESHOLD_MINUTES
        return int(minutes * 60)

    def set_online_threshold_minutes(self, minutes: int) -> None:
        """Set online status threshold in minutes.

        Args:
            minutes: Consider nodes online if heard within this many minutes.
                    Use higher values for networks with longer update intervals.
        """
        if self._settings:
            self._settings.set("online_status_threshold_minutes", minutes)
            self._settings.save()
            logger.info(f"Online status threshold set to {minutes} minutes")

    def get_source_threshold_seconds(self, source: str) -> int:
        """Get online threshold for a specific network source.

        Per-source thresholds allow different timeout windows per network type:
        - meshtastic: 15 min (frequent heartbeats)
        - mqtt: 15 min (real-time broker)
        - rns: 30 min (announces less frequently)
        - aredn: 60 min (scans are infrequent)

        Falls back to the global online_status_threshold_minutes setting.

        Args:
            source: Network source type ("meshtastic", "mqtt", "rns", "aredn")

        Returns:
            Threshold in seconds
        """
        key = f"{source}_threshold_minutes"
        defaults = {
            "meshtastic": self.DEFAULT_MESHTASTIC_THRESHOLD_MINUTES,
            "mqtt": self.DEFAULT_MQTT_THRESHOLD_MINUTES,
            "rns": self.DEFAULT_RNS_THRESHOLD_MINUTES,
            "aredn": self.DEFAULT_AREDN_THRESHOLD_MINUTES,
        }
        default = defaults.get(source, self.DEFAULT_ONLINE_THRESHOLD_MINUTES)
        if self._settings:
            minutes = self._settings.get(key, default)
        else:
            minutes = default
        return int(minutes * 60)

    def _is_node_online(self, last_heard: float, source: str = "meshtastic") -> bool:
        """Determine if a node is online based on last_heard timestamp.

        Single source of truth for online status determination.
        Uses per-source thresholds for accurate status across network types.

        Args:
            last_heard: Unix timestamp of last communication (0 or None = unknown)
            source: Network source type for threshold lookup

        Returns:
            True if the node was heard within the source's threshold window
        """
        if not last_heard or last_heard <= 0:
            return False
        threshold = self.get_source_threshold_seconds(source)
        return (time.time() - last_heard) < threshold

    def get_meshtasticd_host(self) -> str:
        """Get meshtasticd host setting."""
        if self._settings:
            return self._settings.get("meshtasticd_host", self.DEFAULT_MESHTASTICD_HOST)
        return self.DEFAULT_MESHTASTICD_HOST

    def get_meshtasticd_port(self) -> int:
        """Get meshtasticd port setting."""
        if self._settings:
            return int(self._settings.get("meshtasticd_port", self.DEFAULT_MESHTASTICD_PORT))
        return self.DEFAULT_MESHTASTICD_PORT

    def set_meshtasticd_connection(self, host: str, port: int) -> None:
        """Set meshtasticd connection parameters.

        Args:
            host: Hostname or IP address of meshtasticd
            port: TCP port (default: 4403)
        """
        if self._settings:
            self._settings.set("meshtasticd_host", host)
            self._settings.set("meshtasticd_port", port)
            self._settings.save()
            logger.info(f"Meshtasticd connection set to {host}:{port}")

    def get_nodes_without_position(self) -> List[Dict]:
        """Get list of nodes that have no GPS position.

        Returns list of dicts with id, name, last_seen, network info.
        Updated after each collect() call.
        """
        return self._nodes_without_position

    def collect(self, max_age_seconds: int = 30) -> Dict[str, Any]:
        """Collect nodes from all sources, merge, and return GeoJSON.

        Stale-while-revalidate: if a cached result exists but is older
        than ``max_age_seconds``, return it immediately and refresh in a
        background thread (one refresh at a time). Only the very first
        call — when no cache exists yet — blocks for a full collect
        cycle. This keeps the geojson endpoint responsive on Pi-class
        hardware where a cold collect (42k features × json.dumps + SQLite
        history writes) can take 5–17s.

        Args:
            max_age_seconds: Use cached data if collected within this
                window. Pass 0 to force a synchronous collect (used by
                tests and explicit refresh paths).

        Returns:
            GeoJSON FeatureCollection with all known nodes.
        """
        # Fast-path cache check without lock — safe because both attrs are
        # written atomically at the end of _collect_locked().
        if (self._cached_geojson and self._last_collect and
                time.time() - self._last_collect < max_age_seconds):
            return self._cached_geojson

        # max_age_seconds=0 = explicit synchronous collect (test path).
        if max_age_seconds == 0:
            with self._collect_lock:
                return self._collect_locked()

        # Stale-but-cached: serve stale, refresh in background. Use
        # non-blocking acquire so a refresh already in flight doesn't
        # spawn duplicates — the running thread will release the lock.
        if self._cached_geojson is not None:
            if self._collect_lock.acquire(blocking=False):
                threading.Thread(
                    target=self._refresh_in_background,
                    name="MapDataCollector-refresh",
                    daemon=True,
                ).start()
            return self._cached_geojson

        # Cold start (no cache yet): must block for the first collect.
        with self._collect_lock:
            if self._cached_geojson is not None:
                return self._cached_geojson
            return self._collect_locked()

    def _refresh_in_background(self) -> None:
        """Run a collect cycle, then release the lock.

        Caller must already hold ``_collect_lock`` (acquired non-blockingly
        from ``collect()``). Exceptions are logged so a transient failure
        doesn't poison subsequent refreshes.
        """
        try:
            self._collect_locked()
        except Exception:
            logger.exception("Background map data refresh failed")
        finally:
            self._collect_lock.release()

    @staticmethod
    def _tag_source_origin(features: List[Dict[str, Any]], origin: str) -> List[Dict[str, Any]]:
        """Stamp `properties.source_origin` on every feature in-place.

        Drives the directory-table tiered retention (Issue #49):
        external-bulk origins (meshcore_public, aredn_worldmap,
        mqtt_global) age out at 7d; locally-RX'd origins (local_radio,
        rns_path_table, aredn_local, mqtt_local) age out at 30d.

        The unified_tracker source produces a mix — its features can be
        either Meshtastic (local_radio) or RNS (rns_path_table); that
        method tags itself per-feature based on `properties.network`.
        Other sources are uniform per call site.

        First-tag-wins: if a feature already carries a source_origin (set
        by a higher-trust collector earlier this cycle), don't overwrite.
        """
        for f in features:
            props = f.get("properties")
            if not isinstance(props, dict):
                continue
            if not props.get("source_origin"):
                props["source_origin"] = origin
        return features

    def start_periodic_refresh(
        self, interval_seconds: Optional[float] = None,
    ) -> None:
        """Drive _collect_locked() on a fixed cadence, independent of HTTP demand.

        The cache TTL inside collect() (30 s default) only matters when a
        caller actually invokes collect(). Without a periodic driver,
        boxes whose map UI is unvisited accumulate zero node-history
        writes, freezing /api/nodes/directory at the last visit.
        Verified 2026-05-20 on meshanchor-server (8.5 d stall).

        Interval comes from ``map_settings.periodic_refresh_seconds``
        (default ``DEFAULT_PERIODIC_REFRESH_SECONDS``); pass an explicit
        value to override. 0 or negative disables. Idempotent — a second
        call stops any in-flight thread first.

        Skips ticks while another collect() holds ``_collect_lock`` so a
        slow cycle can'''t queue up backed-up refresh work.
        """
        if interval_seconds is None:
            interval_seconds = float(self._settings.get(
                "periodic_refresh_seconds",
                self.DEFAULT_PERIODIC_REFRESH_SECONDS,
            ))
        if interval_seconds <= 0:
            return
        self.stop_periodic_refresh()
        self._periodic_refresh_interval = interval_seconds
        self._periodic_refresh_stop.clear()
        self._periodic_refresh_thread = threading.Thread(
            target=self._periodic_refresh_loop,
            daemon=True,
            name="MapDataCollector-periodic-refresh",
        )
        self._periodic_refresh_thread.start()

    def stop_periodic_refresh(self) -> None:
        """Stop the periodic refresh thread. Idempotent."""
        self._periodic_refresh_stop.set()
        thread = self._periodic_refresh_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._periodic_refresh_thread = None

    def _periodic_refresh_loop(self) -> None:
        while not self._periodic_refresh_stop.is_set():
            # Event.wait satisfies the MF010 lint rule (no time.sleep in
            # daemon loops) and lets stop_periodic_refresh interrupt the
            # wait without waiting for the next tick.
            if self._periodic_refresh_stop.wait(
                self._periodic_refresh_interval
            ):
                return
            if not self._collect_lock.acquire(blocking=False):
                # An on-demand collect() is in flight; skip this tick
                # rather than queue up redundant work behind it.
                continue
            try:
                self._collect_locked()
            except Exception:
                logger.exception("Periodic map data refresh failed")
            finally:
                self._collect_lock.release()

    def _collect_locked(self) -> Dict[str, Any]:
        """Actual collection body. Caller MUST hold self._collect_lock."""
        features: Dict[str, Dict] = {}  # id -> feature (dedup by id)

        # Reset per-cycle tallies — each collect starts fresh and every
        # source that surfaces position-less nodes EXTENDS the list.
        self._nodes_without_position = []
        self._total_nodes_seen = 0

        # Source 0a: MeshCore — primary radio in MeshAnchor.
        # MeshCore advertisements don't carry GPS today, so positioned
        # MeshCore nodes are rare. Position-less nodes go to the side panel.
        meshcore_features = self._tag_source_origin(self._collect_meshcore(), "local_radio")
        for f in meshcore_features:
            fid = f["properties"].get("id", "")
            if fid:
                features[fid] = f

        # Source 0a-self: the local MeshCore radio itself. The contacts
        # iteration above only yields *discovered* nodes — the operator's
        # own NOC radio never lands there. When /radio reports advertised
        # coords, emit a synthetic feature so "this NOC" shows up on the
        # map without requiring an operator placement.
        self_feature = self._collect_meshcore_self()
        if self_feature is not None:
            self_feature = self._tag_source_origin([self_feature], "local_radio")[0]
            fid = self_feature["properties"].get("id", "")
            if fid:
                features[fid] = self_feature

        # Source 0b: UnifiedNodeTracker (richest data — includes RNS + Meshtastic)
        # This is the same data source the topology view uses (378 nodes).
        # It includes nodes from RNS path table, meshtasticd, and gateway bridge.
        # Tagging is per-feature inside _collect_unified_tracker (mixed RNS + Meshtastic).
        tracker_unified_features = self._collect_unified_tracker()
        for f in tracker_unified_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 1: meshtasticd TCP — only when Meshtastic is enabled in the profile
        if self._meshtastic_enabled:
            tcp_features = self._tag_source_origin(self._collect_meshtasticd(), "local_radio")
            for f in tcp_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

            # Source 1.5: Direct USB radio (when meshtasticd not running)
            # Only try this if TCP returned nothing (avoids double-connection)
            direct_radio_features = []
            if not tcp_features:
                direct_radio_features = self._tag_source_origin(self._collect_direct_radio(), "local_radio")
                for f in direct_radio_features:
                    fid = f["properties"].get("id", "")
                    if fid:
                        features[fid] = f
        else:
            # Meshtastic disabled by deployment profile — skip TCP + direct USB polls
            tcp_features = []
            direct_radio_features = []

        # Source 2: MQTT subscriber (if running). Local subscriber → mqtt_local
        # tier; external/global firehoses (when added) should tag mqtt_global.
        mqtt_features = self._tag_source_origin(self._collect_mqtt(), "mqtt_local")
        for f in mqtt_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f
            elif fid and fid in features:
                # Merge: prefer newer data
                self._merge_feature(features[fid], f)

        # Source 3: Node tracker cache files (locally-cached, replayed on cold start)
        tracker_features = self._tag_source_origin(self._collect_node_tracker(), "node_tracker")
        for f in tracker_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 4: AREDN mesh network
        aredn_features = self._tag_source_origin(self._collect_aredn(), "aredn_local")
        for f in aredn_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5: RNS direct query (from rnsd path table)
        rns_direct_features = self._tag_source_origin(self._collect_rns_direct(), "rns_path_table")
        for f in rns_direct_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 5b: map.meshcore.dev public directory.
        # Operator-submitted MeshCore nodes worldwide (~42k). MeshCore has
        # no MQTT firehose, so this directory IS the live picture for
        # MeshCore. Tagged with the existing `meshcore_public` source
        # origin (already in node_history.py retention tiers; 30 days
        # retention same as aredn_worldmap).
        meshcore_public_features = self._tag_source_origin(
            self._collect_meshcore_public(), "meshcore_public",
        )
        for f in meshcore_public_features:
            fid = f["properties"].get("id", "")
            if fid and fid not in features:
                features[fid] = f

        # Source 6: meshforge-maps aggregated GeoJSON (Phase 6.1).
        # Lowest-priority external bulk source — only fills gaps. Local
        # collectors take precedence so meshforge-maps can't shadow data
        # we already have first-hand.
        if self._meshforge_maps_enabled:
            meshforge_maps_features = self._tag_source_origin(
                self._collect_meshforge_maps(), "external_maps"
            )
            for f in meshforge_maps_features:
                fid = f["properties"].get("id", "")
                if fid and fid not in features:
                    features[fid] = f
        else:
            meshforge_maps_features = []

        # Source 7: Last-known cache (fill gaps). node_tracker tier — the
        # cache is whatever this box has previously seen locally.
        if not features:
            cache_features = self._tag_source_origin(self._load_cache(), "node_tracker")
            for f in cache_features:
                fid = f["properties"].get("id", "")
                if fid:
                    features[fid] = f

        sources = self._get_source_summary(
            tcp_features, mqtt_features, tracker_features, aredn_features,
            direct_radio_features, rns_direct_features, tracker_unified_features,
            meshcore_features, meshforge_maps_features,
            meshcore_public_features,
        )
        geojson = {
            "type": "FeatureCollection",
            "features": list(features.values()),
            "properties": {
                "collected_at": datetime.now().isoformat(),
                "source_count": len(features),
                "sources": sources,
                "total_nodes": self._total_nodes_seen,
                "nodes_with_position": len(features),
                "nodes_without_position": self._nodes_without_position,
                "nodes_without_position_count": len(self._nodes_without_position),
                "online_threshold_minutes": self.get_online_threshold_seconds() // 60,
            }
        }

        # Log collection summary for debugging
        logger.debug(
            f"MapDataCollector: {len(features)} nodes "
            f"(meshcore:{sources.get('meshcore', 0)} "
            f"unified:{sources.get('unified_tracker', 0)} "
            f"meshtasticd:{sources.get('meshtasticd', 0)} "
            f"direct_radio:{sources.get('direct_radio', 0)} "
            f"mqtt:{sources.get('mqtt', 0)} "
            f"tracker:{sources.get('node_tracker', 0)} "
            f"rns_direct:{sources.get('rns_direct', 0)} "
            f"meshcore_public:{sources.get('meshcore_public', 0)})"
        )

        # Cache result
        self._cached_geojson = geojson
        self._last_collect = time.time()
        self._save_cache(geojson)

        # Record to history database. Pass BOTH positioned features AND
        # position-less nodes (synthesized into geometry-less features)
        # so the directory table (Issue #49) gets a row per node we
        # heard about — including MeshCore adverts and RNS announces
        # that carry no GPS by protocol design. The observation-stream
        # writer in record_observations skips position-less rows itself
        # (it requires lat/lon for the time-series); the directory
        # writer handles them via NULL last_lat/last_lon.
        if self._history:
            history_features = list(geojson["features"])
            for entry in self._nodes_without_position:
                nid = entry.get("id")
                if not nid:
                    continue
                # Tier-aware origin tagging mirrors the per-source path:
                # MeshCore via the unified tracker is local-RX (gateway
                # bridge), RNS announces are rns_path_table, everything
                # else falls into local_radio. Position-less features
                # never came through one of the threshold-gated external
                # collectors, so they don't get external_bulk tags.
                net = (entry.get("network") or "").lower()
                if net == "rns":
                    origin = "rns_path_table"
                else:
                    origin = "local_radio"
                history_features.append({
                    "type": "Feature",
                    "geometry": {},  # explicit no-position
                    "properties": {
                        "id": nid,
                        "name": entry.get("name", nid),
                        "network": entry.get("network", "unknown"),
                        "is_online": entry.get("is_online", False),
                        "source_origin": origin,
                    },
                })
            if history_features:
                try:
                    self._history.record_observations(history_features)
                except Exception as e:
                    logger.debug(f"History recording error: {e}")

        return geojson

    def _collect_unified_tracker(self) -> List[Dict]:
        """Collect nodes from the UnifiedNodeTracker singleton.

        The UnifiedNodeTracker is the richest data source — it merges nodes from
        RNS path table, meshtasticd, and the gateway bridge into a unified view.
        This is the same data the Topology view displays.

        Returns:
            List of GeoJSON features for nodes with valid positions.
        """
        try:
            tracker = get_node_tracker()
            geojson = tracker.to_geojson()
            features = geojson.get("features", [])

            if features:
                # Enrich with additional properties the map expects
                for f in features:
                    props = f.get("properties", {})
                    # Ensure standard fields exist
                    if "via_mqtt" not in props:
                        props["via_mqtt"] = False
                    if "hardware" not in props:
                        props["hardware"] = ""
                    if "role" not in props:
                        props["role"] = ""
                    if "source" not in props:
                        props["source"] = "unified_tracker"
                    # Tier-aware tagging for the directory (Issue #49):
                    # the unified tracker mixes RNS + Meshtastic; route
                    # each by its protocol so retention applies correctly.
                    if not props.get("source_origin"):
                        net = (props.get("network") or "").lower()
                        if net == "meshtastic":
                            props["source_origin"] = "local_radio"
                        elif net == "rns":
                            props["source_origin"] = "rns_path_table"
                        else:
                            # MeshCore via tracker is local-RX (gateway bridge);
                            # everything else falls into local_radio bucket.
                            props["source_origin"] = "local_radio"

                logger.debug(
                    f"UnifiedNodeTracker: {len(features)} nodes with position "
                    f"(total tracked: {len(tracker.get_all_nodes())})"
                )
            return features

        except Exception as e:
            logger.debug(f"UnifiedNodeTracker collection error: {e}")
            return []

    def _collect_meshcore(self) -> List[Dict]:
        """Collect nodes from MeshCore — primary radio in MeshAnchor.

        Pulls MeshCore nodes from the UnifiedNodeTracker (populated by the
        gateway bridge's meshcore_handler). Today MeshCore advertisements
        don't carry GPS, so almost all MeshCore nodes land in the
        position-less side panel served via /api/nodes/geojson properties.

        Positioned MeshCore nodes (e.g. when meshcore_py grows telemetry-
        with-position support) are returned as features and dedup'd against
        the unified-tracker source by id in _collect_locked.
        """
        try:
            tracker = get_node_tracker()
            mc_nodes = tracker.get_meshcore_nodes()
        except Exception as e:
            logger.debug(f"MeshCore tracker access error: {e}")
            return []

        if not mc_nodes:
            logger.debug("MeshCore: no nodes in tracker")
            return []

        # Operator-placed positions (~/.config/meshanchor/meshcore_positions.json).
        # MeshCore advertisements don't carry GPS, so a discovered contact
        # only lands as a map marker if either (a) the node grew telemetry-
        # with-position upstream, or (b) the operator manually pinned it
        # via the TUI. Look up by pubkey so the join is stable across
        # name changes.
        try:
            placements = get_position_store().list()
        except Exception as e:
            logger.debug(f"meshcore_positions read error: {e}")
            placements = {}

        features: List[Dict] = []
        position_less: List[Dict] = []
        placed_count = 0
        consumed_pubkeys: set = set()

        for node in mc_nodes:
            position = getattr(node, 'position', None)
            has_position = (
                position is not None
                and self._is_valid_coordinate(
                    getattr(position, 'latitude', None),
                    getattr(position, 'longitude', None),
                )
            )

            if has_position:
                features.append(self._make_feature(
                    node_id=node.id,
                    name=node.name,
                    lat=position.latitude,
                    lon=position.longitude,
                    network="meshcore",
                    is_online=node.is_online,
                    snr=node.snr,
                    rssi=node.rssi,
                    role=getattr(node, 'meshcore_role', '') or '',
                    last_seen=node.get_age_string() if hasattr(node, 'get_age_string') else '',
                ))
                continue

            pubkey = getattr(node, 'meshcore_pubkey', '') or ''
            placement = placements.get(pubkey.lower()) if pubkey else None
            if placement and self._is_valid_coordinate(
                placement.get("lat"), placement.get("lon")
            ):
                feature = self._make_feature(
                    node_id=node.id,
                    name=placement.get("name") or node.name or node.id,
                    lat=placement["lat"],
                    lon=placement["lon"],
                    network="meshcore",
                    is_online=node.is_online,
                    snr=node.snr,
                    rssi=node.rssi,
                    role=getattr(node, 'meshcore_role', '') or '',
                    last_seen=node.get_age_string() if hasattr(node, 'get_age_string') else '',
                )
                # Tag as operator-placed so the UI can distinguish hand-pinned
                # nodes from radio-broadcast positions.
                feature["properties"]["source"] = "meshcore_placed"
                feature["properties"]["placed_at"] = placement.get("set_at")
                if placement.get("notes"):
                    feature["properties"]["notes"] = placement["notes"]
                if placement.get("alt") is not None:
                    feature["properties"]["altitude"] = placement["alt"]
                features.append(feature)
                placed_count += 1
                consumed_pubkeys.add(pubkey.lower())
                continue

            last_seen = (
                node.get_age_string()
                if hasattr(node, 'get_age_string')
                else 'unknown'
            )
            position_less.append({
                "id": node.id,
                "name": node.name or node.id,
                "network": "meshcore",
                "is_online": node.is_online,
                "last_seen": last_seen,
                "role": getattr(node, 'meshcore_role', '') or '',
                "snr": node.snr,
                "rssi": node.rssi,
                "hops_away": getattr(node, 'meshcore_hops', None),
                "pubkey": pubkey,
            })

        # Surface position-less MeshCore nodes via the side-panel pipeline.
        self._nodes_without_position.extend(position_less)
        self._total_nodes_seen += len(mc_nodes)

        # Second pass: placements whose pubkey we've never heard from. The
        # operator may have pre-pinned a friend's node or a known fixed
        # install that simply hasn't broadcast within range yet. Emit it
        # as an offline operator-placed marker so it shows up on the map.
        ghost_placed = 0
        for pubkey, placement in placements.items():
            if pubkey in consumed_pubkeys:
                continue
            if not self._is_valid_coordinate(
                placement.get("lat"), placement.get("lon")
            ):
                continue
            feature = self._make_feature(
                node_id=f"meshcore:{pubkey}",
                name=placement.get("name") or f"MC-{pubkey[:8]}",
                lat=placement["lat"],
                lon=placement["lon"],
                network="meshcore",
                is_online=False,
                snr=None,
                rssi=None,
                role="",
                last_seen="never heard",
            )
            feature["properties"]["source"] = "meshcore_placed"
            feature["properties"]["placed_at"] = placement.get("set_at")
            feature["properties"]["pubkey"] = pubkey
            if placement.get("notes"):
                feature["properties"]["notes"] = placement["notes"]
            if placement.get("alt") is not None:
                feature["properties"]["altitude"] = placement["alt"]
            features.append(feature)
            ghost_placed += 1

        logger.debug(
            f"MeshCore: {len(features)} with GPS "
            f"({placed_count} operator-placed seen, "
            f"{ghost_placed} placed-not-heard), "
            f"{len(position_less)} without GPS (total: {len(mc_nodes)})"
        )
        return features

    def _fetch_daemon_radio_state(self) -> Optional[Dict[str, Any]]:
        """GET /radio from the daemon and return the ``radio`` payload.

        meshanchor-map and meshanchor-daemon are separate processes —
        in-process ``get_active_handler()`` returns None on the map side.
        Cross-process state goes through the daemon's HTTP endpoint.

        Cached for ``DAEMON_RADIO_TTL_SECONDS`` so concurrent collects
        don't pound the daemon. Returns None when the daemon is
        unreachable or the response shape is unexpected.
        """
        now = time.time()
        if (self._daemon_radio_state is not None
                and now - self._daemon_radio_state_at < self.DAEMON_RADIO_TTL_SECONDS):
            return self._daemon_radio_state

        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(
                self.DAEMON_RADIO_URL,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            logger.debug(f"daemon /radio unreachable: {e}")
            self._daemon_radio_state = None
            self._daemon_radio_state_at = now
            return None
        if not isinstance(body, dict):
            return None
        radio = body.get("radio")
        if not isinstance(radio, dict):
            return None
        self._daemon_radio_state = radio
        self._daemon_radio_state_at = now
        return radio

    def _collect_meshcore_self(self) -> Optional[Dict]:
        """Emit a synthetic feature for the local MeshCore radio.

        Fetches radio state from the daemon via ``GET /radio`` because the
        map service runs in a different process from meshanchor-daemon —
        ``get_active_handler()`` would return None here. Returns None when
        the daemon is unreachable, the pubkey is missing, or coords aren't
        valid. The pubkey is the stable feature id — without it dedup
        against discovered contacts can't work safely.
        """
        state = self._fetch_daemon_radio_state()
        if state is None:
            return None

        pubkey = (state.get("public_key") or "").lower()
        lat = state.get("radio_lat")
        lon = state.get("radio_lon")
        if not pubkey or not self._is_valid_coordinate(lat, lon):
            return None

        feature = self._make_feature(
            node_id=f"meshcore:{pubkey}",
            name=state.get("node_name") or f"MC-{pubkey[:8]}",
            lat=lat,
            lon=lon,
            network="meshcore",
            is_online=True,
            is_local=True,
            last_seen="now",
            last_heard=state.get("last_refresh_ts"),
        )
        feature["properties"]["source"] = "meshcore_self"
        feature["properties"]["pubkey"] = pubkey
        feature["properties"]["model"] = state.get("model") or ""
        feature["properties"]["fw_build"] = state.get("fw_build") or ""
        return feature

    def _collect_mqtt(self) -> List[Dict]:
        """Collect nodes from MQTT subscriber if available.

        Tries the live subscriber singleton first (best data, includes sensors),
        then falls back to cached GeoJSON file.
        """
        # Try live subscriber first (has real-time sensor data)
        try:
            subscriber = get_local_subscriber()
            if subscriber.is_connected():
                geojson = subscriber.get_geojson()
                features = geojson.get("features", [])
                if features:
                    logger.debug(f"MQTT live: {len(features)} nodes with position")
                    return features
        except Exception as e:
            logger.debug(f"MQTT live collection error: {e}")

        # Fallback: cached MQTT node file
        try:
            mqtt_cache = self._cache_dir / "mqtt_nodes.json"
            if mqtt_cache.exists():
                age = time.time() - mqtt_cache.stat().st_mtime
                if age < 300:  # Less than 5 minutes old
                    with open(mqtt_cache) as f:
                        data = json.load(f)
                    if data.get("type") == "FeatureCollection":
                        return data.get("features", [])
        except Exception as e:
            logger.debug(f"MQTT cache collection error: {e}")

        return []

    def _collect_node_tracker(self) -> List[Dict]:
        """Collect nodes from UnifiedNodeTracker cache files."""
        features = []

        # Check node_cache.json
        cache_path = get_real_user_home() / ".config" / "meshanchor" / "node_cache.json"

        if cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
                max_age = self.get_node_cache_max_age_seconds()
                if age < max_age:  # Configurable, default 48 hours
                    with open(cache_path) as f:
                        data = json.load(f)

                    # Count nodes for logging
                    total_nodes = 0
                    if isinstance(data, list):
                        total_nodes = len(data)
                        for node in data:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict) and "nodes" in data:
                        total_nodes = len(data["nodes"])
                        for node in data["nodes"]:
                            feature = self._node_cache_to_feature(node)
                            if feature:
                                features.append(feature)
                    elif isinstance(data, dict):
                        # Dict without "nodes" key - log for debugging
                        logger.debug(f"node_cache.json has dict format without 'nodes' key: {list(data.keys())}")

                    if features:
                        logger.debug(f"node_cache: {len(features)}/{total_nodes} nodes with position")
                else:
                    # Cache too old
                    age_hours = age / 3600
                    max_hours = max_age / 3600
                    logger.debug(f"node_cache.json too old: {age_hours:.1f}h > {max_hours:.1f}h max")
            except json.JSONDecodeError as e:
                logger.warning(f"node_cache.json JSON parse error: {e}")
            except PermissionError as e:
                logger.warning(f"node_cache.json permission denied: {e}")
            except Exception as e:
                logger.debug(f"Node cache read error: {e}")
        else:
            logger.debug(f"node_cache.json not found at: {cache_path}")

        # Check RNS nodes temp file
        rns_cache = Path("/tmp/meshanchor_rns_nodes.json")
        if rns_cache.exists():
            rns_count = 0
            try:
                age = time.time() - rns_cache.stat().st_mtime
                max_age = self.get_rns_cache_max_age_seconds()
                if age < max_age:  # Configurable, default 1 hour
                    with open(rns_cache) as f:
                        data = json.load(f)

                    # Handle both list and dict-with-nodes format
                    nodes_list = []
                    if isinstance(data, list):
                        nodes_list = data
                    elif isinstance(data, dict) and "nodes" in data:
                        nodes_list = data["nodes"]

                    for node in nodes_list:
                        feature = self._rns_cache_to_feature(node)
                        if feature:
                            features.append(feature)
                            rns_count += 1

                    if rns_count:
                        logger.debug(f"rns_cache: {rns_count}/{len(nodes_list)} nodes with position")
                else:
                    age_mins = age / 60
                    max_mins = max_age / 60
                    logger.debug(f"RNS cache too old: {age_mins:.0f}m > {max_mins:.0f}m max")
            except Exception as e:
                logger.debug(f"RNS cache read error: {e}")

        return features

    def _collect_aredn(self) -> List[Dict]:
        """Collect nodes from AREDN mesh network.

        Scans the local AREDN network for nodes with GPS coordinates.
        AREDN nodes may have location data configured by the operator.
        """
        features = []

        # First try to connect to the local AREDN node
        local_node_ip = self._get_aredn_node_ip()
        if not local_node_ip:
            logger.debug("No AREDN node found on local network")
            return []

        try:
            # Get the local node info (may have location)
            client = AREDNClient(local_node_ip, timeout=5)
            local_node = client.get_node_info()

            if local_node:
                feature = self._aredn_node_to_feature(local_node)
                if feature:
                    features.append(feature)

                # Get neighbor nodes through links
                for link in local_node.links:
                    if link.ip:
                        try:
                            neighbor_client = AREDNClient(link.ip, timeout=3)
                            neighbor_node = neighbor_client.get_node_info()
                            if neighbor_node:
                                neighbor_feature = self._aredn_node_to_feature(neighbor_node)
                                if neighbor_feature:
                                    # Add link quality info
                                    neighbor_feature["properties"]["link_type"] = link.link_type.value
                                    neighbor_feature["properties"]["link_quality"] = link.link_quality
                                    neighbor_feature["properties"]["snr"] = link.snr if link.snr else None
                                    features.append(neighbor_feature)
                        except Exception as e:
                            logger.debug(f"Error fetching AREDN neighbor {link.ip}: {e}")

            if features:
                logger.debug(f"AREDN: {len(features)} nodes with position")

        except Exception as e:
            logger.debug(f"AREDN collection error: {e}")

        return features

    def _get_aredn_node_ip(self) -> Optional[str]:
        """Find AREDN node on local network.

        Checks user-configured IPs first, then common AREDN defaults.
        Configure via map_settings.json: "aredn_node_ips": ["10.54.25.1"]

        Validates with HTTP API response (not just socket test) to confirm
        the host is actually an AREDN node, not some other service on 8080.
        """
        import socket
        import urllib.request

        # User-configured AREDN node IPs (checked first)
        custom_ips = []
        if self._settings:
            custom_ips = self._settings.get("aredn_node_ips", [])
            if isinstance(custom_ips, str):
                custom_ips = [custom_ips]

        # Common AREDN addresses as fallback
        default_hosts = ['localnode.local.mesh', '10.0.0.1', '10.1.0.1', 'localnode']

        for host in custom_ips + default_hosts:
            try:
                # Quick socket pre-check (2s timeout) to avoid slow HTTP timeouts
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 8080))
                    if result != 0:
                        continue
                finally:
                    sock.close()

                # Validate with actual HTTP API response
                url = f"http://{host}:8080/a/sysinfo"
                req = urllib.request.Request(url, method='GET')
                req.add_header('User-Agent', 'MeshAnchor/1.0')
                with urllib.request.urlopen(req, timeout=3) as response:
                    data = response.read().decode('utf-8')
                    import json as _json
                    info = _json.loads(data)
                    # Verify it looks like an AREDN response
                    if isinstance(info, dict) and ('node' in info or 'sysinfo' in info
                                                    or 'meshrf' in info):
                        logger.debug(f"AREDN node confirmed at {host}")
                        return host
                    else:
                        logger.debug(f"Host {host}:8080 responds but not AREDN format")
            except Exception:
                continue
        return None

    def _aredn_node_to_feature(self, node) -> Optional[Dict]:
        """Convert AREDNNode to GeoJSON feature.

        Args:
            node: AREDNNode object from utils.aredn

        Returns:
            GeoJSON Feature dict or None if no valid location
        """
        # Check for valid location
        if not node.has_location():
            return None

        # Determine online status from scan time — AREDN uses longer threshold
        # If we just scanned it successfully, use current time as last_heard
        aredn_last_heard = time.time()
        is_online = self._is_node_online(aredn_last_heard, source="aredn")

        # Determine if this is a "gateway" type node
        # AREDN nodes with tunnels act as gateways
        try:
            is_gateway = int(node.tunnel_count) > 0
        except (TypeError, ValueError):
            is_gateway = False

        return self._make_feature(
            node_id=f"aredn_{node.hostname}",
            name=node.hostname,
            lat=node.latitude,
            lon=node.longitude,
            network="aredn",
            is_online=is_online,
            is_gateway=is_gateway,
            hardware=node.model,
            last_heard=aredn_last_heard,
            role=node.mesh_status or "AREDN",
            last_seen="online",
        )

    # RNS data collection methods (_collect_rns_direct, _load_rns_position_cache,
    # _load_nomadnet_peers, _rns_peer_to_feature, _node_cache_to_feature,
    # _rns_cache_to_feature) are inherited from RNSDataCollectorMixin
    # in _map_collector_rns.py

    def _make_feature(self, node_id: str, name: str, lat: float, lon: float,
                      network: str = "meshtastic", is_online: bool = False,
                      snr: Optional[float] = None, battery: Optional[int] = None,
                      hardware: str = "", role: str = "",
                      is_gateway: bool = False, via_mqtt: bool = False,
                      is_local: bool = False, last_seen: str = "",
                      last_heard: Optional[float] = None,
                      rssi: Optional[int] = None,
                      temperature: Optional[float] = None,
                      humidity: Optional[float] = None,
                      pressure: Optional[float] = None,
                      pm25: Optional[int] = None,
                      co2: Optional[int] = None,
                      iaq: Optional[int] = None,
                      channel_utilization: Optional[float] = None,
                      air_util_tx: Optional[float] = None,
                      channel_name: str = "",
                      has_encryption: Optional[bool] = None) -> Dict:
        """Create a GeoJSON Feature for a node."""
        props = {
            "id": str(node_id),
            "name": name or str(node_id),
            "network": network,
            "is_online": is_online,
            "is_local": is_local,
            "is_gateway": is_gateway,
            "via_mqtt": via_mqtt,
            "snr": snr,
            "rssi": rssi,
            "battery": battery,
            "last_seen": last_seen or ("online" if is_online else "unknown"),
            "last_heard": last_heard or 0,
            "hardware": hardware,
            "role": role,
        }
        # Add sensor data only when present (avoid cluttering output)
        if temperature is not None:
            props["temperature"] = temperature
        if humidity is not None:
            props["humidity"] = humidity
        if pressure is not None:
            props["pressure"] = pressure
        if pm25 is not None:
            props["pm25"] = pm25
        if co2 is not None:
            props["co2"] = co2
        if iaq is not None:
            props["iaq"] = iaq
        if channel_utilization is not None:
            props["channel_utilization"] = channel_utilization
        if air_util_tx is not None:
            props["air_util_tx"] = air_util_tx
        # Channel/encryption info (Phase 3)
        if channel_name:
            props["channel_name"] = channel_name
        if has_encryption is not None:
            props["has_encryption"] = has_encryption
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": props,
        }

    def _merge_feature(self, existing: Dict, new: Dict) -> None:
        """Merge new feature data into existing.

        - For is_online/last_heard: most recent last_heard wins (freshest data
          determines online status). This prevents stale sources from overriding
          accurate status.
        - For other properties: prefer non-null values (fill gaps).
        """
        new_props = new["properties"]
        ex_props = existing["properties"]

        # Handle is_online via most-recent last_heard
        new_lh = new_props.get("last_heard", 0) or 0
        ex_lh = ex_props.get("last_heard", 0) or 0
        if new_lh > ex_lh:
            ex_props["last_heard"] = new_lh
            if "is_online" in new_props:
                ex_props["is_online"] = new_props["is_online"]

        # Merge other properties (prefer non-null to fill gaps)
        for key, value in new_props.items():
            if key in ("is_online", "last_heard"):
                continue  # Already handled above
            if value is not None and value != "" and value != "unknown":
                existing_val = ex_props.get(key)
                if existing_val is None or existing_val == "" or existing_val == "unknown":
                    ex_props[key] = value

    def _collect_meshforge_maps(self) -> List[Dict]:
        """Pull aggregated GeoJSON from meshforge-maps' :8808 service.

        Phase 6.1 — bidirectional handshake. meshforge-maps already
        aggregates nodes from its own collectors (Meshtastic, MeshCore,
        RNS, MQTT, AREDN, HamClock). MeshAnchor pulls that aggregate as
        a low-priority external_maps tier so anything meshforge-maps
        sees but MeshAnchor's local collectors haven't, fills the gap.
        Local collectors take precedence — the dedup in _collect_locked
        skips IDs already present, so meshforge-maps can't shadow our
        first-hand data.

        Endpoint config (host / port / timeout) comes from Phase 6.3's
        meshforge_maps SettingsManager so a non-localhost deployment
        works without code changes. Fully best-effort — any failure
        (unreachable, non-JSON, malformed shape) returns an empty list
        rather than raising, matching the rest of the collector pipeline.
        """
        try:
            from utils.meshforge_maps_config import load_maps_config
            client = load_maps_config().build_client()
            payload = client.fetch_nodes()
            if payload is None:
                return []
            features = payload.get("features", [])
            if not isinstance(features, list):
                return []
            # Trust the shape — meshforge-maps emits the same FeatureCollection
            # contract MapDataCollector itself produces, so no normalization
            # is needed beyond the Source 6 dedup-by-id in _collect_locked.
            return [f for f in features if isinstance(f, dict)]
        except Exception as e:
            logger.debug(f"meshforge-maps collection error: {e}")
            return []

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

    def _get_source_summary(
        self, tcp: List, mqtt: List, tracker: List, aredn: List = None,
        direct_radio: List = None, rns_direct: List = None,
        unified_tracker: List = None, meshcore: List = None,
        meshforge_maps: List = None, meshcore_public: List = None,
    ) -> Dict:
        """Summarize which sources contributed data."""
        summary = {
            "meshcore": len(meshcore) if meshcore else 0,
            "unified_tracker": len(unified_tracker) if unified_tracker else 0,
            "meshtasticd": len(tcp),
            "direct_radio": len(direct_radio) if direct_radio else 0,
            "mqtt": len(mqtt),
            "node_tracker": len(tracker),
            "aredn": len(aredn) if aredn else 0,
            "rns_direct": len(rns_direct) if rns_direct else 0,
            "meshforge_maps": len(meshforge_maps) if meshforge_maps else 0,
            "meshcore_public": len(meshcore_public) if meshcore_public else 0,
        }
        # Surface meshtastic-disabled state so the map UI / API can
        # display "Meshtastic gateway disabled by profile" if needed.
        summary["meshtastic_enabled"] = self._meshtastic_enabled
        summary["meshforge_maps_enabled"] = self._meshforge_maps_enabled
        # Flag if HTTP was used (source tag on features)
        if tcp and any(f.get("properties", {}).get("source") == "meshtasticd_http" for f in tcp):
            summary["meshtasticd_via"] = "http"
        elif tcp:
            summary["meshtasticd_via"] = "tcp"
        return summary
