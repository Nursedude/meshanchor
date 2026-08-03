"""
Tests for node tracker (node cache management, position/telemetry).

Run: python3 -m pytest tests/test_node_tracker.py -v
"""

import json
import pytest
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.gateway.node_tracker import (
    Position,
    Telemetry,
    UnifiedNode,
    UnifiedNodeTracker,
)


class TestPosition:
    """Tests for Position dataclass."""

    def test_defaults(self):
        """Test default position values."""
        pos = Position()

        assert pos.latitude == 0.0
        assert pos.longitude == 0.0
        assert pos.altitude == 0.0
        assert pos.precision == 5
        assert pos.timestamp is None

    def test_is_valid_with_coordinates(self):
        """Test is_valid returns True with valid coordinates."""
        pos = Position(latitude=21.3069, longitude=-157.8583)
        assert pos.is_valid() is True

    def test_is_valid_false_at_origin(self):
        """Test is_valid returns False at 0,0 (unlikely real location)."""
        pos = Position(latitude=0.0, longitude=0.0)
        assert pos.is_valid() is False

    def test_is_valid_false_out_of_range(self):
        """Test is_valid returns False for out-of-range coordinates."""
        pos1 = Position(latitude=91.0, longitude=0.0)
        pos2 = Position(latitude=0.0, longitude=181.0)

        assert pos1.is_valid() is False
        assert pos2.is_valid() is False

    def test_to_dict(self):
        """Test to_dict serialization."""
        pos = Position(
            latitude=21.3069,
            longitude=-157.8583,
            altitude=10.5,
            timestamp=datetime(2026, 1, 9, 12, 0, 0)
        )

        d = pos.to_dict()

        assert d['latitude'] == 21.3069
        assert d['longitude'] == -157.8583
        assert d['altitude'] == 10.5
        assert '2026-01-09' in d['timestamp']

    def test_to_dict_rounds_precision(self):
        """Test that to_dict respects precision setting."""
        pos = Position(
            latitude=21.30694567,
            longitude=-157.85834567,
            precision=3
        )

        d = pos.to_dict()

        assert d['latitude'] == 21.307
        assert d['longitude'] == -157.858


class TestTelemetry:
    """Tests for Telemetry dataclass."""

    def test_defaults(self):
        """Test default telemetry values are None."""
        telem = Telemetry()

        assert telem.battery_level is None
        assert telem.voltage is None
        assert telem.temperature is None

    def test_to_dict_excludes_none(self):
        """Test to_dict excludes None values."""
        telem = Telemetry(battery_level=85, voltage=3.7)

        d = telem.to_dict()

        assert d['battery_level'] == 85
        assert d['voltage'] == 3.7
        assert 'temperature' not in d
        assert 'humidity' not in d

    def test_to_dict_with_timestamp(self):
        """Test to_dict includes timestamp as ISO string."""
        telem = Telemetry(
            battery_level=90,
            timestamp=datetime(2026, 1, 9, 12, 0, 0)
        )

        d = telem.to_dict()

        assert '2026-01-09' in d['timestamp']


class TestUnifiedNode:
    """Tests for UnifiedNode dataclass."""

    def test_defaults(self):
        """Test default node values."""
        node = UnifiedNode(id="test_123", network="meshtastic")

        assert node.id == "test_123"
        assert node.network == "meshtastic"
        assert node.name == ""
        assert node.is_online is False
        assert node.is_gateway is False
        assert node.first_seen is not None

    def test_update_seen(self):
        """Test update_seen updates timestamp and sets online."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.is_online = False

        node.update_seen()

        assert node.is_online is True
        assert node.last_seen is not None

    def test_get_age_string_never(self):
        """Test get_age_string returns 'Never' when not seen."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = None

        assert node.get_age_string() == "Never"

    def test_get_age_string_seconds(self):
        """Test get_age_string for recent nodes."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(seconds=30)

        assert "30s ago" == node.get_age_string()

    def test_get_age_string_minutes(self):
        """Test get_age_string for nodes seen minutes ago."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(minutes=5)

        assert "5m ago" == node.get_age_string()

    def test_get_age_string_hours(self):
        """Test get_age_string for nodes seen hours ago."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(hours=2)

        assert "2h ago" == node.get_age_string()

    def test_get_age_string_days(self):
        """Test get_age_string for nodes seen days ago."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.last_seen = datetime.now() - timedelta(days=3)

        assert "3d ago" == node.get_age_string()

    def test_to_dict(self):
        """Test to_dict serialization."""
        node = UnifiedNode(
            id="mesh_!abcd1234",
            network="meshtastic",
            name="Test Node",
            short_name="TEST",
            meshtastic_id="!abcd1234",
            is_online=True
        )
        node.position = Position(latitude=21.3, longitude=-157.8)

        d = node.to_dict()

        assert d['id'] == "mesh_!abcd1234"
        assert d['network'] == "meshtastic"
        assert d['name'] == "Test Node"
        assert d['meshtastic_id'] == "!abcd1234"
        assert d['is_online'] is True
        assert d['position'] is not None

    def test_to_dict_with_rns_hash(self):
        """Test to_dict serializes RNS hash as hex."""
        node = UnifiedNode(
            id="rns_abc123",
            network="rns",
            rns_hash=bytes.fromhex('abcd1234')
        )

        d = node.to_dict()

        assert d['rns_hash'] == 'abcd1234'

    def test_from_meshtastic(self):
        """Test creating node from Meshtastic data."""
        mesh_data = {
            'num': 0xabcd1234,
            'user': {
                'longName': 'Test Node',
                'shortName': 'TEST',
                'hwModel': 'HELTEC_V3'
            },
            'position': {
                'latitude': 21.3,
                'longitude': -157.8
            },
            'deviceMetrics': {
                'batteryLevel': 85,
                'voltage': 3.7
            }
        }

        node = UnifiedNode.from_meshtastic(mesh_data)

        assert node.network == "meshtastic"
        assert node.name == "Test Node"
        assert node.short_name == "TEST"
        assert node.meshtastic_id == "!abcd1234"
        assert node.position.latitude == 21.3
        assert node.telemetry.battery_level == 85

    def test_from_rns(self):
        """Test creating node from RNS data."""
        rns_hash = bytes.fromhex('abcd1234567890abcdef')

        node = UnifiedNode.from_rns(rns_hash, name="RNS Node")

        assert node.network == "rns"
        assert node.name == "RNS Node"
        assert node.rns_hash == rns_hash
        assert 'rns_' in node.id


class TestMeshcoreHeardTime:
    """Gateway review finding-C sibling (2026-07-23): from_meshcore accepts a
    live advertisement (heard now) AND stored contact data (carries last_seen —
    e.g. get_contacts()). Hard-coding now()/is_online=True would mark every
    swept stale contact 'online / 0s ago' (honest_failure_modes #1); it must
    honour a heard-time. Matters here — MeshCore is MeshAnchor's primary radio."""

    def test_live_advertisement_heard_now(self):
        node = UnifiedNode.from_meshcore(
            {"adv_name": "MC Live", "pubkey_prefix": "aa11bb22cc33"})
        assert node.is_online is True
        assert (datetime.now() - node.last_seen).total_seconds() < 5

    def test_stale_contact_reads_offline(self):
        stale = datetime.now() - timedelta(minutes=30)   # > 900s window
        node = UnifiedNode.from_meshcore(
            {"adv_name": "MC Stale", "pubkey_prefix": "aa11bb22cc33",
             "last_seen": stale})
        assert node.is_online is False
        assert abs((node.last_seen - stale).total_seconds()) < 2

    def test_recent_contact_reads_online(self):
        recent = datetime.now() - timedelta(minutes=2)   # < 900s window
        node = UnifiedNode.from_meshcore(
            {"adv_name": "MC Recent", "pubkey_prefix": "aa11bb22cc33",
             "last_seen": recent})
        assert node.is_online is True

    def test_epoch_heard_time_honoured(self):
        epoch = datetime.now().timestamp() - 1800        # 30 min ago, epoch secs
        node = UnifiedNode.from_meshcore(
            {"adv_name": "MC Epoch", "pubkey_prefix": "aa11bb22cc33",
             "last_heard": epoch})
        assert node.is_online is False

    def test_future_stamp_not_online_forever(self):
        future = datetime.now() + timedelta(minutes=10)  # forged/skewed clock
        node = UnifiedNode.from_meshcore(
            {"adv_name": "MC Future", "pubkey_prefix": "aa11bb22cc33",
             "last_seen": future})
        assert node.is_online is True                    # "heard now", not negative-age
        assert (datetime.now() - node.last_seen).total_seconds() < 5


