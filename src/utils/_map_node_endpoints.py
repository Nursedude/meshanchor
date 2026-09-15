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

import json
import logging
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

from utils.safe_import import safe_import

_SRTMProvider, _LOSAnalyzer, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'SRTMProvider', 'LOSAnalyzer'
)


# ── Terrain endpoint guards (frontier security pass 2026-09-14) ──────
#
# /api/coverage and /api/los are the map's two compute+download amplifiers:
# one maximal coverage request is 172,800 elevation lookups (1.7 s on a
# Pi 5, ~5-6 s on a Pi 4, GIL-bound) and, before this pass, any coordinate
# an unauthenticated client picked could trigger a synchronous 25 MB S3
# download inside the handler. Three bounds, applied in this order:
#
#   1. _reject_if_untrusted()  — loopback / configured LAN only. "LAN" is
#      whatever --cors-origins names: the fleet unit derives it from EVERY
#      IPv4 address the box holds (one /24 each), so on that unit the gate
#      keeps out a router port-forward and any client NOT on an attached
#      /24 — it does NOT keep out a future second interface's own /24,
#      because the unit would trust it on the next restart (a unit-file
#      decision, reviewed 2026-09-14). With no --cors-origins at all the
#      gate is loopback-only and LAN browsers get 403 here — the secure
#      default; pinned by TestTerrainEndpointsAreGated.
#   2. finite-range validation — lat/lon/alt/freq/radius/resolution must
#      be finite and inside physical bounds. `float("nan")` used to parse
#      and publish a confident `is_clear: true` beside a bare NaN token
#      that no browser can JSON.parse.
#   3. _TERRAIN_SLOTS — a per-BOX bound (not per-client: client IP is a
#      weak key behind NAT/AREDN). Beyond N concurrent terrain
#      computations the answer is 503 + Retry-After, so a burst costs the
#      Pi one GIL's worth, never the whole map.
#
# The request path's provider is SHARED and never downloads
# (`auto_download=False`): tiles arrive through SRTMProvider.warm_tiles()
# at map warm-up, bounded and off the request thread (map_data_service).
def _slots_from_env(default: int = 2) -> int:
    """MESHFORGE_TERRAIN_SLOTS as a positive int; a bad value keeps the default
    (and says so) rather than failing the whole map at import."""
    raw = os.environ.get("MESHFORGE_TERRAIN_SLOTS", "")
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("MESHFORGE_TERRAIN_SLOTS=%r is not an int; using %d", raw, default)
        return default


_TERRAIN_SLOTS = threading.BoundedSemaphore(_slots_from_env())
_TERRAIN_RETRY_AFTER_S = 5
_TERRAIN_MISSING_NOTE = (
    "part of this path has no terrain tile cached on this box; the request "
    "path never downloads (auto_download is off). The map warms tiles around "
    "its own local nodes once at start-up; to add tiles now run "
    "scripts/srtm_warm.py on this box (or restart the map)"
)

_provider_lock = threading.Lock()
_provider_singleton = None


def _terrain_provider():
    """The map process's ONE terrain provider (request path, no downloads).

    Shared so the decoded-tile LRU and the missing-tile memory are
    per-process, not per-request: a per-request provider re-read 25 MB per
    tile per request and forgot every negative answer. Tests patch THIS
    name to inject a synthetic provider.
    """
    global _provider_singleton
    with _provider_lock:
        if _provider_singleton is None:
            _provider_singleton = _SRTMProvider(auto_download=False)
        return _provider_singleton


