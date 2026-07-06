"""Fetch operator-submitted MeshCore nodes from map.meshcore.dev.

MeshCore has no MQTT firehose for live nodes. The official directory at
https://map.meshcore.io is fed by operator advertisements that get
submitted to the backend at https://map.meshcore.dev/api/v1/nodes
(MessagePack, ~12MB / ~42k nodes). Each record includes lat/lon, so
once decoded we can drop them straight into MeshAnchor's GeoJSON
pipeline tagged ``source: meshcore_public``.

Fetched payload is cached on disk; default TTL is 1h to keep the page
response fast and to avoid hammering the public endpoint.

Mirrors the existing ``_map_collector_meshtastic.py`` /
``_map_collector_rns.py`` mixin pattern so MapDataCollector composes
cleanly.

Expects on the host class:
    - self._is_valid_coordinate(lat, lon)
    - self._make_feature(...)
    - get_real_user_home() (module-level import)

Settings (~/.config/meshanchor/map_settings.json):
    - meshcore_public_enabled (bool, default True)
    - meshcore_public_cache_ttl_sec (int, default 3600)
    - meshcore_public_max_nodes (int, default 50000 — soft cap)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

_msgpack, _HAS_MSGPACK = safe_import("msgpack")

DEFAULT_MESHCORE_PUBLIC_URL = (
    "https://map.meshcore.dev/api/v1/nodes?binary=1&short=1"
)
DEFAULT_CACHE_TTL_SEC = 3600  # 1 hour
DEFAULT_FETCH_TIMEOUT_SEC = 30
DEFAULT_MAX_NODES = 50_000  # soft safety cap
# Hard byte cap on the PUBLIC (hostile-by-assumption) meshcore.dev body. Real
# payload is ~12 MB (~42k nodes); 48 MB is 4x headroom. Without this, a
# compromised/misbehaving upstream (or MITM) can return a multi-GB body that
# resp.read() buffers whole → OOM on a Pi. The max_nodes cap only applies AFTER
# the full decode, so it protects nothing here. (QA audit 2026-07-06.)
MAX_MESHCORE_PUBLIC_BYTES = 48 * 1024 * 1024

_CACHE_FILENAME = "meshcore_public_cache.msgpack"


def _safe_int(value, default: int) -> int:
    """int() that falls back to a default instead of raising — an operator
    typo in map_settings.json (e.g. ``"1h"``) must not abort the collect
    cycle. (QA audit 2026-07-06.)"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MeshCorePublicCollectorMixin:
    """Adds ``_collect_meshcore_public`` to MapDataCollector."""

    def _meshcore_public_cache_path(self) -> Path:
        return (
            get_real_user_home()
            / ".cache"
            / "meshanchor"
            / _CACHE_FILENAME
        )

    def _meshcore_public_settings(self) -> Dict:
        """Read settings with fallback defaults. Survives a missing host attr."""
        settings = getattr(self, "_settings", None)
        if settings is None:
            return {}
        return {
            "enabled": settings.get(
                "meshcore_public_enabled", True
            ),
            "ttl_sec": _safe_int(settings.get(
                "meshcore_public_cache_ttl_sec", DEFAULT_CACHE_TTL_SEC,
            ), DEFAULT_CACHE_TTL_SEC),
            "max_nodes": _safe_int(settings.get(
                "meshcore_public_max_nodes", DEFAULT_MAX_NODES,
            ), DEFAULT_MAX_NODES),
            "url": settings.get(
                "meshcore_public_url", DEFAULT_MESHCORE_PUBLIC_URL,
            ),
            "timeout_sec": _safe_int(settings.get(
                "meshcore_public_fetch_timeout_sec",
                DEFAULT_FETCH_TIMEOUT_SEC,
            ), DEFAULT_FETCH_TIMEOUT_SEC),
        }

    def _collect_meshcore_public(self) -> List[Dict]:
        """Fetch (or read from cache) the public meshcore.dev directory.

        Returns a list of GeoJSON features tagged ``source: meshcore_public``.
        Returns empty list when the feature is disabled, msgpack is missing,
        the network is unreachable, or the payload doesn't parse.
        """
        if not _HAS_MSGPACK:
            logger.debug(
                "meshcore_public: msgpack library not available, "
                "skipping public directory fetch"
            )
            return []

        cfg = self._meshcore_public_settings()
        if not cfg.get("enabled", True):
            logger.debug("meshcore_public: disabled via settings")
            return []

        cache_path = self._meshcore_public_cache_path()
        ttl = cfg.get("ttl_sec", DEFAULT_CACHE_TTL_SEC)

        raw: Optional[bytes] = None

        # Use cached payload if fresh.
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < ttl:
                try:
                    raw = cache_path.read_bytes()
                    logger.debug(
                        "meshcore_public: cache hit (%.0fs old, %d bytes)",
                        age, len(raw),
                    )
                except OSError as e:
                    logger.debug("meshcore_public: cache read error: %s", e)
                    raw = None

        # Fetch fresh if cache stale/missing.
        if raw is None:
            url = cfg.get("url", DEFAULT_MESHCORE_PUBLIC_URL)
            timeout = cfg.get("timeout_sec", DEFAULT_FETCH_TIMEOUT_SEC)
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "MeshAnchor/0.1 (+map.meshcore.dev fetcher)"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read(MAX_MESHCORE_PUBLIC_BYTES + 1)
                if len(raw) > MAX_MESHCORE_PUBLIC_BYTES:
                    logger.warning(
                        "meshcore_public: response exceeded %d bytes — rejecting "
                        "(hostile/misconfigured upstream); not caching",
                        MAX_MESHCORE_PUBLIC_BYTES,
                    )
                    return []
                logger.info(
                    "meshcore_public: fetched %d bytes from %s",
                    len(raw), url,
                )
                self._meshcore_public_save_cache(cache_path, raw)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                logger.warning("meshcore_public fetch failed: %s", e)
                # Fall back to stale cache if any cache is present.
                if cache_path.exists():
                    try:
                        raw = cache_path.read_bytes()
                        logger.info(
                            "meshcore_public: using stale cache (%.1fh old)",
                            (time.time() - cache_path.stat().st_mtime) / 3600,
                        )
                    except OSError:
                        return []
                else:
                    return []

        if not raw:
            return []

        try:
            data = _msgpack.unpackb(raw, raw=False, timestamp=3)
        except Exception as e:
            logger.warning("meshcore_public: msgpack decode error: %s", e)
            return []

        if not isinstance(data, list):
            logger.warning(
                "meshcore_public: expected list, got %s", type(data).__name__,
            )
            return []

        return self._meshcore_public_to_features(data, cfg.get("max_nodes", DEFAULT_MAX_NODES))

    def _meshcore_public_save_cache(self, path: Path, raw: bytes) -> None:
        """Atomic cache write: tmp file in same dir + os.replace."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".meshcore_public.", suffix=".tmp",
                dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.debug("meshcore_public cache write error: %s", e)

    def _meshcore_public_to_features(
        self, records: List[Dict], max_nodes: int,
    ) -> List[Dict]:
        """Convert msgpack-decoded records to GeoJSON features.

        Each record (per map.meshcore.dev short format) carries:
            pk  -> public_key (hex bytes or hex string)
            n   -> adv_name
            t   -> node type (1-4)
            la  -> last_advert (msgpack Timestamp)
            lat -> latitude
            lon -> longitude
            p   -> radio params dict (freq/bw/sf/cr) — preserved as 'radio_params'
        """
        features: List[Dict] = []
        skipped = 0
        for i, rec in enumerate(records):
            if i >= max_nodes:
                logger.warning(
                    "meshcore_public: hit max_nodes cap of %d, stopping",
                    max_nodes,
                )
                break
            if not isinstance(rec, dict):
                skipped += 1
                continue
            lat = rec.get("lat")
            lon = rec.get("lon")
            if not self._is_valid_coordinate(lat, lon):
                skipped += 1
                continue
            pk_raw = rec.get("pk")
            if isinstance(pk_raw, (bytes, bytearray)):
                pk_hex = pk_raw.hex()
            elif isinstance(pk_raw, str):
                pk_hex = pk_raw.lower()
            else:
                skipped += 1
                continue

            name = rec.get("n") or f"MC-{pk_hex[:8]}"
            node_id = f"meshcore:{pk_hex}"

            # Convert msgpack Timestamp (or datetime, or int) to epoch seconds.
            last_advert = rec.get("la")
            last_heard = self._meshcore_public_timestamp_to_epoch(last_advert)

            feature = self._make_feature(
                node_id=node_id,
                name=str(name),
                lat=float(lat),
                lon=float(lon),
                network="meshcore",
                is_online=False,  # public directory is historical, not live
                last_heard=last_heard,
                last_seen="public directory",
            )
            props = feature["properties"]
            props["source"] = "meshcore_public"
            props["pubkey"] = pk_hex
            if "t" in rec:
                props["meshcore_type"] = rec["t"]
            params = rec.get("p")
            if isinstance(params, dict):
                props["radio_params"] = {
                    k: params[k] for k in ("freq", "bw", "sf", "cr") if k in params
                }
            features.append(feature)

        logger.debug(
            "meshcore_public: %d features (%d skipped, %d total records)",
            len(features), skipped, len(records),
        )
        return features

    @staticmethod
    def _meshcore_public_timestamp_to_epoch(la) -> Optional[float]:
        """Return epoch seconds from a msgpack Timestamp / datetime / int / None."""
        if la is None:
            return None
        # msgpack-python returns Timestamp objects with .seconds + .nanoseconds
        seconds = getattr(la, "seconds", None)
        if seconds is not None:
            return float(seconds)
        # datetime fallback
        try:
            return float(la.timestamp())
        except AttributeError:
            pass
        if isinstance(la, (int, float)):
            return float(la)
        return None


__all__ = [
    "MeshCorePublicCollectorMixin",
    "DEFAULT_MESHCORE_PUBLIC_URL",
    "DEFAULT_CACHE_TTL_SEC",
]
