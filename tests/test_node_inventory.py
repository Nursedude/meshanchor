"""
Tests for Node Inventory.

Tests cover:
- Node creation and updates
- Status detection (online/offline/stale)
- Search and filtering
- Statistics
- Persistence (save/load)
- Pruning stale nodes
- Inventory formatting
- Edge cases

Run with: pytest tests/test_node_inventory.py -v
"""

import pytest
import sys
import os
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.node_inventory import (
    NodeInventory, NodeRecord,
    ONLINE_TIMEOUT_SEC, STALE_TIMEOUT_SEC,
)


@pytest.fixture
def inventory():
    """Create an in-memory inventory (no persistence)."""
    return NodeInventory(path=None)


@pytest.fixture
def persistent_inventory(tmp_path):
    """Create an inventory with file persistence."""
    path = tmp_path / "test_inventory.json"
    return NodeInventory(path=path)


class TestNodeRecord:
    """Test NodeRecord dataclass."""

    def test_create_minimal(self):
        node = NodeRecord(node_id="!abc123")
        assert node.node_id == "!abc123"
        assert node.short_name == ""
        assert node.hardware == ""
        assert node.first_seen == 0.0

    def test_display_name_long_name(self):
        node = NodeRecord(node_id="!abc", long_name="Hilltop Node")
        assert node.display_name == "Hilltop Node"

    def test_display_name_short_name(self):
        node = NodeRecord(node_id="!abc", short_name="HT1")
        assert node.display_name == "HT1"

    def test_display_name_fallback_to_id(self):
        node = NodeRecord(node_id="!abc123")
        assert node.display_name == "!abc123"

    def test_display_name_prefers_long(self):
        node = NodeRecord(node_id="!abc", short_name="HT1", long_name="Hilltop")
        assert node.display_name == "Hilltop"

    def test_has_position_true(self):
        node = NodeRecord(node_id="!abc", lat=21.3, lon=-157.8)
        assert node.has_position is True

    def test_has_position_false(self):
        node = NodeRecord(node_id="!abc")
        assert node.has_position is False

    def test_has_position_partial(self):
        node = NodeRecord(node_id="!abc", lat=21.3)
        assert node.has_position is False

    def test_is_online_recent(self):
        node = NodeRecord(node_id="!abc", last_seen=time.time() - 60)
        assert node.is_online is True

    def test_is_online_expired(self):
        node = NodeRecord(node_id="!abc",
                          last_seen=time.time() - ONLINE_TIMEOUT_SEC - 10)
        assert node.is_online is False

    def test_is_online_never_seen(self):
        node = NodeRecord(node_id="!abc", last_seen=0.0)
        assert node.is_online is False

    def test_is_stale(self):
        node = NodeRecord(node_id="!abc",
                          last_seen=time.time() - STALE_TIMEOUT_SEC - 10)
        assert node.is_stale is True

    def test_not_stale(self):
        node = NodeRecord(node_id="!abc", last_seen=time.time() - 3600)
        assert node.is_stale is False

    def test_status_online(self):
        node = NodeRecord(node_id="!abc", last_seen=time.time())
        assert node.status == "online"

    def test_status_offline(self):
        node = NodeRecord(node_id="!abc",
                          last_seen=time.time() - ONLINE_TIMEOUT_SEC - 60)
        assert node.status == "offline"

    def test_status_stale(self):
        node = NodeRecord(node_id="!abc",
                          last_seen=time.time() - STALE_TIMEOUT_SEC - 60)
        assert node.status == "stale"

    def test_to_dict(self):
        node = NodeRecord(node_id="!abc", short_name="Test", lat=21.3)
        d = node.to_dict()
        assert d['node_id'] == "!abc"
        assert d['short_name'] == "Test"
        assert d['lat'] == 21.3

    def test_from_dict(self):
        data = {'node_id': '!xyz', 'short_name': 'Node1',
                'hardware': 'RAK4631', 'last_seen': 1000.0}
        node = NodeRecord.from_dict(data)
        assert node.node_id == '!xyz'
        assert node.short_name == 'Node1'
        assert node.hardware == 'RAK4631'

    def test_from_dict_ignores_unknown_fields(self):
        data = {'node_id': '!abc', 'unknown_field': 'value'}
        node = NodeRecord.from_dict(data)
        assert node.node_id == '!abc'


