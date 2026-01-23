"""
Data pipeline integration tests — MQTT → NodeTracker → Map feed end-to-end.

Tests the complete data flow:
1. MQTT message ingestion → node/position/telemetry extraction
2. GeoJSON cache persistence → mqtt_nodes.json written to disk
3. MapDataCollector reads MQTT cache → merges with other sources
4. NodeHistoryDB records observations → queryable trajectory
5. Full round trip: inject messages → verify in history DB

All tests use temporary directories to avoid polluting real caches.
"""

import sys
import os
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from monitoring.mqtt_subscriber import MQTTNodelessSubscriber, MQTTNode
from utils.node_history import NodeHistoryDB, NodeObservation


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test caches."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def mqtt_subscriber():
    """Create MQTT subscriber with test config."""
    config = {
        "broker": "test.example.com",
        "port": 1883,
        "username": "",
        "password": "",
        "root_topic": "msh/US/2/e",
        "channel": "LongFast",
        "use_tls": False,
        "auto_reconnect": False,
    }
    return MQTTNodelessSubscriber(config=config)


@pytest.fixture
def history_db(temp_dir):
    """Create a temporary NodeHistoryDB."""
    db_path = temp_dir / "test_history.db"
    return NodeHistoryDB(db_path=db_path, retention_seconds=86400)


