"""Ported/MA-specific collector findings from the 2026-07-06 QA review.

Covers the collector-layer fixes: numeric-id canonicalization, epoch coercion +
future clamp, record_observations coord isolation, meshcore-public bounded read
+ safe-int settings.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from utils.map_data_collector import MapDataCollector  # noqa: E402
from utils._map_collector_meshtastic import _canonical_meshtastic_id  # noqa: E402
from utils._map_collector_meshcore_public import _safe_int, MAX_MESHCORE_PUBLIC_BYTES  # noqa: E402
from utils.node_history import NodeHistoryDB  # noqa: E402


class TestCanonicalMeshtasticId:
    def test_decimal_string_never_passes_through(self):
        assert _canonical_meshtastic_id("!499602d2", 1234567890) == "!499602d2"
        assert _canonical_meshtastic_id(None, 1234567890) == "!499602d2"
        assert _canonical_meshtastic_id("1234567890", 1234567890) == "!499602d2"
        assert _canonical_meshtastic_id(None, 0) == "unknown"
        assert _canonical_meshtastic_id("", None) == "unknown"


class TestCoerceEpochAndFutureClamp:
    def test_coerce_epoch(self):
        assert MapDataCollector._coerce_epoch(1700000000) == 1700000000.0
        assert MapDataCollector._coerce_epoch("1700000000") == 1700000000.0
        assert MapDataCollector._coerce_epoch("garbage") == 0.0
        assert MapDataCollector._coerce_epoch(None) == 0.0
        assert MapDataCollector._coerce_epoch(True) == 0.0

    def test_iso_string_coerced(self):
        from datetime import datetime, timezone
        iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        assert MapDataCollector._coerce_epoch(iso) > 0

    def test_is_node_online_future_and_string(self, tmp_path):
        import time
        c = MapDataCollector.__new__(MapDataCollector)
        c._settings = None
        # future stamp → not online
        assert c._is_node_online(time.time() + 10_000, source="meshcore") is False
        # ISO string (recent) → does not raise, online
        from datetime import datetime, timezone
        iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        assert c._is_node_online(iso, source="meshtastic") is True
        # garbage string → offline, no raise
        assert c._is_node_online("nope", source="rns") is False
        assert c._is_node_online(0, source="meshtastic") is False


class TestRecordObservationsCoordIsolation:
    def _feat(self, nid, lon, lat):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": nid, "name": nid, "is_online": True,
                           "network": "meshtastic"},
        }

    def test_bad_coord_skips_only_that_feature(self, tmp_path):
        hist = NodeHistoryDB(db_path=tmp_path / "h.db", retention_seconds=86400)
        n = hist.record_observations([
            self._feat("!good1", 0.1, 0.2),
            self._feat("!bad", "x", "19.4"),   # would crash round() before fix
            self._feat("!good2", 0.3, 0.4),
        ])
        assert n == 2  # both good rows; bad one skipped, batch not aborted

    def test_numeric_string_coords_coerced(self, tmp_path):
        hist = NodeHistoryDB(db_path=tmp_path / "h2.db", retention_seconds=86400)
        assert hist.record_observations([self._feat("!s", "0.11", "0.22")]) == 1


class TestMeshcorePublicHardening:
    def test_safe_int(self):
        assert _safe_int("1h", 3600) == 3600
        assert _safe_int("50", 3600) == 50
        assert _safe_int(None, 42) == 42
        assert _safe_int(7, 42) == 7

    def test_byte_cap_is_sane(self):
        # generous over the ~12MB real body, but bounded (not unlimited)
        assert 12 * 1024 * 1024 < MAX_MESHCORE_PUBLIC_BYTES <= 128 * 1024 * 1024