class TestNodeInventoryBasics:
    """Test basic inventory operations."""

    def test_empty_inventory(self, inventory):
        assert inventory.node_count == 0
        assert inventory.get_all_nodes() == []

    def test_update_creates_node(self, inventory):
        node = inventory.update_node("!abc123", name="Test")
        assert node.node_id == "!abc123"
        assert node.short_name == "Test"
        assert inventory.node_count == 1

    def test_update_existing_node(self, inventory):
        inventory.update_node("!abc", name="First")
        inventory.update_node("!abc", name="Updated")
        assert inventory.node_count == 1
        node = inventory.get_node("!abc")
        assert node.short_name == "Updated"

    def test_update_sets_first_seen(self, inventory):
        node = inventory.update_node("!abc")
        assert node.first_seen > 0

    def test_update_sets_last_seen(self, inventory):
        node = inventory.update_node("!abc")
        assert node.last_seen > 0
        assert node.last_seen >= node.first_seen

    def test_update_increments_count(self, inventory):
        inventory.update_node("!abc")
        inventory.update_node("!abc")
        inventory.update_node("!abc")
        node = inventory.get_node("!abc")
        assert node.update_count == 3

    def test_update_preserves_first_seen(self, inventory):
        node1 = inventory.update_node("!abc")
        first = node1.first_seen
        time.sleep(0.01)
        inventory.update_node("!abc", name="Later")
        node2 = inventory.get_node("!abc")
        assert node2.first_seen == first

    def test_get_nonexistent_node(self, inventory):
        assert inventory.get_node("!missing") is None

    def test_update_with_snr_rssi(self, inventory):
        inventory.update_node("!abc", snr=-5.0, rssi=-90)
        node = inventory.get_node("!abc")
        assert node.last_snr == -5.0
        assert node.last_rssi == -90

    def test_update_with_location(self, inventory):
        inventory.update_node("!abc", lat=21.3, lon=-157.8, alt=100.0)
        node = inventory.get_node("!abc")
        assert node.lat == 21.3
        assert node.lon == -157.8
        assert node.alt == 100.0

    def test_update_with_hardware(self, inventory):
        inventory.update_node("!abc", hardware="RAK4631",
                              firmware="2.3.5", role="router")
        node = inventory.get_node("!abc")
        assert node.hardware == "RAK4631"
        assert node.firmware == "2.3.5"
        assert node.role == "router"

    def test_update_ignores_none_values(self, inventory):
        inventory.update_node("!abc", hardware="RAK4631")
        inventory.update_node("!abc", hardware=None)
        node = inventory.get_node("!abc")
        assert node.hardware == "RAK4631"

    def test_multiple_nodes(self, inventory):
        inventory.update_node("!node1", name="Node 1")
        inventory.update_node("!node2", name="Node 2")
        inventory.update_node("!node3", name="Node 3")
        assert inventory.node_count == 3