def make_mqtt_message(topic, payload_dict):
    """Create a mock MQTT message."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = json.dumps(payload_dict).encode('utf-8')
    return msg


# =============================================================================
# Stage 1: MQTT Message → Node Extraction
# =============================================================================

class TestMQTTIngestion:
    """Test that MQTT messages are correctly parsed into node data."""

    def test_nodeinfo_creates_node(self, mqtt_subscriber):
        """nodeinfo message creates node with metadata."""
        msg = make_mqtt_message(
            "msh/US/2/json/LongFast/!node01",
            {
                "from": "!node01",
                "type": "nodeinfo",
                "payload": {
                    "id": "!node01",
                    "longname": "Mauna Kea Relay",
                    "shortname": "MKR",
                    "hardware": "TBEAM",
                    "role": "ROUTER",
                }
            }
        )
        mqtt_subscriber._on_message(None, None, msg)
        node = mqtt_subscriber.get_node("!node01")
        assert node is not None
        assert node.long_name == "Mauna Kea Relay"
        assert node.hardware_model == "TBEAM"
        assert node.role == "ROUTER"

    def test_position_updates_coordinates(self, mqtt_subscriber):
        """position message updates node coordinates."""
        msg = make_mqtt_message(
            "msh/US/2/json/LongFast/!node02",
            {
                "from": "!node02",
                "type": "position",
                "payload": {
                    "latitude_i": 197749000,   # 19.7749°N (Mauna Kea)
                    "longitude_i": -1552845000,  # -155.2845°W
                    "altitude": 4205,
                }
            }
        )
        mqtt_subscriber._on_message(None, None, msg)
        node = mqtt_subscriber.get_node("!node02")
        assert abs(node.latitude - 19.7749) < 0.001
        assert abs(node.longitude - (-155.2845)) < 0.001
        assert node.altitude == 4205

    def test_telemetry_updates_metrics(self, mqtt_subscriber):
        """telemetry message updates battery/voltage."""
        msg = make_mqtt_message(
            "msh/US/2/json/LongFast/!node03",
            {
                "from": "!node03",
                "type": "telemetry",
                "payload": {
                    "device_metrics": {
                        "battery_level": 78,
                        "voltage": 3.92,
                        "channel_utilization": 8.5,
                        "air_util_tx": 2.1,
                    }
                }
            }
        )
        mqtt_subscriber._on_message(None, None, msg)
        node = mqtt_subscriber.get_node("!node03")
        assert node.battery_level == 78
        assert node.voltage == 3.92

    def test_snr_rssi_from_message(self, mqtt_subscriber):
        """SNR/RSSI extracted from message envelope."""
        msg = make_mqtt_message(
            "msh/US/2/json/LongFast/!node04",
            {
                "from": "!node04",
                "sender": "!node04",
                "type": "text",
                "snr": -3.5,
                "rssi": -98,
                "payload": {"text": "Hello mesh"},
                "id": "msg001",
                "to": "!all",
            }
        )
        mqtt_subscriber._on_message(None, None, msg)
        node = mqtt_subscriber.get_node("!node04")
        assert node.snr == -3.5
        assert node.rssi == -98

    def test_multiple_messages_same_node(self, mqtt_subscriber):
        """Multiple messages update same node progressively."""
        # First: position
        msg1 = make_mqtt_message("msh/US/2/json/LongFast/!node05", {
            "from": "!node05", "type": "position",
            "payload": {"latitude": 21.3, "longitude": -157.8}
        })
        # Then: nodeinfo
        msg2 = make_mqtt_message("msh/US/2/json/LongFast/!node05", {
            "from": "!node05", "type": "nodeinfo",
            "payload": {"id": "!node05", "longname": "Diamond Head"}
        })
        # Then: telemetry
        msg3 = make_mqtt_message("msh/US/2/json/LongFast/!node05", {
            "from": "!node05", "type": "telemetry",
            "payload": {"device_metrics": {"battery_level": 92}}
        })

        for msg in [msg1, msg2, msg3]:
            mqtt_subscriber._on_message(None, None, msg)

        node = mqtt_subscriber.get_node("!node05")
        assert node.latitude == 21.3
        assert node.long_name == "Diamond Head"
        assert node.battery_level == 92


# =============================================================================
# Stage 2: GeoJSON Cache Persistence
# =============================================================================

class TestGeoJSONCachePersistence:
    """Test that MQTT subscriber persists GeoJSON to disk."""

    def test_geojson_generation(self, mqtt_subscriber):
        """Subscriber generates valid GeoJSON from nodes."""
        # Add positioned nodes
        node = mqtt_subscriber._ensure_node("!geo01")
        node.latitude = 19.82
        node.longitude = -155.47
        node.long_name = "Kohala Summit"
        node.snr = -5.0

        geojson = mqtt_subscriber.get_geojson()
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 1

        feature = geojson["features"][0]
        assert feature["geometry"]["type"] == "Point"
        assert feature["geometry"]["coordinates"] == [-155.47, 19.82]
        assert feature["properties"]["id"] == "!geo01"
        assert feature["properties"]["name"] == "Kohala Summit"
        assert feature["properties"]["snr"] == -5.0

    def test_cache_written_to_disk(self, mqtt_subscriber, temp_dir):
        """persist_map_cache writes valid JSON file."""
        node = mqtt_subscriber._ensure_node("!cache01")
        node.latitude = 20.0
        node.longitude = -156.0

        # Patch cache path
        with patch('monitoring.mqtt_subscriber.get_real_user_home',
                   return_value=temp_dir):
            mqtt_subscriber._persist_map_cache()

        cache_file = temp_dir / ".local" / "share" / "meshforge" / "mqtt_nodes.json"
        assert cache_file.exists()

        data = json.loads(cache_file.read_text())
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1

    def test_empty_nodes_not_cached(self, mqtt_subscriber, temp_dir):
        """Empty feature set doesn't write cache file."""
        with patch('monitoring.mqtt_subscriber.get_real_user_home',
                   return_value=temp_dir):
            mqtt_subscriber._persist_map_cache()

        cache_file = temp_dir / ".local" / "share" / "meshforge" / "mqtt_nodes.json"
        assert not cache_file.exists()


# =============================================================================
# Stage 3: MapDataCollector Reads MQTT Cache
# =============================================================================