class TestUnifiedNodeTracker:
    """Tests for UnifiedNodeTracker class."""

    def test_init(self):
        """Test tracker initialization."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            assert len(tracker._nodes) == 0
            assert tracker._running is False

    def test_add_node(self):
        """Test adding a node."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node = UnifiedNode(id="test_1", network="meshtastic", name="Test")

            tracker.add_node(node)

            assert "test_1" in tracker._nodes
            assert tracker.get_node("test_1") == node

    def test_remove_node(self):
        """Test removing a node."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node = UnifiedNode(id="test_1", network="meshtastic")
            tracker.add_node(node)

            tracker.remove_node("test_1")

            assert tracker.get_node("test_1") is None

    def test_get_all_nodes(self):
        """Test getting all nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node1 = UnifiedNode(id="test_1", network="meshtastic")
            node2 = UnifiedNode(id="test_2", network="rns")
            tracker.add_node(node1)
            tracker.add_node(node2)

            all_nodes = tracker.get_all_nodes()

            assert len(all_nodes) == 2

    def test_get_meshtastic_nodes(self):
        """Test filtering meshtastic nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="m1", network="meshtastic"))
            tracker.add_node(UnifiedNode(id="r1", network="rns"))
            tracker.add_node(UnifiedNode(id="b1", network="both"))

            mesh_nodes = tracker.get_meshtastic_nodes()

            assert len(mesh_nodes) == 2  # meshtastic + both

    def test_get_rns_nodes(self):
        """Test filtering RNS nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="m1", network="meshtastic"))
            tracker.add_node(UnifiedNode(id="r1", network="rns"))
            tracker.add_node(UnifiedNode(id="b1", network="both"))

            rns_nodes = tracker.get_rns_nodes()

            assert len(rns_nodes) == 2  # rns + both

    def test_get_node_by_mesh_id(self):
        """Test finding node by Meshtastic ID."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            node = UnifiedNode(id="test_1", network="meshtastic", meshtastic_id="!abcd1234")
            tracker.add_node(node)
            tracker.add_node(UnifiedNode(id="test_2", network="meshtastic", meshtastic_id="!efgh5678"))

            result = tracker.get_node_by_mesh_id("!abcd1234")

            assert result is not None
            assert result.id == "test_1"
            assert result.meshtastic_id == "!abcd1234"

    def test_get_node_by_mesh_id_not_found(self):
        """Test get_node_by_mesh_id returns None when not found."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="test_1", network="meshtastic", meshtastic_id="!abcd1234"))

            result = tracker.get_node_by_mesh_id("!nonexistent")

            assert result is None

    def test_get_node_by_rns_hash(self):
        """Test finding node by RNS hash."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            rns_hash = bytes.fromhex('abcd1234567890abcdef0123456789ab')
            node = UnifiedNode(id="rns_1", network="rns", rns_hash=rns_hash)
            tracker.add_node(node)

            result = tracker.get_node_by_rns_hash(rns_hash)

            assert result is not None
            assert result.id == "rns_1"
            assert result.rns_hash == rns_hash

    def test_get_node_by_rns_hash_not_found(self):
        """Test get_node_by_rns_hash returns None when not found."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            rns_hash = bytes.fromhex('abcd1234567890abcdef0123456789ab')
            tracker.add_node(UnifiedNode(id="rns_1", network="rns", rns_hash=rns_hash))

            other_hash = bytes.fromhex('ffff1234567890abcdef0123456789ff')
            result = tracker.get_node_by_rns_hash(other_hash)

            assert result is None

    def test_get_nodes_with_position(self):
        """Test filtering nodes with valid positions."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node_with_pos = UnifiedNode(id="pos1", network="meshtastic")
            node_with_pos.position = Position(latitude=21.3, longitude=-157.8)

            node_without_pos = UnifiedNode(id="nopos", network="meshtastic")

            tracker.add_node(node_with_pos)
            tracker.add_node(node_without_pos)

            positioned = tracker.get_nodes_with_position()

            assert len(positioned) == 1
            assert positioned[0].id == "pos1"

    def test_get_online_nodes(self):
        """Test filtering online nodes."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            online = UnifiedNode(id="on1", network="meshtastic")
            online.is_online = True

            offline = UnifiedNode(id="off1", network="meshtastic")
            offline.is_online = False

            tracker.add_node(online)
            tracker.add_node(offline)

            online_nodes = tracker.get_online_nodes()

            assert len(online_nodes) == 1

    def test_get_stats(self):
        """Test statistics generation."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            tracker.add_node(UnifiedNode(id="m1", network="meshtastic"))
            tracker.add_node(UnifiedNode(id="r1", network="rns"))

            node_online = UnifiedNode(id="m2", network="meshtastic")
            node_online.is_online = True
            tracker.add_node(node_online)

            stats = tracker.get_stats()

            assert stats['total'] == 3
            assert stats['meshtastic'] == 2
            assert stats['rns'] == 1
            assert stats['online'] == 1

    def test_register_callback(self):
        """Test callback registration."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            callback = MagicMock()

            tracker.register_callback(callback)
            node = UnifiedNode(id="test", network="meshtastic")
            tracker.add_node(node)

            callback.assert_called_once_with("update", node)

    def test_unregister_callback(self):
        """Test callback unregistration."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            callback = MagicMock()

            tracker.register_callback(callback)
            tracker.unregister_callback(callback)
            tracker.add_node(UnifiedNode(id="test", network="meshtastic"))

            callback.assert_not_called()

    def test_merge_node_updates_network(self):
        """Test that merging nodes updates network to 'both'."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            mesh_node = UnifiedNode(id="test", network="meshtastic")
            tracker.add_node(mesh_node)

            rns_node = UnifiedNode(id="test", network="rns")
            tracker.add_node(rns_node)

            result = tracker.get_node("test")
            assert result.network == "both"

    def test_merge_node_keeps_better_name(self):
        """Test that merge keeps better name."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node1 = UnifiedNode(id="test", network="meshtastic", name="!abcd1234")
            tracker.add_node(node1)

            node2 = UnifiedNode(id="test", network="meshtastic", name="Good Name")
            tracker.add_node(node2)

            result = tracker.get_node("test")
            assert result.name == "Good Name"

    def test_thread_safety(self):
        """Test thread-safe node operations."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()
            errors = []

            def add_nodes(prefix, count):
                try:
                    for i in range(count):
                        node = UnifiedNode(id=f"{prefix}_{i}", network="meshtastic")
                        tracker.add_node(node)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=add_nodes, args=("a", 50)),
                threading.Thread(target=add_nodes, args=("b", 50)),
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            assert len(tracker.get_all_nodes()) == 100