def _finite(name: str, raw, lo: float, hi: float) -> float:
    """Parse ``raw`` as a finite float inside ``[lo, hi]`` or raise ValueError.

    `float()` accepts "nan", "inf" and "1e309"; none of those is a
    coordinate, an antenna height or a frequency, and every one of them
    reached the analyzer before 2026-09-14.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {raw!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {raw!r}")
    if value < lo or value > hi:
        raise ValueError(f"{name} must be between {lo:g} and {hi:g}, got {value:g}")
    return value


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
        if self._reject_if_untrusted():
            return
        try:
            if len(parts) < 3:
                self._serve_json({"error": "Usage: /api/coverage/<lat>/<lon>/<height_m>"},
                                 status=400)
                return

            lat = _finite("lat", parts[0], -90.0, 90.0)
            lon = _finite("lon", parts[1], -180.0, 180.0)
            alt = _finite("height_m", parts[2], 0.0, 10000.0)

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            radius_km = _finite("radius_km", params.get('radius_km', ['10'])[0], 0.1, 50.0)
            freq_mhz = _finite("freq_mhz", params.get('freq_mhz', ['906'])[0], 1.0, 100000.0)
            resolution = int(_finite("resolution", params.get('resolution', ['24'])[0], 1, 48))

            # Get coverage prediction from terrain analyzer
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"}, status=503)
                return
            if not _TERRAIN_SLOTS.acquire(blocking=False):
                self._serve_terrain_busy()
                return
            try:
                provider = _terrain_provider()
                analyzer = _LOSAnalyzer(provider)
                coverage = analyzer.coverage_grid(
                    lat, lon, alt,
                    radius_km=radius_km,
                    freq_mhz=freq_mhz,
                    resolution=resolution
                )
            except Exception as e:
                logger.error(f"Coverage calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"}, status=500)
                return
            finally:
                _TERRAIN_SLOTS.release()

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
            self._serve_json({"error": f"Invalid parameters: {e}"}, status=400)
        except Exception as e:
            logger.error(f"Coverage endpoint error: {e}")
            self._serve_json({"error": str(e)}, status=500)

    def _serve_terrain_busy(self):
        """503 + Retry-After: every terrain slot on this box is in use."""
        body = json.dumps({
            "error": "terrain busy",
            "detail": (f"this box is already running its maximum concurrent "
                       f"terrain computations; retry in {_TERRAIN_RETRY_AFTER_S}s"),
            "retry_after_s": _TERRAIN_RETRY_AFTER_S,
        }).encode()
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Retry-After', str(_TERRAIN_RETRY_AFTER_S))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

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
        if self._reject_if_untrusted():
            return
        try:
            if len(parts) < 4:
                self._serve_json({"error": "Usage: /api/los/<lat1>/<lon1>/<lat2>/<lon2>"},
                                 status=400)
                return

            lat1 = _finite("lat1", parts[0], -90.0, 90.0)
            lon1 = _finite("lon1", parts[1], -180.0, 180.0)
            lat2 = _finite("lat2", parts[2], -90.0, 90.0)
            lon2 = _finite("lon2", parts[3], -180.0, 180.0)

            # Parse query params

            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            alt1 = _finite("alt1", params.get('alt1', ['10'])[0], 0.0, 10000.0)
            alt2 = _finite("alt2", params.get('alt2', ['10'])[0], 0.0, 10000.0)
            freq_mhz = _finite("freq_mhz", params.get('freq_mhz', ['906'])[0], 1.0, 100000.0)

            # Calculate LOS
            if not _HAS_TERRAIN:
                self._serve_json({"error": "terrain module not available"}, status=503)
                return
            if not _TERRAIN_SLOTS.acquire(blocking=False):
                self._serve_terrain_busy()
                return
            try:
                provider = _terrain_provider()
                analyzer = _LOSAnalyzer(provider)
                result = analyzer.analyze(lat1, lon1, alt1, lat2, lon2, alt2, freq_mhz)
            except Exception as e:
                logger.error(f"LOS calculation failed: {e}")
                self._serve_json({"error": f"calculation failed: {str(e)}"}, status=500)
                return
            finally:
                _TERRAIN_SLOTS.release()

            # Build elevation profile for visualization.
            #
            # These lists are the LOSResult contract (elevation_profile /
            # los_heights / fresnel_radii), read by name. This block used to
            # guard on `hasattr(result, 'profile')` and `result.obstructions`
            # — attributes LOSResult has NEVER had — so `profile` was always
            # [] and `obstruction_count` always 0, on every call, with real
            # terrain loaded. A hasattr() guard against a sibling module's
            # shape cannot fail loudly; it just quietly publishes nothing.
            # TestLOSResponseMatchesAnalyzer pins the names instead.
            elevations = result.elevation_profile
            los_heights = result.los_heights
            fresnel_radii = result.fresnel_radii
            n = len(elevations)
            profile = []
            if n and len(los_heights) == n and len(fresnel_radii) == n:
                for i in range(n):
                    t = i / max(1, n - 1)
                    los_h = los_heights[i]
                    radius = fresnel_radii[i]
                    profile.append({
                        "distance_m": result.distance_m * t,
                        "elevation_m": elevations[i],
                        "los_height_m": los_h,
                        "fresnel_top": los_h + radius,
                        "fresnel_bottom": los_h - radius,
                    })
            elif n:
                logger.warning(
                    "LOS profile lists disagree (elev=%d los=%d fresnel=%d) "
                    "— omitting profile rather than publishing a ragged one",
                    n, len(los_heights), len(fresnel_radii),
                )

            response = {
                "is_clear": result.is_clear,
                "distance_m": result.distance_m,
                "total_loss_db": result.total_loss_db,
                "terrain_loss_db": result.terrain_loss_db,
                # Free-space component on its own, so a client can show it
                # without subtracting two published numbers (or worse,
                # re-implementing the FSPL formula as a fourth copy).
                "fspl_db": result.fspl_db,
                "fresnel_clearance_pct": result.fresnel_clearance_pct,
                "obstruction_count": result.num_obstructions,
                "profile": profile,
                # Coverage rides WITH the verdict, never separately: every
                # field above is an opinion about invented ground wherever a
                # sample was missing. A consumer that ignores this renders a
                # confident "Clear LOS" over terrain nobody measured.
                "terrain_complete": result.terrain_complete,
                "terrain_samples_total": result.terrain_samples_total,
                "terrain_samples_missing": result.terrain_samples_missing,
                "endpoints": {
                    "from": {"lat": lat1, "lon": lon1, "alt": alt1},
                    "to": {"lat": lat2, "lon": lon2, "alt": alt2},
                }
            }
            if not result.terrain_complete:
                # Say WHICH lever to pull, not just that the answer is
                # incomplete: the request path never downloads, so an
                # honest "unknown" here stays unknown until someone warms
                # the tiles. Name them when the provider can.
                response["terrain_note"] = _TERRAIN_MISSING_NOTE
                missing_for = getattr(provider, "missing_tiles_for", None)
                if callable(missing_for):
                    n = max(2, len(elevations))
                    pts = [(lat1 + (lat2 - lat1) * i / (n - 1),
                            lon1 + (lon2 - lon1) * i / (n - 1)) for i in range(n)]
                    response["terrain_tiles_missing"] = missing_for(pts)
            self._serve_json(response)

        except ValueError as e:
            self._serve_json({"error": f"Invalid parameters: {e}"}, status=400)
        except Exception as e:
            logger.error(f"LOS endpoint error: {e}")
            self._serve_json({"error": str(e)}, status=500)