class TestMapDataCollectorMQTT:
    """Test that MapDataCollector reads MQTT cache file."""

    def test_reads_fresh_mqtt_cache(self, temp_dir):
        """Collector reads mqtt_nodes.json if < 5 minutes old."""
        from utils.map_data_service import MapDataCollector

        # Write a mock MQTT cache
        mqtt_cache = temp_dir / "mqtt_nodes.json"
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.5, 19.8]},
                "properties": {
                    "id": "!mqtt_node",
                    "name": "MQTT Test Node",
                    "network": "meshtastic",
                    "is_online": True,
                    "via_mqtt": True,
                    "snr": -4.0,
                }
            }]
        }
        mqtt_cache.write_text(json.dumps(geojson))

        collector = MapDataCollector(cache_dir=temp_dir, enable_history=False)
        result = collector.collect(max_age_seconds=0)

        # Node should appear in results (from mqtt cache)
        node_ids = [f["properties"]["id"] for f in result["features"]]
        assert "!mqtt_node" in node_ids

    def test_stale_mqtt_cache_ignored(self, temp_dir):
        """Collector ignores mqtt_nodes.json if > 5 minutes old."""
        from utils.map_data_service import MapDataCollector

        mqtt_cache = temp_dir / "mqtt_nodes.json"
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.5, 19.8]},
                "properties": {"id": "!old_node", "name": "Old"}
            }]
        }
        mqtt_cache.write_text(json.dumps(geojson))

        # Make file appear old
        old_time = time.time() - 400  # 6+ minutes ago
        os.utime(mqtt_cache, (old_time, old_time))

        collector = MapDataCollector(cache_dir=temp_dir, enable_history=False)
        result = collector.collect(max_age_seconds=0)

        # Old node should NOT appear
        node_ids = [f["properties"]["id"] for f in result["features"]]
        assert "!old_node" not in node_ids


# =============================================================================
# Stage 4: NodeHistoryDB Records Observations
# =============================================================================

class TestNodeHistoryRecording:
    """Test that NodeHistoryDB correctly stores observations from GeoJSON."""

    def test_records_from_geojson_features(self, history_db):
        """GeoJSON features are recorded as observations."""
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.5, 19.8]},
                "properties": {
                    "id": "!hist01",
                    "name": "History Node",
                    "snr": -6.5,
                    "battery": 85,
                    "network": "meshtastic",
                    "is_online": True,
                    "via_mqtt": True,
                }
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-157.8, 21.3]},
                "properties": {
                    "id": "!hist02",
                    "name": "Oahu Node",
                    "snr": -3.0,
                    "battery": 100,
                }
            }
        ]

        count = history_db.record_observations(features)
        assert count == 2

        # Verify stored data
        trajectory = history_db.get_trajectory("!hist01", hours=1)
        assert len(trajectory) == 1
        obs = trajectory[0]
        assert obs.node_id == "!hist01"
        assert abs(obs.latitude - 19.8) < 0.001
        assert abs(obs.longitude - (-155.5)) < 0.001
        assert obs.snr == -6.5
        assert obs.battery == 85

    def test_throttles_duplicate_records(self, history_db):
        """Same node within MIN_RECORD_INTERVAL is not re-recorded."""
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-155.5, 19.8]},
            "properties": {"id": "!throttle01"}
        }]

        count1 = history_db.record_observations(features)
        count2 = history_db.record_observations(features)  # Same node, too soon
        assert count1 == 1
        assert count2 == 0  # Throttled

    def test_snapshot_returns_latest(self, history_db):
        """get_snapshot returns most recent observation per node."""
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-155.5, 19.8]},
            "properties": {"id": "!snap01", "snr": -5.0}
        }]
        history_db.record_observations(features)

        snapshot = history_db.get_snapshot(window_seconds=60)
        assert len(snapshot) == 1
        assert snapshot[0].node_id == "!snap01"


# =============================================================================
# Stage 5: Full Round Trip — MQTT → GeoJSON → History
# =============================================================================