class TestSelfReportedNameReplacesStaleName:
    """A node's announced name must be able to correct a stale cached one.

    Twin of MeshForge 2026-07-21: _merge_node only replaced a name when the
    existing one was empty or started with "!", so a name recorded once was
    permanent — when the propagation parser was fixed, the cache kept serving
    the old mojibake while the log showed the correct name. The guard itself
    is right (an unparseable announce falls back to a hash placeholder); the
    missing distinction was PROVENANCE.
    """

    def _tracker(self, tmp_path):
        cache_file = tmp_path / "node_cache.json"
        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                return UnifiedNodeTracker()

    def test_self_reported_name_replaces_stale_garbage(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_node(UnifiedNode(id="rns_abc", network="rns", name="j^x("))

        fresh = UnifiedNode(id="rns_abc", network="rns", name="WH6GXZ MeshForge PN")
        fresh.name_is_self_reported = True
        tracker.add_node(fresh)

        assert tracker._nodes["rns_abc"].name == "WH6GXZ MeshForge PN"

    def test_placeholder_never_overwrites_a_good_name(self, tmp_path):
        tracker = self._tracker(tmp_path)
        good = UnifiedNode(id="rns_abc", network="rns", name="WH6GXZ MeshForge PN")
        good.name_is_self_reported = True
        tracker.add_node(good)

        tracker.add_node(UnifiedNode(id="rns_abc", network="rns", name="3968a2ee"))

        assert tracker._nodes["rns_abc"].name == "WH6GXZ MeshForge PN"

    def test_meshtastic_style_merge_is_unchanged(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_node(UnifiedNode(id="mesh_1", network="meshtastic", name="Real Name"))
        tracker.add_node(UnifiedNode(id="mesh_1", network="meshtastic", name="Other"))

        assert tracker._nodes["mesh_1"].name == "Real Name"

    def test_meshtastic_rename_corrects_stale_cached_name(self, tmp_path):
        """MeshForge 2026-07-21 review (W1): the stale-name fix only covered
        the RNS leg — from_meshtastic never set the provenance flag, so a
        Meshtastic node that renamed itself was mis-named in the cache
        forever. longName IS self-reported (it comes from the node's own
        NodeInfo)."""
        tracker = self._tracker(tmp_path)
        old = UnifiedNode.from_meshtastic(
            {"num": 1, "user": {"longName": "Old Name"}})
        tracker.add_node(old)
        renamed = UnifiedNode.from_meshtastic(
            {"num": 1, "user": {"longName": "New Name"}})
        tracker.add_node(renamed)

        assert tracker._nodes[renamed.id].name == "New Name"

    def test_meshtastic_id_fallback_is_not_self_reported(self):
        """The !hex fallback is a placeholder — it must never displace a name."""
        node = UnifiedNode.from_meshtastic({"num": 1, "user": {}})
        assert node.name_is_self_reported is False

    def test_meshcore_role_promotion_refreshes_on_merge(self, tmp_path):
        """W1: a MeshCore node promoted client→repeater kept the stale role
        for the life of the cache entry — meshcore_* now refresh on merge."""
        tracker = self._tracker(tmp_path)
        client = UnifiedNode.from_meshcore(
            {"adv_name": "MC One", "pubkey_prefix": "abcdef123456",
             "role": "client", "hops": 3})
        tracker.add_node(client)
        promoted = UnifiedNode.from_meshcore(
            {"adv_name": "MC One Prime", "pubkey_prefix": "abcdef123456",
             "role": "repeater", "hops": 1})
        tracker.add_node(promoted)

        merged = tracker._nodes[promoted.id]
        assert merged.meshcore_role == "repeater"
        assert merged.meshcore_hops == 1
        assert merged.name == "MC One Prime"   # adv_name is self-reported

    def test_heuristic_decoded_name_carries_no_correction_authority(self):
        """MeshForge 2026-07-21 review (W2): the delivery-leg byte-scan and
        the Generic/Nomad errors='ignore' decodes can render arbitrary bytes
        as a printable 'name' — the mojibake mechanism. Since self-reported
        names now OVERWRITE cached ones, a heuristic guess must not be marked
        self-reported (it may still fill an empty slot)."""
        from src.gateway.node_models import ServiceInfo, RNSServiceType

        info = ServiceInfo(service_type=RNSServiceType.UNKNOWN,
                           aspect="unknown",
                           display_name="j^x((",
                           display_name_is_parsed=False)
        node = UnifiedNode.from_rns(
            bytes.fromhex("3968a2eeac25e2e7a7961f25842d3d85"),
            service_info=info)

        assert node.name == "j^x(("               # something beats nothing
        assert node.name_is_self_reported is False  # but it cannot correct

    def test_placeholder_still_fills_an_empty_name(self, tmp_path):
        tracker = self._tracker(tmp_path)
        tracker.add_node(UnifiedNode(id="rns_abc", network="rns", name=""))
        tracker.add_node(UnifiedNode(id="rns_abc", network="rns", name="3968a2ee"))

        assert tracker._nodes["rns_abc"].name == "3968a2ee"


class TestNodeTrackerCache:
    """Tests for cache save/load functionality."""

    def test_save_cache(self, tmp_path):
        """Test saving node cache."""
        cache_file = tmp_path / "node_cache.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                tracker.add_node(UnifiedNode(
                    id="test_1",
                    network="meshtastic",
                    name="Test Node"
                ))

                tracker._save_cache()

                assert cache_file.exists()
                with open(cache_file) as f:
                    data = json.load(f)
                assert len(data['nodes']) == 1

    def test_load_cache(self, tmp_path):
        """Test loading node cache."""
        cache_file = tmp_path / "node_cache.json"
        cache_data = {
            'version': 1,
            'nodes': [{
                'id': 'cached_1',
                'network': 'meshtastic',
                'name': 'Cached Node',
                'short_name': 'CN',
                'meshtastic_id': '!12345678'
            }]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            assert len(tracker._nodes) == 1
            assert 'cached_1' in tracker._nodes
            assert tracker._nodes['cached_1'].name == 'Cached Node'

    def test_load_cache_restores_service_type(self, tmp_path):
        """service_type must survive the save/load round-trip.

        Twin of the MeshForge regression found live 2026-07-21: to_dict()
        wrote service_type but _load_cache() restored the other fields and
        silently dropped it, so every restart erased the RNS service type of
        every known node until it announced again (honest_failure_modes #4 —
        a writer with no matching reader).
        """
        cache_file = tmp_path / "node_cache.json"
        cache_data = {
            'version': 1,
            'nodes': [{
                'id': 'rns_3968a2eeac25e2e7',
                'network': 'rns',
                'name': 'propagation node',
                'rns_hash': '3968a2eeac25e2e7a7961f25842d3d85',
                'service_type': 'LXMF_PROPAGATION',
                'last_seen': '2026-07-21T03:37:44.163446',
            }]
        }
        cache_file.write_text(json.dumps(cache_data))

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            assert tracker._nodes['rns_3968a2eeac25e2e7'].service_type == 'LXMF_PROPAGATION'

    @staticmethod
    def _fully_populated_node():
        from src.gateway.node_models import (
            AirQualityMetrics, DetectionSensor, HealthMetrics, PKIStatus,
            Position, Telemetry)
        from datetime import datetime
        node = UnifiedNode(
            id="mesh_!deadbeef", network="meshtastic", name="Full Node",
            short_name="FULL", meshtastic_id="!deadbeef",
            rns_hash=bytes.fromhex("aa" * 16),
            hops=3, is_gateway=True, is_local=True,
            hardware_model="RAK4631", firmware_version="2.7.9",
            role="ROUTER", name_is_self_reported=True,
            service_type="LXMF_PROPAGATION", service_aspect="lxmf.propagation",
            service_capabilities=["propagation"],
            meshcore_pubkey="abcdef123456", meshcore_role="repeater",
            meshcore_hops=2, is_favorite=True,
        )
        ts = datetime(2026, 7, 20, 10, 0, 0)
        node.position = Position(latitude=19.5, longitude=-155.5,
                                 altitude=1200.0, timestamp=ts)
        node.telemetry = Telemetry(
            battery_level=88, voltage=4.1, channel_utilization=7.5,
            air_util_tx=1.2, uptime=3600, temperature=24.5, humidity=60.0,
            pressure=1013.2, gas_resistance=120000.0,
            air_quality=AirQualityMetrics(pm25_standard=8, co2=420, iaq=51),
            health=HealthMetrics(heart_rate=64, spo2=98),
            detection_sensors=[DetectionSensor(name="Door", triggered=True,
                                               gpio_pin=17, trigger_count=4,
                                               last_triggered=ts)],
            timestamp=ts)
        node.pki_status = PKIStatus.from_public_key(b"\x01" * 32)
        node.first_seen = ts
        node.last_seen = ts
        node.snr, node.rssi = 7.25, -95
        node.favorite_updated = ts
        return node

    # Keys legitimately different across a save→load cycle:
    _ROUNDTRIP_VOLATILE = {
        "is_online",       # loader forces offline until re-heard (by design)
        "last_seen_ago",   # rendered age string, wall-clock dependent
        # Pri-3 (07-23 review, MF twin): `state` comes from the STATE MACHINE
        # when one is present (not "derived from is_online" — a false
        # justification that let a persisted ONLINE contradict the forced
        # is_online=False). The loader now resets an active restored state to
        # STALE_CACHE, so these can differ from a node ONLINE at save. Dedicated
        # invariant: test_active_state_reloads_as_stale_cache_no_contradiction.
        "state", "state_display", "state_icon",
        "snr_trend", "rssi_trend",  # need ≥2 history samples; N/A here
    }

    def test_save_then_load_round_trip_is_field_complete(self, tmp_path):
        """MeshForge 2026-07-21 review (C2), ported as the twin: the loader
        restored a handful of fields and silently dropped the rest —
        telemetry, hops, is_gateway, is_local, firmware_version,
        service_aspect/capabilities, pki_status, position timestamp — the
        same writer-with-no-reader class as service_type, which the old
        single-field test could never catch. This compares the FULL
        serialized shape, so the NEXT field added to to_dict() fails here
        unless the loader learns it too."""
        cache_file = tmp_path / "node_cache.json"
        node = self._fully_populated_node()
        original = node.to_dict(include_signal_history=True)

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                tracker._nodes[node.id] = node
                tracker._save_cache()

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            reloaded = UnifiedNodeTracker()

        restored = reloaded._nodes[node.id].to_dict(include_signal_history=True)
        for key in self._ROUNDTRIP_VOLATILE:
            original.pop(key, None)
            restored.pop(key, None)
        assert restored == original

    def test_pki_baseline_survives_restart_and_still_detects_mitm(self, tmp_path):
        """MeshForge 2026-07-21 review (C1): pki_status was written but never
        restored, so every restart erased the TOFU key baseline and a key
        change across a restart was silently re-TOFU'd as first-seen — the
        MITM branch could never fire. The baseline must survive, and a
        DIFFERENT key observed after reload must read CHANGED."""
        from src.gateway.node_models import PKIKeyState, PKIStatus
        cache_file = tmp_path / "node_cache.json"
        key_a, key_b = b"\x01" * 32, b"\x02" * 32

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                node = UnifiedNode(id="mesh_!abc", network="meshtastic", name="n")
                node.pki_status = PKIStatus.from_public_key(key_a)
                tracker._nodes[node.id] = node
                tracker._save_cache()

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            reloaded = UnifiedNodeTracker()

        restored = reloaded._nodes["mesh_!abc"]
        assert restored.pki_status.state == PKIKeyState.TRUSTED
        assert restored.pki_status.public_key == key_a
        assert restored.update_pki_status(key_b) is True   # change detected
        assert restored.pki_status.state == PKIKeyState.CHANGED

    def test_active_state_reloads_as_stale_cache_no_contradiction(self, tmp_path):
        """Pri-3 (07-23 review, MF twin): a node persisted while ONLINE must NOT
        reload with state==ONLINE while is_online==False — a not-yet-heard node
        reading as live. The loader resets an active restored state to
        STALE_CACHE so state.is_active() and is_online agree, history survives."""
        from src.gateway.node_state import NodeState
        from src.gateway.node_models import NODE_STATE_AVAILABLE
        if not NODE_STATE_AVAILABLE:
            import pytest
            pytest.skip("node_state machine not available")
        cache_file = tmp_path / "node_cache.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                node = UnifiedNode(id="mesh_!live", network="meshtastic", name="n")
                node.update_seen()  # drive the machine to ONLINE
                assert node.state == NodeState.ONLINE
                assert node.is_online is True
                tracker._nodes[node.id] = node
                tracker._save_cache()

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            reloaded = UnifiedNodeTracker()

        r = reloaded._nodes["mesh_!live"]
        assert r.is_online is False
        assert r.state == NodeState.STALE_CACHE
        assert r.state.is_active() is False
        assert r.state.is_active() == r.is_online  # consistent, not contradictory
        assert len(r._state_machine.get_transitions()) >= 1

    def test_offline_state_is_preserved_on_reload(self, tmp_path):
        """The reconcile only touches ACTIVE states — a persisted OFFLINE is
        already consistent with is_online=False and keeps its informative label."""
        from src.gateway.node_state import NodeState
        from src.gateway.node_models import NODE_STATE_AVAILABLE
        if not NODE_STATE_AVAILABLE:
            import pytest
            pytest.skip("node_state machine not available")
        cache_file = tmp_path / "node_cache.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                node = UnifiedNode(id="mesh_!gone", network="meshtastic", name="n")
                node._state_machine.transition_to(NodeState.OFFLINE, "test")
                tracker._nodes[node.id] = node
                tracker._save_cache()

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            reloaded = UnifiedNodeTracker()

        r = reloaded._nodes["mesh_!gone"]
        assert r.state == NodeState.OFFLINE
        assert r.is_online is False
        assert r.state.is_active() == r.is_online

    def test_merge_routes_observed_key_through_existing_tofu_state(self, tmp_path):
        """C1 second half: from_meshtastic() TOFUs the key on the THROWAWAY
        new object and _merge_node dropped it — so a key change for an
        already-known node never hit the existing node's state machine."""
        from src.gateway.node_models import PKIKeyState, PKIStatus
        cache_file = tmp_path / "node_cache.json"
        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            with patch.object(UnifiedNodeTracker, '_load_cache'):
                tracker = UnifiedNodeTracker()
                known = UnifiedNode(id="mesh_!abc", network="meshtastic", name="n")
                known.pki_status = PKIStatus.from_public_key(b"\x01" * 32)
                tracker.add_node(known)

                announce = UnifiedNode(id="mesh_!abc", network="meshtastic", name="n")
                announce.pki_status = PKIStatus.from_public_key(b"\x02" * 32)
                tracker.add_node(announce)

                assert tracker._nodes["mesh_!abc"].pki_status.state == PKIKeyState.CHANGED

    def test_load_cache_handles_missing_file(self, tmp_path):
        """Test loading when cache file doesn't exist."""
        cache_file = tmp_path / "nonexistent.json"

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            assert len(tracker._nodes) == 0

    def test_load_cache_handles_corrupted_file(self, tmp_path):
        """Test loading handles corrupted cache gracefully."""
        cache_file = tmp_path / "node_cache.json"
        cache_file.write_text("not valid json {{{")

        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file):
            tracker = UnifiedNodeTracker()

            # Should not raise, just start empty
            assert len(tracker._nodes) == 0


