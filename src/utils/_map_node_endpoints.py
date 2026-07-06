"""Node data endpoint mixin for :class:`MapRequestHandler`.

Holds the node-data surfaces of the map HTTP API:

- ``/api/nodes/geojson``    — live node GeoJSON (ResponseByteCache hot
                              path; age/region filters compose)
- ``/api/nodes/directory``  — persistent node directory (Issue #49;
                              single-flight response cache hot path)
- ``/api/nodes/history``    — node history stats + unique nodes (24h)
- ``/api/nodes/trajectory/<id>`` — per-node trajectory GeoJSON
- ``/api/nodes/snapshot``   — historical network snapshot for playback
- ``/api/coverage/...``     — terrain-aware coverage prediction
- ``/api/los/...``          — line-of-sight analysis

Also carries the server-side region/age filter machinery
(``REGION_BBOXES`` + ``_filter_by_age`` / ``_filter_by_region`` /
``_resolve_max_age_days`` / ``_resolve_region``) used by the geojson
endpoint — MA's analog of MeshForge's VIEW_PRESETS block. They stay
class-level (``MapRequestHandler.REGION_BBOXES`` etc.) so existing
test access via the class keeps working.

Extracted from ``map_http_handler.py`` to keep that file under the
1,500-line size cap (``CLAUDE.md``). No behaviour change — methods are
mixed into ``MapRequestHandler`` via inheritance and rely on the hub's
``self._serve_json`` / ``self._serve_cached``.

Mirrors MeshForge's ``_map_node_endpoints.py`` boundary (the repos'
endpoint sets differ: MA has no region-presets/settings/view-preset
endpoints, so those have no counterpart here).
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_SRTMProvider, _LOSAnalyzer, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'SRTMProvider', 'LOSAnalyzer'
)


class NodeDataEndpointsMixin:
    """Node-data endpoints + the serve-time age/region filter machinery."""

    # Region bboxes: (lat_min, lat_max, lon_min, lon_max).
    # ``world`` (no filter) is handled by absence from this map. Pacific +
    # other dateline-crossing regions are intentionally omitted for now
    # because bbox math without longitude wrap-around mis-classifies them;
    # operator can fall back to ``world`` and zoom in.
    REGION_BBOXES = {
        # Continental US + Alaska + Hawaii + Puerto Rico/USVI
        "us": (15.0, 72.0, -170.0, -65.0),
        # North America (US + Canada + Mexico + Central America)
        "na": (7.0, 84.0, -170.0, -50.0),
        # Hawaiian Islands only
        "hi": (18.0, 23.0, -161.0, -154.0),
        # Europe (mainland + UK + Iceland + Scandinavia)
        "eu": (34.0, 72.0, -25.0, 45.0),
        # Asia (mainland; excludes east of dateline)
        "as": (-10.0, 60.0, 60.0, 145.0),
        # Australia + NZ
        "oc": (-50.0, -10.0, 110.0, 180.0),
    }

    def _serve_geojson(self):
        """Serve live node GeoJSON, with optional age and region filters.

        Query params:
          ``max_age_days=N``  Drop features whose ``last_heard`` is older
                              than N days. ``0`` disables the filter.
                              Default falls back to map_settings.max_age_days
                              (ship default 30).
          ``region=KEY``      Drop features outside the region bbox.
                              KEY in {us, na, hi, eu, as, oc, world}.
                              ``world`` disables the filter. Default falls
                              back to map_settings.region (ship default
                              ``us``).

        Both filters are critical for the public meshcore.dev fetcher,
        which can return 40k+ nodes — many old, many on other continents.
        Filtering at serve time keeps the collector cache warm; the operator
        can A/B with ``?max_age_days=0&region=world`` for the unfiltered
        set.
        """
        if not self.collector:
            self._serve_json({"type": "FeatureCollection", "features": []})
            return

        # Resolve filters first (cheap query/settings parse) — they form the
        # cache key, since they materially change the response.
        max_age_days = self._resolve_max_age_days()
        region = self._resolve_region()
        cache_key = (max_age_days, region)

        def _build_geojson():
            # Always shallow-copy before mutating — the collector cache is
            # shared across concurrent requests via ThreadingHTTPServer.
            geojson = self.collector.collect()
            features = geojson.get("features") or []

            annotations: Dict[str, Any] = {}
            if max_age_days is not None and max_age_days > 0:
                features = self._filter_by_age(features, max_age_days)
                annotations["max_age_days"] = max_age_days
            if region and region != "world":
                bbox = self.REGION_BBOXES.get(region)
                if bbox is not None:
                    features = self._filter_by_region(features, bbox)
                    annotations["region"] = region
                    annotations["region_bbox"] = list(bbox)

            if annotations:
                geojson = dict(geojson)
                geojson["features"] = features
                props = dict(geojson.get("properties") or {})
                props.update(annotations)
                props["features_after_filter"] = len(features)
                geojson["properties"] = props
            return geojson

        self._serve_cached(
            self.collector._geojson_response_cache, cache_key, _build_geojson,
        )

    def _resolve_max_age_days(self) -> Optional[int]:
        """Read max_age_days from the query string or settings, or None."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("max_age_days") or [None])[0]
        if raw is not None:
            try:
                value = int(raw)
            except ValueError:
                return None
            return max(value, 0)
        # Fall back to operator default. SettingsManager lookups raise
        # nothing — missing keys return the default. 30 days is the
        # ship default; ``0`` means "never filter".
        try:
            settings = self.collector._settings
        except AttributeError:
            return 30
        if settings is None:
            return 30
        try:
            return int(settings.get("max_age_days", 30))
        except (TypeError, ValueError):
            return 30

    @staticmethod
    def _filter_by_age(features: list, max_age_days: int) -> list:
        """Keep features whose ``last_heard`` is within ``max_age_days``.

        Features without a numeric ``last_heard`` are kept (we can't prove
        they're stale). is_local features are always kept. The cutoff is
        wall-clock now − max_age_days.
        """
        cutoff = time.time() - (max_age_days * 86400)
        kept = []
        for f in features:
            props = f.get("properties") or {}
            if props.get("is_local"):
                kept.append(f)
                continue
            last_heard = props.get("last_heard")
            if not isinstance(last_heard, (int, float)):
                kept.append(f)
                continue
            if last_heard >= cutoff:
                kept.append(f)
        return kept

    def _resolve_region(self) -> Optional[str]:
        """Read region from the query string or settings, or None.

        Returns a known region key (``us``/``na``/``hi``/``eu``/``as``/
        ``oc``/``world``) or None when value is unrecognized. Caller
        treats None as "no filter".
        """
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        raw = (qs.get("region") or [None])[0]
        if raw is not None:
            value = raw.strip().lower()
            if value == "" or value == "world":
                return "world"
            if value in self.REGION_BBOXES:
                return value
            return None
        try:
            settings = self.collector._settings
        except AttributeError:
            return "us"
        if settings is None:
            return "us"
        try:
            saved = (settings.get("region", "us") or "us").strip().lower()
        except (TypeError, AttributeError):
            return "us"
        if saved == "world" or saved in self.REGION_BBOXES:
            return saved
        return "us"

    @staticmethod
    def _filter_by_region(features: list, bbox: tuple) -> list:
        """Keep features whose [lon, lat] falls inside ``bbox``.

        ``bbox`` is (lat_min, lat_max, lon_min, lon_max). is_local features
        are always kept (the NOC's own radio is in-region by definition,
        even if the bbox is wrong). Features without valid geometry are
        kept (the side-panel pipeline expects them).
        """
        lat_min, lat_max, lon_min, lon_max = bbox
        kept = []
        for f in features:
            props = f.get("properties") or {}
            if props.get("is_local"):
                kept.append(f)
                continue
            geom = f.get("geometry") or {}
            coords = geom.get("coordinates")
            if (
                not isinstance(coords, (list, tuple))
                or len(coords) < 2
            ):
                kept.append(f)
                continue
            try:
                lon, lat = float(coords[0]), float(coords[1])
            except (TypeError, ValueError):
                kept.append(f)
                continue
            if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                kept.append(f)
        return kept

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

    def _serve_directory(self):
        """Serve the persistent node directory as a GeoJSON FeatureCollection.

        Returns every node ever heard (within tier retention) — superset
        of `/api/nodes/geojson`, which only covers what the latest
        collect cycle saw. Position-less nodes (MeshCore adverts without
        GPS, RNS announces) surface in the sibling `nodes_without_position`
        array, mirroring the convention from Issue #43.
        """
        if not self.collector or not self.collector._history:
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": "history not available"},
                "nodes_without_position": [],
            })
            return

        def _build_directory():
            features, position_less = (
                self.collector._history.get_directory_snapshot(
                    include_position_less=True
                )
            )
            # Per-network breakdown alongside the full list — same shape
            # /api/status uses, so dashboards can consume either.
            by_network: Dict[str, int] = {}
            for entry in position_less:
                net = entry.get("network", "unknown")
                by_network[net] = by_network.get(net, 0) + 1
            return {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "generated_at": datetime.now().isoformat(),
                    "total_features": len(features),
                    "total_position_less": len(position_less),
                },
                "nodes_without_position": position_less,
                "nodes_without_position_by_network": by_network,
            }

        # No query params → single cache key. The snapshot exception path
        # serves a 500 uncached (errors must not be cached).
        try:
            self._serve_cached(
                self.collector._directory_response_cache, None, _build_directory,
            )
        except Exception as e:
            logger.error(f"directory snapshot failed: {e}")
            self._serve_json({
                "type": "FeatureCollection",
                "features": [],
                "properties": {"error": str(e)[:200]},
                "nodes_without_position": [],
            }, status=500)

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

    def _serve_coverage(self, parts: List[str]):
        """Serve terrain-aware coverage prediction for a location.

        URL: /api/coverage/<lat>/<lon>/<antenna_height_m>
        Optional query params: radius_km (default 10), freq_mhz (default 906)
        """
        try:
            if len(parts) < 3:
                self._serve_json({"error": "Usage: /api/coverage/<lat>/<lon>/<height_m>"})
                return

            lat = float(parts[0])
            lon = float(parts[1])
            alt = float(parts[2])

            # Parse query params
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            radius_km = float(params.get('radius_km', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])
            resolution = int(params.get('resolution', ['24'])[0])

            # Limit resolution for performance
            resolution = min(resolution, 48)
            radius_km = min(radius_km, 50)

            # Get coverage prediction from terrain analyzer
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                coverage = analyzer.coverage_grid(
                    lat, lon, alt,
                    radius_km=radius_km,
                    freq_mhz=freq_mhz,
                    resolution=resolution
                )
            except Exception as e:
                logger.error(f"Coverage calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Convert to GeoJSON for map display
            features = []
            for point in coverage:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [point["lon"], point["lat"]]
                    },
                    "properties": {
                        "is_clear": point["is_clear"],
                        "total_loss_db": point["total_loss_db"],
                        "terrain_loss_db": point["terrain_loss_db"],
                        "fresnel_pct": point["fresnel_clearance_pct"],
                        "distance_m": point["distance_m"],
                        "bearing": point["bearing"],
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "center": [lon, lat],
                    "antenna_height_m": alt,
                    "radius_km": radius_km,
                    "freq_mhz": freq_mhz,
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Coverage endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_snapshot(self):
        """Serve a historical network snapshot for playback.

        URL: /api/nodes/snapshot?timestamp=<unix_ts>&window=300
        """
        from urllib.parse import parse_qs, urlparse

        try:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                timestamp = float(params.get('timestamp', [str(time.time())])[0])
            except (ValueError, TypeError):
                timestamp = time.time()
            # Clamp the window: an unbounded ?window= forces a large DB scan +
            # GIL-heavy serialization on the request thread, letting one crafted
            # request stall other request threads. 1h is ample for playback.
            # (MF maps-QA audit port, 2026-07-06.)
            try:
                window = int(params.get('window', ['300'])[0])
            except (ValueError, TypeError):
                window = 300
            window = max(1, min(window, 3600))

            if not self.collector or not self.collector._history:
                self._serve_json({"error": "history not available", "features": []})
                return

            history = self.collector._history
            observations = history.get_snapshot(timestamp=timestamp, window_seconds=window)

            # Convert observations to GeoJSON features
            features = []
            for obs in observations:
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [obs.longitude, obs.latitude]
                    },
                    "properties": {
                        "id": obs.node_id,
                        "name": obs.name,
                        "network": obs.network,
                        "is_online": obs.is_online,
                        "snr": obs.snr,
                        "battery": obs.battery,
                        "hardware": obs.hardware,
                        "role": obs.role,
                        "via_mqtt": obs.via_mqtt,
                        "timestamp": obs.timestamp,
                    }
                })

            result = {
                "type": "FeatureCollection",
                "features": features,
                "properties": {
                    "snapshot_time": timestamp,
                    "window_seconds": window,
                    "node_count": len(features),
                }
            }
            self._serve_json(result)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"Snapshot endpoint error: {e}")
            self._serve_json({"error": str(e)})

    def _serve_los(self, parts: List[str]):
        """Serve line-of-sight analysis between two points.

        URL: /api/los/<lat1>/<lon1>/<lat2>/<lon2>
        Optional query params: alt1, alt2 (antenna heights, default 10m), freq_mhz (default 906)
        """
        try:
            if len(parts) < 4:
                self._serve_json({"error": "Usage: /api/los/<lat1>/<lon1>/<lat2>/<lon2>"})
                return

            lat1 = float(parts[0])
            lon1 = float(parts[1])
            lat2 = float(parts[2])
            lon2 = float(parts[3])

            # Parse query params
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            alt1 = float(params.get('alt1', ['10'])[0])
            alt2 = float(params.get('alt2', ['10'])[0])
            freq_mhz = float(params.get('freq_mhz', ['906'])[0])

            # Calculate LOS
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"})
                return
            try:
                provider = _SRTMProvider()
                analyzer = _LOSAnalyzer(provider)
                result = analyzer.analyze(lat1, lon1, alt1, lat2, lon2, alt2, freq_mhz)
            except Exception as e:
                logger.error(f"LOS calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"})
                return

            # Build elevation profile for visualization
            profile = []
            if hasattr(result, 'profile') and result.profile:
                for p in result.profile:
                    profile.append({
                        "distance_m": p.distance_m,
                        "elevation_m": p.ground_elevation,
                        "los_height_m": p.los_height,
                        "fresnel_top": p.los_height + p.fresnel_radius,
                        "fresnel_bottom": p.los_height - p.fresnel_radius,
                    })

            response = {
                "is_clear": result.is_clear,
                "distance_m": result.distance_m,
                "total_loss_db": result.total_loss_db,
                "terrain_loss_db": result.terrain_loss_db,
                "fresnel_clearance_pct": result.fresnel_clearance_pct,
                "obstruction_count": len(result.obstructions) if hasattr(result, 'obstructions') else 0,
                "profile": profile,
                "endpoints": {
                    "from": {"lat": lat1, "lon": lon1, "alt": alt1},
                    "to": {"lat": lat2, "lon": lon2, "alt": alt2},
                }
            }
            self._serve_json(response)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"})
        except Exception as e:
            logger.error(f"LOS endpoint error: {e}")
            self._serve_json({"error": str(e)})