class TestFullPipeline:
    """End-to-end pipeline: MQTT message → subscriber → cache → collector → history."""

    def test_mqtt_to_history_round_trip(self, mqtt_subscriber, temp_dir):
        """Complete flow from MQTT message to queryable history."""
        from utils.map_data_service import MapDataCollector

        # Step 1: Inject MQTT messages (position + nodeinfo + telemetry)
        messages = [
            make_mqtt_message("msh/US/2/json/LongFast/!rt01", {
                "from": "!rt01", "type": "nodeinfo",
                "payload": {"id": "!rt01", "longname": "Round Trip Node"}
            }),
            make_mqtt_message("msh/US/2/json/LongFast/!rt01", {
                "from": "!rt01", "type": "position",
                "snr": -4.5,
                "payload": {"latitude": 19.5, "longitude": -155.9, "altitude": 800}
            }),
            make_mqtt_message("msh/US/2/json/LongFast/!rt01", {
                "from": "!rt01", "type": "telemetry",
                "payload": {"device_metrics": {"battery_level": 67}}
            }),
        ]
        for msg in messages:
            mqtt_subscriber._on_message(None, None, msg)

        # Step 2: Persist MQTT cache to temp dir
        with patch('monitoring.mqtt_subscriber.get_real_user_home',
                   return_value=temp_dir):
            mqtt_subscriber._persist_map_cache()

        # Verify cache written
        cache_file = temp_dir / ".local" / "share" / "meshforge" / "mqtt_nodes.json"
        assert cache_file.exists()

        # Step 3: MapDataCollector reads MQTT cache
        # Use the same cache dir structure the subscriber wrote to
        cache_dir = temp_dir / ".local" / "share" / "meshforge"
        collector = MapDataCollector(cache_dir=cache_dir, enable_history=True)

        # Step 4: Collect (will read mqtt_nodes.json + record to history)
        geojson = collector.collect(max_age_seconds=0)

        # Verify node appears in collected data
        node_ids = [f["properties"]["id"] for f in geojson["features"]]
        assert "!rt01" in node_ids

        # Find our node's feature
        rt_feature = next(f for f in geojson["features"]
                          if f["properties"]["id"] == "!rt01")
        assert rt_feature["properties"]["name"] == "Round Trip Node"
        assert rt_feature["geometry"]["coordinates"] == [-155.9, 19.5]

        # Step 5: Verify recorded in history DB
        history = collector._history
        assert history is not None

        trajectory = history.get_trajectory("!rt01", hours=1)
        assert len(trajectory) == 1
        assert abs(trajectory[0].latitude - 19.5) < 0.001
        assert abs(trajectory[0].longitude - (-155.9)) < 0.001

    def test_multiple_nodes_round_trip(self, mqtt_subscriber, temp_dir):
        """Multiple nodes flow through the entire pipeline."""
        from utils.map_data_service import MapDataCollector

        # Create 5 nodes across Hawaii
        nodes_data = [
            ("!hw01", "Mauna Kea", 19.82, -155.47, -3.0),
            ("!hw02", "Mauna Loa", 19.47, -155.59, -7.5),
            ("!hw03", "Diamond Head", 21.26, -157.80, -2.0),
            ("!hw04", "Haleakala", 20.71, -156.25, -5.5),
            ("!hw05", "Ko'olau", 21.35, -157.78, -9.0),
        ]

        for node_id, name, lat, lon, snr in nodes_data:
            msg = make_mqtt_message(f"msh/US/2/json/LongFast/{node_id}", {
                "from": node_id,
                "sender": node_id,
                "type": "position",
                "snr": snr,
                "payload": {"latitude": lat, "longitude": lon}
            })
            mqtt_subscriber._on_message(None, None, msg)

            msg2 = make_mqtt_message(f"msh/US/2/json/LongFast/{node_id}", {
                "from": node_id,
                "type": "nodeinfo",
                "payload": {"id": node_id, "longname": name}
            })
            mqtt_subscriber._on_message(None, None, msg2)

        # Persist to cache
        with patch('monitoring.mqtt_subscriber.get_real_user_home',
                   return_value=temp_dir):
            mqtt_subscriber._persist_map_cache()

        # Collect through pipeline
        cache_dir = temp_dir / ".local" / "share" / "meshforge"
        collector = MapDataCollector(cache_dir=cache_dir, enable_history=True)
        geojson = collector.collect(max_age_seconds=0)

        # All 5 nodes should be in the collection
        assert len(geojson["features"]) == 5
        node_ids = {f["properties"]["id"] for f in geojson["features"]}
        assert node_ids == {"!hw01", "!hw02", "!hw03", "!hw04", "!hw05"}

        # All should be recorded in history
        history = collector._history
        unique = history.get_unique_nodes(hours=1)
        assert len(unique) == 5

    def test_signal_trending_from_pipeline(self, mqtt_subscriber, temp_dir):
        """Signal trending integrates with pipeline data."""
        from utils.map_data_service import MapDataCollector
        from utils.signal_trending import SignalTrendingManager

        # Create node with varying SNR over "time"
        node_id = "!trend01"
        msg = make_mqtt_message(f"msh/US/2/json/LongFast/{node_id}", {
            "from": node_id, "type": "position",
            "snr": -5.0,
            "payload": {"latitude": 21.3, "longitude": -157.8}
        })
        mqtt_subscriber._on_message(None, None, msg)

        # Persist and collect
        with patch('monitoring.mqtt_subscriber.get_real_user_home',
                   return_value=temp_dir):
            mqtt_subscriber._persist_map_cache()

        cache_dir = temp_dir / ".local" / "share" / "meshforge"
        collector = MapDataCollector(cache_dir=cache_dir, enable_history=True)
        collector.collect(max_age_seconds=0)

        # Ingest into trending manager
        mgr = SignalTrendingManager()
        total = mgr.ingest_from_history(collector._history, hours=1)
        assert total >= 1
        assert node_id in mgr.get_tracked_nodes()