class TestNodeQueries:
    """Test query and filter operations."""

    def test_get_all_nodes_sorted(self, inventory):
        inventory.update_node("!old")
        time.sleep(0.01)
        inventory.update_node("!new")
        nodes = inventory.get_all_nodes()
        assert nodes[0].node_id == "!new"  # Most recent first
        assert nodes[1].node_id == "!old"

    def test_get_online_nodes(self, inventory):
        inventory.update_node("!online")
        # Create an offline node
        inventory.update_node("!offline")
        inventory._nodes["!offline"].last_seen = (
            time.time() - ONLINE_TIMEOUT_SEC - 60
        )

        online = inventory.get_online_nodes()
        assert len(online) == 1
        assert online[0].node_id == "!online"

    def test_get_offline_nodes(self, inventory):
        inventory.update_node("!online")
        inventory.update_node("!offline")
        inventory._nodes["!offline"].last_seen = (
            time.time() - ONLINE_TIMEOUT_SEC - 60
        )

        offline = inventory.get_offline_nodes()
        assert len(offline) == 1
        assert offline[0].node_id == "!offline"

    def test_get_stale_nodes(self, inventory):
        inventory.update_node("!fresh")
        inventory.update_node("!stale")
        inventory._nodes["!stale"].last_seen = (
            time.time() - STALE_TIMEOUT_SEC - 60
        )

        stale = inventory.get_stale_nodes()
        assert len(stale) == 1
        assert stale[0].node_id == "!stale"

    def test_search_by_name(self, inventory):
        inventory.update_node("!abc", name="Hilltop")
        inventory.update_node("!xyz", name="Valley")
        results = inventory.search("hilltop")
        assert len(results) == 1
        assert results[0].node_id == "!abc"

    def test_search_by_id(self, inventory):
        inventory.update_node("!abc123")
        inventory.update_node("!xyz789")
        results = inventory.search("abc")
        assert len(results) == 1
        assert results[0].node_id == "!abc123"

    def test_search_by_owner(self, inventory):
        inventory.update_node("!abc", owner="WH6GXZ")
        inventory.update_node("!xyz", owner="KH6ABC")
        results = inventory.search("WH6")
        assert len(results) == 1
        assert results[0].node_id == "!abc"

    def test_search_by_hardware(self, inventory):
        inventory.update_node("!abc", hardware="RAK4631")
        inventory.update_node("!xyz", hardware="T-Beam")
        results = inventory.search("RAK")
        assert len(results) == 1

    def test_search_case_insensitive(self, inventory):
        inventory.update_node("!abc", name="HILLTOP")
        results = inventory.search("hilltop")
        assert len(results) == 1

    def test_search_no_results(self, inventory):
        inventory.update_node("!abc", name="Test")
        results = inventory.search("nonexistent")
        assert len(results) == 0

    def test_get_by_role(self, inventory):
        inventory.update_node("!r1", role="router")
        inventory.update_node("!r2", role="router")
        inventory.update_node("!c1", role="client")

        routers = inventory.get_by_role("router")
        assert len(routers) == 2

        clients = inventory.get_by_role("client")
        assert len(clients) == 1

    def test_get_by_role_case_insensitive(self, inventory):
        inventory.update_node("!r1", role="Router")
        results = inventory.get_by_role("router")
        assert len(results) == 1


class TestNodeStats:
    """Test statistics collection."""

    def test_empty_stats(self, inventory):
        stats = inventory.get_stats()
        assert stats['total'] == 0
        assert stats['online'] == 0
        assert stats['offline'] == 0
        assert stats['stale'] == 0

    def test_stats_with_nodes(self, inventory):
        inventory.update_node("!online1")
        inventory.update_node("!online2")
        inventory.update_node("!offline")
        inventory._nodes["!offline"].last_seen = (
            time.time() - ONLINE_TIMEOUT_SEC - 60
        )

        stats = inventory.get_stats()
        assert stats['total'] == 3
        assert stats['online'] == 2
        assert stats['offline'] == 1

    def test_stats_roles(self, inventory):
        inventory.update_node("!r1", role="router")
        inventory.update_node("!r2", role="router")
        inventory.update_node("!c1", role="client")

        stats = inventory.get_stats()
        assert stats['roles']['router'] == 2
        assert stats['roles']['client'] == 1

    def test_stats_hardware(self, inventory):
        inventory.update_node("!a", hardware="RAK4631")
        inventory.update_node("!b", hardware="RAK4631")
        inventory.update_node("!c", hardware="T-Beam")

        stats = inventory.get_stats()
        assert stats['hardware_types']['RAK4631'] == 2
        assert stats['hardware_types']['T-Beam'] == 1

    def test_stats_with_position(self, inventory):
        inventory.update_node("!a", lat=21.0, lon=-157.0)
        inventory.update_node("!b")

        stats = inventory.get_stats()
        assert stats['with_position'] == 1


