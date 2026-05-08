"""Tests for utils._map_collector_meshcore_public."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

msgpack = pytest.importorskip("msgpack")

from src.utils._map_collector_meshcore_public import (
    MeshCorePublicCollectorMixin,
)


class _Host(MeshCorePublicCollectorMixin):
    """Minimal host class for the mixin — provides what the mixin expects."""

    def __init__(self, cache_path: Path, settings: dict | None = None):
        self._cache_path = cache_path
        self._settings = settings or {}

    def _meshcore_public_cache_path(self) -> Path:
        return self._cache_path

    @staticmethod
    def _is_valid_coordinate(lat, lon) -> bool:
        try:
            return (
                lat is not None and lon is not None
                and -90 <= float(lat) <= 90
                and -180 <= float(lon) <= 180
                and not (float(lat) == 0 and float(lon) == 0)
            )
        except (TypeError, ValueError):
            return False

    def _make_feature(self, node_id, name, lat, lon, network="meshtastic",
                      is_online=False, last_heard=None, last_seen="",
                      **kwargs) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": node_id,
                "name": name,
                "network": network,
                "is_online": is_online,
                "last_heard": last_heard,
                "last_seen": last_seen,
            },
        }


SAMPLE_RECORDS = [
    {
        "pk": bytes.fromhex("aa" * 32),
        "n": "Lat",
        "t": 1,
        "la": msgpack.Timestamp(1777319154, 0),
        "lat": 21.3069,
        "lon": -157.8583,
        "p": {"freq": 910.525, "bw": 62.5, "sf": 7, "cr": 5},
    },
    {
        "pk": bytes.fromhex("bb" * 32),
        "n": "Sat",
        "t": 1,
        "la": msgpack.Timestamp(1777319000, 0),
        "lat": 19.4351,
        "lon": -155.2138,
        "p": {"freq": 910.525, "bw": 62.5, "sf": 7, "cr": 5},
    },
    # Invalid coords — should skip
    {"pk": bytes.fromhex("cc" * 32), "n": "Junk", "lat": 999, "lon": 999},
    # No coords
    {"pk": bytes.fromhex("dd" * 32), "n": "Pos-less"},
]


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    return _Host(cache_path=tmp_path / "cache.msgpack")


class TestMeshCorePublicCollector:
    def test_disabled_returns_empty(self, host):
        host._settings = {"meshcore_public_enabled": False}
        assert host._collect_meshcore_public() == []

    def test_no_msgpack_returns_empty(self, host, monkeypatch):
        monkeypatch.setattr(
            "src.utils._map_collector_meshcore_public._HAS_MSGPACK", False,
        )
        assert host._collect_meshcore_public() == []

    def test_fetch_decodes_and_filters(self, host):
        raw = msgpack.packb(SAMPLE_RECORDS)
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = raw
            features = host._collect_meshcore_public()
        # 2 valid, 2 skipped (one bad coords, one no coords)
        assert len(features) == 2
        ids = {f["properties"]["id"] for f in features}
        assert ids == {f"meshcore:{'aa'*32}", f"meshcore:{'bb'*32}"}
        first = features[0]
        assert first["properties"]["network"] == "meshcore"
        assert first["properties"]["source"] == "meshcore_public"
        assert first["properties"]["pubkey"] == "aa" * 32
        assert "radio_params" in first["properties"]
        assert first["properties"]["radio_params"]["freq"] == 910.525

    def test_cache_hit_skips_fetch(self, host, tmp_path):
        # Pre-populate cache file
        raw = msgpack.packb(SAMPLE_RECORDS)
        host._cache_path.parent.mkdir(parents=True, exist_ok=True)
        host._cache_path.write_bytes(raw)
        # Set TTL well in the future
        host._settings = {"meshcore_public_cache_ttl_sec": 99999}
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen"
        ) as mock_open:
            features = host._collect_meshcore_public()
            mock_open.assert_not_called()
        assert len(features) == 2

    def test_stale_cache_falls_back_when_fetch_fails(self, host, tmp_path):
        raw = msgpack.packb(SAMPLE_RECORDS)
        host._cache_path.parent.mkdir(parents=True, exist_ok=True)
        host._cache_path.write_bytes(raw)
        # Make cache stale (mtime in the past)
        old_mtime = time.time() - 10000
        import os as _os
        _os.utime(host._cache_path, (old_mtime, old_mtime))
        host._settings = {"meshcore_public_cache_ttl_sec": 60}
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen",
            side_effect=OSError("network down"),
        ):
            features = host._collect_meshcore_public()
        # Used stale cache rather than returning empty
        assert len(features) == 2

    def test_decode_error_returns_empty(self, host):
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = b"not msgpack \xff\xff"
            features = host._collect_meshcore_public()
        assert features == []

    def test_max_nodes_cap_enforced(self, host):
        big_records = SAMPLE_RECORDS * 100  # 400 records, 200 valid
        raw = msgpack.packb(big_records)
        host._settings = {"meshcore_public_max_nodes": 50}
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = raw
            features = host._collect_meshcore_public()
        # Capped at 50 records iterated; some may be invalid → <= 50
        assert len(features) <= 50

    def test_fetch_failure_no_cache_returns_empty(self, host):
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen",
            side_effect=OSError("network down"),
        ):
            features = host._collect_meshcore_public()
        assert features == []

    def test_pk_as_string(self, host):
        records = [
            {"pk": "AABBCC", "n": "StrPk", "lat": 1.0, "lon": 2.0},
        ]
        raw = msgpack.packb(records)
        with patch(
            "src.utils._map_collector_meshcore_public.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = raw
            features = host._collect_meshcore_public()
        assert len(features) == 1
        assert features[0]["properties"]["pubkey"] == "aabbcc"