# =============================================================================
# Edge Cases
# =============================================================================

class TestPipelineEdgeCases:
    """Edge cases in the data pipeline."""

    def test_node_without_position_not_in_geojson(self, mqtt_subscriber):
        """Nodes without lat/lon don't appear in GeoJSON."""
        msg = make_mqtt_message("msh/US/2/json/LongFast/!nopos", {
            "from": "!nopos", "type": "nodeinfo",
            "payload": {"id": "!nopos", "longname": "No Position"}
        })
        mqtt_subscriber._on_message(None, None, msg)

        geojson = mqtt_subscriber.get_geojson()
        assert len(geojson["features"]) == 0

    def test_corrupted_cache_file_handled(self, temp_dir):
        """Corrupted mqtt_nodes.json doesn't crash collector."""
        from utils.map_data_service import MapDataCollector

        # Write corrupted cache
        cache_file = temp_dir / "mqtt_nodes.json"
        cache_file.write_text("not valid json {{{")

        collector = MapDataCollector(cache_dir=temp_dir, enable_history=False)
        # Should not crash
        result = collector.collect(max_age_seconds=0)
        assert result["type"] == "FeatureCollection"

    def test_missing_geometry_in_feature(self, history_db):
        """Features missing geometry are skipped by history DB."""
        features = [{
            "type": "Feature",
            "properties": {"id": "!nogeom", "name": "No Geometry"}
        }]
        count = history_db.record_observations(features)
        assert count == 0

    def test_empty_feature_list(self, history_db):
        """Empty feature list records nothing."""
        count = history_db.record_observations([])
        assert count == 0

    def test_unicode_node_names(self, mqtt_subscriber, temp_dir):
        """Unicode characters in node names survive pipeline."""
        node = mqtt_subscriber._ensure_node("!unicode01")
        node.latitude = 21.0
        node.longitude = -157.0
        node.long_name = "Aloha \u2600 Node \u2764"

        geojson = mqtt_subscriber.get_geojson()
        feature = geojson["features"][0]
        assert "Aloha" in feature["properties"]["name"]
        assert "\u2600" in feature["properties"]["name"]

    def test_concurrent_messages_no_crash(self, mqtt_subscriber):
        """Rapid sequential messages don't crash (simulated concurrency)."""
        import threading

        def send_messages(start_id):
            for i in range(20):
                node_id = f"!concurrent_{start_id}_{i}"
                msg = make_mqtt_message(f"msh/US/2/json/LongFast/{node_id}", {
                    "from": node_id, "type": "position",
                    "payload": {"latitude": 20.0 + i * 0.01, "longitude": -156.0}
                })
                mqtt_subscriber._on_message(None, None, msg)

        threads = [threading.Thread(target=send_messages, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 80 nodes should exist
        assert len(mqtt_subscriber.get_nodes()) == 80