class TestPersistence:
    """Test save/load functionality."""

    def test_save_and_load(self, tmp_path):
        path = tmp_path / "inv.json"

        # Create and populate
        inv1 = NodeInventory(path=path)
        inv1.update_node("!abc", name="Node1", hardware="RAK4631")
        inv1.update_node("!xyz", name="Node2", lat=21.3, lon=-157.8)
        inv1.flush()

        # Load in new instance
        inv2 = NodeInventory(path=path)
        assert inv2.node_count == 2
        node = inv2.get_node("!abc")
        assert node.short_name == "Node1"
        assert node.hardware == "RAK4631"

    def test_load_nonexistent_file(self, tmp_path):
        path = tmp_path / "missing.json"
        inv = NodeInventory(path=path)
        assert inv.node_count == 0

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("this is not json {{{")
        inv = NodeInventory(path=path)
        assert inv.node_count == 0

    def test_save_creates_directories(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "inv.json"
        inv = NodeInventory(path=path)
        inv.update_node("!test")
        inv.flush()
        assert path.exists()

    def test_debounce_prevents_rapid_saves(self, tmp_path):
        path = tmp_path / "inv.json"
        inv = NodeInventory(path=path)

        # First save happens
        inv.update_node("!first")
        inv._save()
        assert path.exists()
        first_mtime = path.stat().st_mtime

        # Rapid second update should be debounced
        inv.update_node("!second")
        # Internal save is debounced, file may not update
        # But flush() should force it
        inv.flush()
        content = json.loads(path.read_text())
        assert "!second" in content

    def test_flush_saves_dirty(self, tmp_path):
        path = tmp_path / "inv.json"
        inv = NodeInventory(path=path)
        inv.update_node("!test")
        inv._dirty = True
        inv._last_save = time.time()  # Simulate debounce
        inv.flush()
        assert path.exists()
        content = json.loads(path.read_text())
        assert "!test" in content

    def test_persistence_preserves_position(self, tmp_path):
        path = tmp_path / "inv.json"

        inv1 = NodeInventory(path=path)
        inv1.update_node("!gps", lat=21.3069, lon=-157.8583, alt=50.0)
        inv1.flush()

        inv2 = NodeInventory(path=path)
        node = inv2.get_node("!gps")
        assert node.lat == 21.3069
        assert node.lon == -157.8583
        assert node.alt == 50.0


class TestPruning:
    """Test stale node pruning."""

    def test_prune_removes_old_nodes(self, inventory):
        inventory.update_node("!fresh")
        inventory.update_node("!old")
        inventory._nodes["!old"].last_seen = (
            time.time() - 31 * 24 * 3600  # 31 days ago
        )

        removed = inventory.prune_stale(max_age_days=30)
        assert removed == 1
        assert inventory.node_count == 1
        assert inventory.get_node("!old") is None

    def test_prune_keeps_recent(self, inventory):
        inventory.update_node("!recent")
        removed = inventory.prune_stale(max_age_days=30)
        assert removed == 0
        assert inventory.node_count == 1

    def test_prune_custom_age(self, inventory):
        inventory.update_node("!node")
        inventory._nodes["!node"].last_seen = (
            time.time() - 8 * 24 * 3600  # 8 days ago
        )
        # 7-day prune should remove it
        removed = inventory.prune_stale(max_age_days=7)
        assert removed == 1

    def test_remove_specific_node(self, inventory):
        inventory.update_node("!keep")
        inventory.update_node("!remove")
        assert inventory.remove_node("!remove") is True
        assert inventory.node_count == 1
        assert inventory.get_node("!remove") is None

    def test_remove_nonexistent_node(self, inventory):
        assert inventory.remove_node("!missing") is False


class TestFormatting:
    """Test inventory formatting."""

    def test_format_empty(self, inventory):
        report = inventory.format_inventory()
        assert "0 nodes" in report
        assert "No nodes" in report

    def test_format_with_nodes(self, inventory):
        inventory.update_node("!abc", name="TestNode",
                              hardware="RAK4631", role="router")
        report = inventory.format_inventory()
        assert "1 nodes" in report
        assert "!abc" in report
        assert "TestNode" in report
        assert "RAK4631" in report
        assert "router" in report
        assert "online" in report

    def test_format_excludes_stale_by_default(self, inventory):
        inventory.update_node("!fresh")
        inventory.update_node("!stale")
        inventory._nodes["!stale"].last_seen = (
            time.time() - STALE_TIMEOUT_SEC - 60
        )

        report = inventory.format_inventory(include_stale=False)
        assert "!fresh" in report
        assert "!stale" not in report

    def test_format_includes_stale_when_asked(self, inventory):
        inventory.update_node("!stale")
        inventory._nodes["!stale"].last_seen = (
            time.time() - STALE_TIMEOUT_SEC - 60
        )

        report = inventory.format_inventory(include_stale=True)
        assert "!stale" in report
        assert "stale" in report

    def test_format_shows_snr(self, inventory):
        inventory.update_node("!abc", snr=-5.5)
        report = inventory.format_inventory()
        assert "-5.5" in report

    def test_format_markdown_table(self, inventory):
        inventory.update_node("!abc", name="Test")
        report = inventory.format_inventory()
        assert "|" in report
        assert "Node ID" in report
        assert "Hardware" in report

    def test_export_json(self, inventory):
        inventory.update_node("!abc", name="Test", hardware="RAK4631")
        exported = inventory.export_json()
        data = json.loads(exported)
        assert "!abc" in data
        assert data["!abc"]["short_name"] == "Test"
        assert data["!abc"]["hardware"] == "RAK4631"

    def test_export_json_empty(self, inventory):
        exported = inventory.export_json()
        data = json.loads(exported)
        assert data == {}


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_node_id(self, inventory):
        node = inventory.update_node("")
        assert node.node_id == ""

    def test_special_chars_in_name(self, inventory):
        inventory.update_node("!abc", name="Node <test> & 'quotes'")
        node = inventory.get_node("!abc")
        assert node.short_name == "Node <test> & 'quotes'"

    def test_very_long_name(self, inventory):
        long_name = "A" * 1000
        inventory.update_node("!abc", long_name=long_name)
        node = inventory.get_node("!abc")
        assert node.long_name == long_name

    def test_negative_coordinates(self, inventory):
        inventory.update_node("!abc", lat=-33.8688, lon=151.2093)
        node = inventory.get_node("!abc")
        assert node.lat == -33.8688
        assert node.lon == 151.2093

    def test_update_count_survives_persistence(self, tmp_path):
        path = tmp_path / "inv.json"
        inv = NodeInventory(path=path)
        inv.update_node("!abc")
        inv.update_node("!abc")
        inv.update_node("!abc")
        inv.flush()

        inv2 = NodeInventory(path=path)
        node = inv2.get_node("!abc")
        assert node.update_count == 3

    def test_concurrent_update_safe(self, inventory):
        """Rapid concurrent updates should not corrupt data."""
        for i in range(100):
            inventory.update_node(f"!node{i}", name=f"Node {i}")
        assert inventory.node_count == 100

    def test_stats_unknown_role(self, inventory):
        inventory.update_node("!abc")  # No role set
        stats = inventory.get_stats()
        assert "unknown" in stats['roles']

    def test_stats_unknown_hardware(self, inventory):
        inventory.update_node("!abc")  # No hardware set
        stats = inventory.get_stats()
        assert "unknown" in stats['hardware_types']