class TestRNSAnnounceHandling:
    """Tests for RNS announce parsing and handling."""

    def test_from_rns_with_name_in_app_data(self):
        """Test from_rns extracts display name from app_data."""
        rns_hash = bytes.fromhex('abcd1234567890abcdef1234567890ab')
        app_data = b"Alice's Node"

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        assert node.name == "Alice's Node"
        assert node.network == "rns"

    def test_from_rns_with_msgpack_telemetry(self):
        """Test from_rns parses msgpack telemetry with position."""
        try:
            import msgpack
        except ImportError:
            pytest.skip("msgpack not installed")

        rns_hash = bytes.fromhex('abcd1234567890abcdef1234567890ab')

        # Create app_data: display name + msgpack telemetry
        name_bytes = b"GPS Node"
        telemetry = {"latitude": 21.3069, "longitude": -157.8583, "altitude": 10.0}
        telemetry_bytes = msgpack.packb(telemetry)
        app_data = name_bytes + telemetry_bytes

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        assert node.name == "GPS Node"
        assert node.position.is_valid()
        assert abs(node.position.latitude - 21.3069) < 0.001
        assert abs(node.position.longitude - (-157.8583)) < 0.001

    def test_from_rns_with_sideband_style_telemetry(self):
        """Test from_rns parses Sideband-style telemetry keys."""
        try:
            import msgpack
        except ImportError:
            pytest.skip("msgpack not installed")

        rns_hash = bytes.fromhex('1234567890abcdef1234567890abcdef')

        # Sideband uses 'lat', 'lon', 'alt' keys
        telemetry = {"lat": 19.896, "lon": -155.582, "alt": 45.0, "speed": 5.2}
        app_data = b"Sideband" + msgpack.packb(telemetry)

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        assert node.position.is_valid()
        assert abs(node.position.latitude - 19.896) < 0.001
        assert abs(node.position.longitude - (-155.582)) < 0.001

    def test_from_rns_with_invalid_coordinates(self):
        """Test from_rns rejects out-of-range coordinates."""
        try:
            import msgpack
        except ImportError:
            pytest.skip("msgpack not installed")

        rns_hash = bytes.fromhex('deadbeef12345678deadbeef12345678')
        telemetry = {"latitude": 999.0, "longitude": -157.8}  # Invalid lat
        app_data = b"BadGPS" + msgpack.packb(telemetry)

        node = UnifiedNode.from_rns(rns_hash, app_data=app_data)

        # Position should NOT be set for invalid coordinates
        assert not node.position.is_valid()

    def test_from_rns_without_app_data(self):
        """Test from_rns works without app_data."""
        rns_hash = bytes.fromhex('cafebabe12345678cafebabe12345678')

        node = UnifiedNode.from_rns(rns_hash)

        assert node.network == "rns"
        assert node.rns_hash == rns_hash
        # Name should be derived from hash
        assert node.name == rns_hash.hex()[:8]

    def test_parse_lxmf_app_data_name_only(self):
        """Test _parse_lxmf_app_data with name-only data."""
        app_data = b"My Node Name"

        result = UnifiedNode._parse_lxmf_app_data(app_data)

        assert result.get("display_name") == "My Node Name"
        assert result.get("latitude") is None

    def test_parse_lxmf_app_data_empty(self):
        """Test _parse_lxmf_app_data with empty data."""
        result = UnifiedNode._parse_lxmf_app_data(b"")
        assert result == {}

    def test_on_rns_announce_adds_node(self):
        """Test _on_rns_announce adds node to tracker."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            dest_hash = bytes.fromhex('1122334455667788aabbccddeeff0011')
            announced_identity = MagicMock()
            app_data = b"Announced Node"

            tracker._on_rns_announce(dest_hash, announced_identity, app_data)

            # Verify node was added
            nodes = tracker.get_rns_nodes()
            assert len(nodes) == 1
            assert nodes[0].name == "Announced Node"

    def test_on_rns_announce_error_handling(self):
        """Test _on_rns_announce handles errors gracefully."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            # Should not raise even with bad data
            tracker._on_rns_announce(None, None, None)

            # Tracker should still be operational
            assert len(tracker._nodes) == 0


class TestGeoJSON:
    """Tests for GeoJSON export."""

    def test_to_geojson_format(self):
        """Test GeoJSON output format."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node = UnifiedNode(id="geo1", network="meshtastic", name="GeoNode")
            node.position = Position(latitude=21.3, longitude=-157.8)
            tracker.add_node(node)

            geojson = tracker.to_geojson()

            assert geojson['type'] == 'FeatureCollection'
            assert len(geojson['features']) == 1

            feature = geojson['features'][0]
            assert feature['type'] == 'Feature'
            assert feature['geometry']['type'] == 'Point'
            assert feature['geometry']['coordinates'] == [-157.8, 21.3]

    def test_to_geojson_excludes_nodes_without_position(self):
        """Test GeoJSON excludes nodes without valid positions."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            with_pos = UnifiedNode(id="pos1", network="meshtastic")
            with_pos.position = Position(latitude=21.3, longitude=-157.8)

            without_pos = UnifiedNode(id="nopos", network="meshtastic")

            tracker.add_node(with_pos)
            tracker.add_node(without_pos)

            geojson = tracker.to_geojson()

            assert len(geojson['features']) == 1


class TestSignalQualityTrending:
    """Tests for signal quality trending feature."""

    def test_record_signal_quality_snr(self):
        """Test recording SNR values."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(snr=10.5)

        assert node.snr == 10.5
        assert len(node.snr_history) == 1
        assert node.snr_history[0].value == 10.5

    def test_record_signal_quality_rssi(self):
        """Test recording RSSI values."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(rssi=-75)

        assert node.rssi == -75
        assert len(node.rssi_history) == 1
        assert node.rssi_history[0].value == -75.0

    def test_record_signal_quality_both(self):
        """Test recording both SNR and RSSI together."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(snr=8.0, rssi=-80)

        assert node.snr == 8.0
        assert node.rssi == -80
        assert len(node.snr_history) == 1
        assert len(node.rssi_history) == 1

    def test_history_accumulates(self):
        """Test that signal history accumulates over multiple recordings."""
        node = UnifiedNode(id="test", network="meshtastic")

        for i in range(5):
            node.record_signal_quality(snr=float(i))

        assert len(node.snr_history) == 5
        assert node.snr_history[0].value == 0.0
        assert node.snr_history[4].value == 4.0

    def test_history_max_samples(self):
        """Test that history is trimmed to MAX_SIGNAL_SAMPLES."""
        node = UnifiedNode(id="test", network="meshtastic")
        node.MAX_SIGNAL_SAMPLES = 10  # Override for testing

        for i in range(15):
            node.record_signal_quality(snr=float(i))

        assert len(node.snr_history) == 10
        # Should keep the most recent 10
        assert node.snr_history[0].value == 5.0
        assert node.snr_history[9].value == 14.0

    def test_snr_trend_unknown_insufficient_data(self):
        """Test trend returns 'unknown' with insufficient data."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Less than 5 samples
        for i in range(3):
            node.record_signal_quality(snr=float(i))

        assert node.snr_trend == "unknown"

    def test_snr_trend_improving(self):
        """Test SNR trend detection for improving signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Older samples: low SNR (0-4)
        for i in range(5):
            node.record_signal_quality(snr=float(i))
        # Recent samples: high SNR (10-14) - clear improvement
        for i in range(10, 15):
            node.record_signal_quality(snr=float(i))

        assert node.snr_trend == "improving"

    def test_snr_trend_degrading(self):
        """Test SNR trend detection for degrading signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Older samples: high SNR
        for i in range(10, 15):
            node.record_signal_quality(snr=float(i))
        # Recent samples: low SNR - clear degradation
        for i in range(5):
            node.record_signal_quality(snr=float(i))

        assert node.snr_trend == "degrading"

    def test_snr_trend_stable(self):
        """Test SNR trend detection for stable signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # All samples around the same value
        for _ in range(10):
            node.record_signal_quality(snr=5.0)

        assert node.snr_trend == "stable"

    def test_rssi_trend_improving(self):
        """Test RSSI trend detection for improving signal."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Older samples: low RSSI (worse signal)
        for i in range(5):
            node.record_signal_quality(rssi=-90 + i)
        # Recent samples: high RSSI (better signal)
        for i in range(5):
            node.record_signal_quality(rssi=-70 + i)

        assert node.rssi_trend == "improving"

    def test_get_signal_stats(self):
        """Test signal statistics calculation."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Add some varied SNR values
        for snr in [5.0, 10.0, 8.0, 12.0, 6.0, 9.0, 11.0, 7.0, 10.0, 8.0]:
            node.record_signal_quality(snr=snr)

        stats = node.get_signal_stats()

        assert 'snr' in stats
        assert stats['snr']['min'] == 5.0
        assert stats['snr']['max'] == 12.0
        assert stats['snr']['samples'] == 10
        assert stats['snr']['current'] == 8.0
        assert 'trend' in stats['snr']

    def test_to_dict_includes_trends(self):
        """Test that to_dict includes trend information."""
        node = UnifiedNode(id="test", network="meshtastic")

        # Add enough samples for trend
        for i in range(10):
            node.record_signal_quality(snr=float(i), rssi=-80 + i)

        d = node.to_dict()

        assert 'snr_trend' in d
        assert 'rssi_trend' in d
        assert d['snr_trend'] is not None

    def test_to_dict_with_signal_history(self):
        """Test that to_dict includes history when requested."""
        node = UnifiedNode(id="test", network="meshtastic")

        node.record_signal_quality(snr=10.0, rssi=-75)

        # Without history
        d_minimal = node.to_dict(include_signal_history=False)
        assert 'snr_history' not in d_minimal

        # With history
        d_full = node.to_dict(include_signal_history=True)
        assert 'snr_history' in d_full
        assert len(d_full['snr_history']) == 1
        assert 'rssi_history' in d_full

    def test_merge_node_records_signal(self):
        """Test that merging nodes records signal quality."""
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            tracker = UnifiedNodeTracker()

            node1 = UnifiedNode(id="test", network="meshtastic", snr=5.0)
            tracker.add_node(node1)

            node2 = UnifiedNode(id="test", network="meshtastic", snr=8.0)
            tracker.add_node(node2)

            result = tracker.get_node("test")

            # Should have recorded both signal values
            assert result.snr == 8.0
            assert len(result.snr_history) == 1  # Only from merge, not initial add

    def test_signal_sample_to_dict(self):
        """Test SignalSample serialization."""
        from src.gateway.node_tracker import SignalSample

        sample = SignalSample(
            timestamp=datetime(2026, 1, 15, 12, 30, 0),
            value=10.5
        )

        d = sample.to_dict()

        assert d['value'] == 10.5
        assert '2026-01-15' in d['timestamp']


class TestCacheWriteChurn20260803:
    """Cache-write churn — ported from MeshForge d101d5ff, re-measured here.

    MeshForge's 18.6 h tracemalloc soak found its gateway was never leaking;
    it was serializing its whole node population on every 60 s tick. The same
    code runs here, and meshanchor-server measured the same disease on
    2026-08-03: node_cache.json 11,334,223 B + rns_nodes.json 8,917,591 B =
    20.2 MB per pass, one of them pretty-printed, x1440 = ~29 GB/day of
    fsync'd writes — for 9,051 RNS announce-space nodes serving five local
    MeshCore radios.

    These pin the cure: ONE compact serialization reused for both files, a
    cadence the write path actually honours, and a dirty gate built so it can
    never silently freeze the cache.
    """

    @pytest.fixture
    def tracker(self, tmp_path):
        """A tracker whose BOTH cache files land in tmp_path.

        The web-API copy is patched too, deliberately: _save_cache writes a
        second operator-owned file via MeshAnchorPaths.rns_nodes_cache_path(),
        and a test that leaves it unpatched overwrites the live node cache of
        whatever box runs the suite (feedback_tests_must_pin_ambient_state).
        """
        cache_file = tmp_path / "node_cache.json"
        web_cache = tmp_path / "rns_nodes.json"
        with patch.object(UnifiedNodeTracker, 'get_cache_file', return_value=cache_file), \
             patch.object(UnifiedNodeTracker, '_load_cache'), \
             patch('utils.paths.MeshAnchorPaths.rns_nodes_cache_path', return_value=web_cache):
            t = UnifiedNodeTracker()
            t.add_node(UnifiedNode(id="n1", network="meshcore", name="One"))
            yield t, cache_file, web_cache

    # --- one compact serialization, not two (one indented) ------------------

    def test_primary_cache_is_compact_not_indented(self, tracker):
        """indent=2 cost ~2.4 MB per write and nothing reads 11 MB by eye."""
        t, cache_file, _ = tracker
        t._save_cache()
        text = cache_file.read_text()
        assert '\n  ' not in text, "cache is pretty-printed; indent=2 is pure write amplification"
        json.loads(text)  # still valid JSON

    def test_both_files_receive_identical_bytes(self, tracker):
        """Same payload -> serialize once, write twice."""
        t, cache_file, web_cache = tracker
        t._save_cache()
        assert cache_file.read_bytes() == web_cache.read_bytes()

    def test_serializes_exactly_once_per_save(self, tracker):
        """Two json.dumps of 9k nodes doubles the transient allocation peak."""
        t, _, _ = tracker
        import src.gateway.node_tracker as nt
        with patch.object(nt.json, 'dumps', wraps=nt.json.dumps) as dumps:
            t._save_cache()
        assert dumps.call_count == 1, f"serialized {dumps.call_count}x per save"

    # --- cadence: not on every 60 s tick ------------------------------------

    def test_cadence_is_not_every_tick(self, tracker):
        """Ten minutes of CLEANUP_TICK ticks must not produce ten writes.

        Derived from the constants rather than hardcoding "5 minutes": a test
        that pins a VALUE gets edited the moment the constant moves, a test
        that pins a RELATIONSHIP gets re-derived.
        """
        t, _, _ = tracker
        clock = [1000.0]
        ticks = 10
        expected_max = int(
            (ticks * UnifiedNodeTracker.CLEANUP_TICK)
            / UnifiedNodeTracker.CACHE_SAVE_INTERVAL
        ) + 1
        # wraps=, not a bare mock: _save_cache is what advances the cadence
        # clock, so replacing it outright would break the very feedback loop
        # under test and the assertion would pin nothing
        # (feedback_verify_the_verification — the mock standing in for the
        # layer that matters).
        with patch.object(t, '_save_cache', wraps=t._save_cache) as save, \
             patch('src.gateway.node_tracker.time.monotonic', side_effect=lambda: clock[0]):
            t._last_cache_save = clock[0]
            for _ in range(ticks):
                clock[0] += UnifiedNodeTracker.CLEANUP_TICK
                t._mark_cache_dirty()
                t._maybe_save_cache()
        assert save.call_count <= expected_max, (
            f"{save.call_count} writes in {ticks} ticks; cadence not applied"
        )
        assert UnifiedNodeTracker.CACHE_SAVE_INTERVAL >= 2 * UnifiedNodeTracker.CLEANUP_TICK, (
            "a write cadence at or below the tick rate is no cadence at all"
        )

    def test_clean_tracker_inside_window_does_not_write(self, tracker):
        """Unchanged state is not worth 20 MB of fsync'd writes."""
        t, _, _ = tracker
        clock = [1000.0]
        with patch.object(UnifiedNodeTracker, '_save_cache') as save, \
             patch('src.gateway.node_tracker.time.monotonic', side_effect=lambda: clock[0]):
            t._cache_dirty = False
            t._last_cache_save = clock[0]
            clock[0] += UnifiedNodeTracker.CACHE_SAVE_INTERVAL
            t._maybe_save_cache()
        assert save.call_count == 0

    def test_staleness_ceiling_writes_even_when_never_marked_dirty(self, tracker):
        """A missed dirty marker must go stale for minutes, never forever.

        The dirty flag is an optimization; it must not become a correctness
        dependency (honest_failure_modes #9 — no permanent silent blindness).
        """
        t, _, _ = tracker
        clock = [1000.0]
        with patch.object(UnifiedNodeTracker, '_save_cache') as save, \
             patch('src.gateway.node_tracker.time.monotonic', side_effect=lambda: clock[0]):
            t._cache_dirty = False
            t._last_cache_save = clock[0]
            clock[0] += UnifiedNodeTracker.CACHE_MAX_STALENESS + 1
            t._maybe_save_cache()
        assert save.call_count == 1, "clean cache can never be refreshed — permanently stale"

    # --- the dirty markers themselves ---------------------------------------

    def test_add_node_marks_dirty(self, tracker):
        t, _, _ = tracker
        t._cache_dirty = False
        t.add_node(UnifiedNode(id="n2", network="rns", name="Two"))
        assert t._cache_dirty is True

    def test_merge_of_existing_node_marks_dirty(self, tracker):
        t, _, _ = tracker
        t._cache_dirty = False
        t.add_node(UnifiedNode(id="n1", network="meshcore", name="One-updated"))
        assert t._cache_dirty is True

    def test_remove_node_marks_dirty(self, tracker):
        t, _, _ = tracker
        t._cache_dirty = False
        t.remove_node("n1")
        assert t._cache_dirty is True

    def test_eviction_marks_dirty(self, tracker):
        t, _, _ = tracker
        t._cache_dirty = False
        with t._lock:
            t._evict_stale_nodes()
        assert t._cache_dirty is True

    def test_timeout_state_change_marks_dirty(self, tracker):
        """The loop's own mutation is a mutation — it must mark dirty too."""
        t, _, _ = tracker
        node = t.get_node("n1")
        if node._state_machine is None:
            pytest.skip("state machine unavailable in this build")
        # A node must be HEARD before it can time out: a fresh node sits in
        # STALE_CACHE, which is_active() excludes, so check_timeout is a no-op
        # until something responds. Drive the real path.
        node.update_seen()
        node.last_seen = datetime.now() - timedelta(hours=6)
        t._cache_dirty = False
        with t._lock:
            changed = node.check_timeout()
            if changed:
                t._mark_cache_dirty()
        assert changed is True and t._cache_dirty is True

    # --- failure + clock honesty --------------------------------------------

    def test_failed_write_keeps_dirty_for_retry(self, tracker):
        """A save that raised did not happen — the gate must not clear."""
        t, _, _ = tracker
        t._mark_cache_dirty()
        before = t._last_cache_save
        with patch('utils.paths.atomic_write_text', side_effect=OSError("disk full")):
            t._save_cache()   # swallowed + logged by design
        assert t._cache_dirty is True, "failed write cleared the dirty gate"
        assert t._last_cache_save == before, "failed write advanced the cadence clock"

    def test_mutation_racing_the_write_is_not_lost(self, tracker):
        """A node added mid-serialization must still be dirty afterwards.

        The gate is cleared against the SNAPSHOT, under the lock that guards
        it. If it were cleared after the write instead, a mutation landing
        between snapshot and clear would be marked dirty, then un-marked by a
        write that never contained it — invisible until CACHE_MAX_STALENESS.
        """
        t, _, _ = tracker
        import src.gateway.node_tracker as nt
        real_dumps = nt.json.dumps

        def racing_dumps(obj, *a, **kw):
            # Simulate a concurrent announce arriving while we serialize.
            if not getattr(t, "_raced", False):
                t._raced = True
                t.add_node(UnifiedNode(id="racer", network="rns", name="Racer"))
            return real_dumps(obj, *a, **kw)

        with patch.object(nt.json, 'dumps', side_effect=racing_dumps):
            t._save_cache()

        assert t._cache_dirty is True, "the racing mutation was silently dropped"

    def test_cadence_uses_monotonic_not_wallclock(self, tracker):
        """RTC-less Pis + NTP steps forge wall-clock durations.

        A wall clock that jumps a day backwards must not stall the cache.
        """
        t, _, _ = tracker
        clock = [1000.0]
        with patch.object(UnifiedNodeTracker, '_save_cache') as save, \
             patch('src.gateway.node_tracker.time.monotonic', side_effect=lambda: clock[0]), \
             patch('src.gateway.node_tracker.time.time', return_value=0.0):
            t._mark_cache_dirty()
            t._last_cache_save = clock[0]
            clock[0] += UnifiedNodeTracker.CACHE_SAVE_INTERVAL + 1
            t._maybe_save_cache()
        assert save.call_count == 1, "cadence is driven by the forgeable wall clock"

    def test_cadence_stays_inside_the_map_collector_freshness_window(self):
        """Two consumers of one artifact must not drift (honest_failure_modes #5).

        Slowing the writer is only safe while every READER still considers the
        file fresh. The map collector rejects node_cache.json once it is older
        than node_cache_max_age_hours. Import the real constant — a future
        cadence bump must fail HERE, not silently blind the map in the field.
        """
        from src.utils.map_data_collector import MapDataCollector

        worst_case = UnifiedNodeTracker.CACHE_MAX_STALENESS
        reader_window = MapDataCollector.DEFAULT_NODE_CACHE_MAX_AGE_HOURS * 3600
        assert UnifiedNodeTracker.CACHE_SAVE_INTERVAL <= worst_case
        assert worst_case * 2 < reader_window, (
            f"worst-case cache age {worst_case}s leaves under 2x margin on the "
            f"{reader_window}s map-collector freshness window"
        )

    def test_stop_flushes_unconditionally(self, tracker):
        """Shutdown must not lose up to CACHE_SAVE_INTERVAL of state."""
        t, cache_file, _ = tracker
        t._cache_dirty = False
        t._last_cache_save = time.monotonic()
        cache_file.unlink(missing_ok=True)
        t.stop(timeout=0.1)
        assert cache_file.exists(), "stop() did not flush the cache"


class TestPopulationRetention20260803:
    """The tracker held the whole reachable Reticulum announce space.

    Measured on meshanchor-server 2026-08-03: 9,051 RNS nodes resident for
    FIVE local MeshCore radios. The population grows with the NETWORK, not
    with this box's workload, and every node is serialized into both cache
    files on every save. Age distribution: 2.1% heard inside a day, 11.7%
    inside a week, 37.1% inside 30 days.

    Retention tiers are IMPORTED from the node-directory retention rather
    than re-declared, so the two consumers of "how long is a node
    interesting" cannot drift (honest_failure_modes #5).
    """

    @pytest.fixture
    def tracker(self, tmp_path):
        with patch.object(UnifiedNodeTracker, '_load_cache'):
            yield UnifiedNodeTracker()

    @staticmethod
    def _node(nid, network="rns", age_days=0.0, rns_hash=None, **kw):
        n = UnifiedNode(id=nid, network=network, name=nid, **kw)
        n.last_seen = datetime.now() - timedelta(days=age_days)
        if rns_hash:
            n.rns_hash = bytes.fromhex(rns_hash)
        return n

    # --- the reader/writer pair must fail together (hfm #4) --------------

    def test_inert_until_pins_are_wired(self, tracker):
        """Never evict while we have not been told what is pinned.

        The pin list comes from gateway.json via the bridge. If that wiring is
        missing, the safe degradation is the OLD behaviour (keep everything) —
        never 'evict with an empty pin set', which would silently drop the
        configured propagation node and turn lxmf_propagation_node_dark into a
        false UNHEARD page (the 2026-07-21 class).
        """
        tracker.add_node(self._node("rns_old", age_days=99))
        assert tracker._retention_pins is None
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_old") is not None, "evicted with pins unwired"

    # --- the tiers --------------------------------------------------------

    def test_tiers_are_imported_not_redeclared(self):
        from src.utils.node_history import (
            DEFAULT_DIRECTORY_RETENTION_LOCAL,
            DEFAULT_DIRECTORY_RETENTION_EXTERNAL,
        )
        assert UnifiedNodeTracker.RETENTION_LOCAL == DEFAULT_DIRECTORY_RETENTION_LOCAL
        assert UnifiedNodeTracker.RETENTION_EXTERNAL == DEFAULT_DIRECTORY_RETENTION_EXTERNAL

    def test_cold_rns_node_evicted_at_external_tier(self, tracker):
        tracker.set_retention_pins([])
        tracker.add_node(self._node("rns_cold", age_days=30))
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_cold") is None

    def test_warm_rns_node_survives(self, tracker):
        tracker.set_retention_pins([])
        tracker.add_node(self._node("rns_warm", age_days=2))
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_warm") is not None

    def test_meshcore_node_gets_the_longer_local_tier(self, tracker):
        """THE MeshAnchor-specific half of this port.

        MeshForge's local radio is Meshtastic; MeshAnchor's is MeshCore.
        Copying MeshForge's ("meshtastic", "both") tuple verbatim would have
        put this box's own five radios on the 7-day announce-firehose tier and
        evicted them for going quiet over a long weekend.
        """
        tracker.set_retention_pins([])
        tracker.add_node(self._node("mc_10d", network="meshcore", age_days=10))
        tracker._evict_expired_nodes()
        assert tracker.get_node("mc_10d") is not None, (
            "a local MeshCore node was evicted on the external (7d) tier"
        )

    def test_meshtastic_node_also_gets_the_local_tier(self, tracker):
        """MeshAnchor still parses Meshtastic nodes; they are local RF too."""
        tracker.set_retention_pins([])
        tracker.add_node(self._node("mesh_10d", network="meshtastic", age_days=10))
        tracker._evict_expired_nodes()
        assert tracker.get_node("mesh_10d") is not None

    def test_unknown_age_is_held_not_evicted(self, tracker):
        """last_seen=None means we cannot observe age — absence is not staleness
        (honest_failure_modes #2)."""
        tracker.set_retention_pins([])
        n = self._node("rns_unknown", age_days=0)
        n.last_seen = None
        tracker.add_node(n)
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_unknown") is not None

    # --- the pins that keep probes honest --------------------------------

    def test_pinned_propagation_node_is_never_evicted(self, tracker):
        """THE constraint. lxmf_propagation_node_dark reports STALE when the
        configured node is in the cache and UNHEARD (= wrong/truncated hash)
        when it is absent. Evicting a quiet propagation node would manufacture
        a false 'wrong hash' diagnosis."""
        h = "3968a2eeac25e2e7a7961f25842d3d85"
        tracker.set_retention_pins([h])
        tracker.add_node(self._node("rns_prop", age_days=99, rns_hash=h))
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_prop") is not None, (
            "configured propagation node evicted -> probe flips STALE to UNHEARD"
        )

    def test_pins_match_case_insensitively(self, tracker):
        h = "3968A2EEAC25E2E7A7961F25842D3D85"
        tracker.set_retention_pins([h])
        tracker.add_node(self._node("rns_prop", age_days=99, rns_hash=h.lower()))
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_prop") is not None

    def test_gateway_and_local_flags_are_never_evicted(self, tracker):
        tracker.set_retention_pins([])
        tracker.add_node(self._node("rns_gw", age_days=99, is_gateway=True))
        tracker.add_node(self._node("rns_local", age_days=99, is_local=True))
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_gw") is not None
        assert tracker.get_node("rns_local") is not None

    # --- integration with the write path ---------------------------------

    def test_eviction_marks_cache_dirty(self, tracker):
        tracker.set_retention_pins([])
        tracker.add_node(self._node("rns_cold", age_days=30))
        tracker._cache_dirty = False
        tracker._evict_expired_nodes()
        assert tracker._cache_dirty is True

    def test_no_eviction_leaves_cache_clean(self, tracker):
        """A no-op sweep must not force a 20 MB write every tick."""
        tracker.set_retention_pins([])
        tracker.add_node(self._node("rns_warm", age_days=1))
        tracker._cache_dirty = False
        tracker._evict_expired_nodes()
        assert tracker._cache_dirty is False

    def test_population_shrinks_to_the_warm_set(self, tracker):
        """End to end on a meshanchor-server-shaped population."""
        tracker.set_retention_pins([])
        for i in range(50):
            tracker.add_node(self._node(f"cold{i}", age_days=30))
        for i in range(5):
            tracker.add_node(self._node(f"warm{i}", age_days=1))
        for i in range(5):
            tracker.add_node(self._node(f"mc{i}", network="meshcore", age_days=10))
        tracker._evict_expired_nodes()
        remaining = {n.id for n in tracker.get_all_nodes()}
        assert len(remaining) == 10, sorted(remaining)
        assert not any(r.startswith("cold") for r in remaining)

    # --- the dead purge this replaced ------------------------------------

    def test_no_second_eviction_policy_survives(self, tracker):
        """STALE_PURGE_THRESHOLD is gone, and must not come back.

        It purged at 24 h but sat under `elif node._state_machine is None`,
        and every node gets a state machine when NODE_STATE_AVAILABLE — True
        on this fleet. It had therefore never evicted anything, which is how
        9,051 RNS nodes accumulated under a nominal 24-hour purge. Two
        eviction policies, one of them dead, is worse than one that runs.
        """
        assert not hasattr(UnifiedNodeTracker, "STALE_PURGE_THRESHOLD")

    def test_eviction_reaches_nodes_that_have_a_state_machine(self, tracker):
        """The bug in one line: the dead purge could not see these nodes.

        A node with a live state machine took the `if` branch and never
        reached the purge in the `elif`. The TTL sweep runs outside that
        branch entirely, so it must evict a cold node that HAS one.
        """
        tracker.set_retention_pins([])
        n = self._node("rns_cold_sm", age_days=30)
        n.update_seen()                                   # materialises the state machine
        n.last_seen = datetime.now() - timedelta(days=30)  # ...then age it back
        tracker.add_node(n)
        assert tracker.get_node("rns_cold_sm")._state_machine is not None, (
            "precondition: this test is meaningless without a state machine"
        )
        tracker._evict_expired_nodes()
        assert tracker.get_node("rns_cold_sm") is None

    # --- the two-writer shutdown hazard ----------------------------------

    def test_stop_sweeps_before_the_final_flush(self, tracker):
        """The daemon's singleton tracker only ever writes in stop().

        NodeTrackerService holds a UnifiedNodeTracker it never start()s, so
        its cleanup loop — the only other caller of _evict_expired_nodes —
        never runs. Because services stop in reverse registration order it
        also writes LAST. Without a sweep here it would hand the full
        announce-space population it loaded at startup back to disk, undoing
        the bridge tracker's TTL work on every clean shutdown
        (honest_failure_modes #8, two writers of one artifact).
        """
        tracker.set_retention_pins([])
        tracker.add_node(self._node("rns_cold", age_days=30))
        tracker.add_node(self._node("rns_warm", age_days=1))
        written = {}

        def capture():
            written["ids"] = {n.id for n in tracker.get_all_nodes()}

        with patch.object(tracker, '_save_cache', side_effect=capture):
            tracker.stop(timeout=0.1)
        assert written["ids"] == {"rns_warm"}, (
            f"stop() flushed an unswept population: {written.get('ids')}"
        )

    def test_stop_sweep_is_inert_when_retention_unwired(self, tracker):
        """An unwired tracker must still flush — just without evicting."""
        tracker.add_node(self._node("rns_old", age_days=99))
        written = {}
        with patch.object(tracker, '_save_cache',
                          side_effect=lambda: written.update(
                              ids={n.id for n in tracker.get_all_nodes()})):
            tracker.stop(timeout=0.1)
        assert written["ids"] == {"rns_old"}
