"""
Tests for topology visualization module.

Tests the TopologyVisualizer class for generating network topology visualizations.
"""

import json
import os
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestTopoNode:
    """Tests for TopoNode dataclass."""

    def test_topo_node_creation(self):
        """Test creating a TopoNode."""
        from utils.topology_visualizer import TopoNode

        node = TopoNode(
            id="test_node",
            name="Test Node",
            node_type="gateway",
            network="rns",
            is_online=True,
            services=["LXMF_DELIVERY"],
            hops=2,
        )

        assert node.id == "test_node"
        assert node.name == "Test Node"
        assert node.node_type == "gateway"
        assert node.network == "rns"
        assert node.is_online is True
        assert "LXMF_DELIVERY" in node.services
        assert node.hops == 2

    def test_topo_node_to_dict(self):
        """Test TopoNode serialization."""
        from utils.topology_visualizer import TopoNode

        node = TopoNode(
            id="node1",
            name="Node One",
            node_type="rns",
            network="rns",
        )
        data = node.to_dict()

        assert data["id"] == "node1"
        assert data["name"] == "Node One"
        assert data["type"] == "rns"
        assert data["network"] == "rns"
        assert data["online"] is True

    def test_topo_node_defaults(self):
        """Test TopoNode default values."""
        from utils.topology_visualizer import TopoNode

        node = TopoNode(id="minimal")

        assert node.name == ""
        assert node.node_type == "node"
        assert node.network == "unknown"
        assert node.is_online is True
        assert node.services == []
        assert node.hops == 0


class TestTopoEdge:
    """Tests for TopoEdge dataclass."""

    def test_topo_edge_creation(self):
        """Test creating a TopoEdge."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(
            source="node1",
            target="node2",
            hops=2,
            snr=8.5,
            rssi=-85,
        )

        assert edge.source == "node1"
        assert edge.target == "node2"
        assert edge.hops == 2
        assert edge.snr == 8.5
        assert edge.rssi == -85

    def test_edge_quality_color_excellent(self):
        """Test edge color for excellent SNR."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(source="a", target="b", snr=15.0)
        assert edge.get_quality_color() == "#22c55e"  # Green
        assert edge.get_quality_label() == "Excellent"

    def test_edge_quality_color_good(self):
        """Test edge color for good SNR."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(source="a", target="b", snr=7.0)
        assert edge.get_quality_color() == "#84cc16"  # Light green
        assert edge.get_quality_label() == "Good"

    def test_edge_quality_color_marginal(self):
        """Test edge color for marginal SNR."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(source="a", target="b", snr=2.0)
        assert edge.get_quality_color() == "#eab308"  # Yellow
        assert edge.get_quality_label() == "Marginal"

    def test_edge_quality_color_poor(self):
        """Test edge color for poor SNR."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(source="a", target="b", snr=-3.0)
        assert edge.get_quality_color() == "#f97316"  # Orange
        assert edge.get_quality_label() == "Poor"

    def test_edge_quality_color_bad(self):
        """Test edge color for bad SNR."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(source="a", target="b", snr=-10.0)
        assert edge.get_quality_color() == "#ef4444"  # Red
        assert edge.get_quality_label() == "Bad"

    def test_edge_quality_color_unknown(self):
        """Test edge color for unknown SNR."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(source="a", target="b", snr=None)
        assert edge.get_quality_color() == "#6b7280"  # Gray
        assert edge.get_quality_label() == "Unknown"

    def test_edge_to_dict(self):
        """Test TopoEdge serialization."""
        from utils.topology_visualizer import TopoEdge

        edge = TopoEdge(
            source="node1",
            target="node2",
            hops=3,
            snr=5.5,
            announce_count=10,
        )
        data = edge.to_dict()

        assert data["source"] == "node1"
        assert data["target"] == "node2"
        assert data["hops"] == 3
        assert data["snr"] == 5.5
        assert data["announce_count"] == 10
        assert data["quality"] == "Good"
        assert data["color"] == "#84cc16"


class TestTopologyVisualizer:
    """Tests for TopologyVisualizer class."""

    def test_visualizer_creation(self):
        """Test creating a TopologyVisualizer."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        assert viz is not None
        assert len(viz._nodes) == 0
        assert len(viz._edges) == 0

    def test_add_node(self):
        """Test adding nodes."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        node = viz.add_node(
            "test1",
            name="Test Node 1",
            node_type="gateway",
            network="rns",
        )

        assert "test1" in viz._nodes
        assert viz._nodes["test1"].name == "Test Node 1"
        assert viz._nodes["test1"].node_type == "gateway"

    def test_add_edge(self):
        """Test adding edges."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        edge = viz.add_edge(
            "node1",
            "node2",
            hops=2,
            snr=8.0,
        )

        assert len(viz._edges) == 1
        assert viz._edges[0].source == "node1"
        assert viz._edges[0].target == "node2"
        assert viz._edges[0].hops == 2

    def test_add_edge_auto_creates_nodes(self):
        """Test that adding an edge auto-creates nodes."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_edge("auto1", "auto2")

        assert "auto1" in viz._nodes
        assert "auto2" in viz._nodes

    def test_get_stats(self):
        """Test getting topology statistics."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_node("local", node_type="local", network="rns")
        viz.add_node("rns_abc123", node_type="rns", network="rns")
        viz.add_node("mesh_def456", node_type="meshtastic", network="meshtastic")

        viz.add_edge("local", "rns_abc123", hops=1)
        viz.add_edge("local", "mesh_def456", hops=2)

        stats = viz.get_stats()

        assert stats["total_nodes"] == 3
        assert stats["total_edges"] == 2
        assert stats["rns_nodes"] == 2  # local + rns_abc123
        assert stats["meshtastic_nodes"] == 1
        assert stats["avg_hops"] == 1.5  # (1 + 2) / 2

    def test_get_stats_empty(self):
        """Stats work with no nodes or edges (regression: max() on empty sequence)."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        stats = viz.get_stats()

        assert stats["total_nodes"] == 0
        assert stats["total_edges"] == 0
        assert stats["avg_hops"] == 0
        assert stats["max_hops"] == 0

    def test_add_event(self):
        """Test adding topology events."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_event("PATH_DISCOVERED", node_id="test_node", details={"hops": 2})

        assert len(viz._events) == 1
        assert viz._events[0]["type"] == "PATH_DISCOVERED"
        assert viz._events[0]["node_id"] == "test_node"

    def test_event_limit(self):
        """Test that events are limited to 100."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        for i in range(150):
            viz.add_event(f"EVENT_{i}")

        assert len(viz._events) == 100
        # Should have the last 100 events
        assert viz._events[0]["type"] == "EVENT_50"

    def test_generate_ascii(self):
        """Test ASCII topology generation."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_node("local", name="Local Node", node_type="local")
        viz.add_node("node1", name="Remote Node 1")
        viz.add_edge("local", "node1", hops=2, snr=8.0)

        ascii_output = viz.generate_ascii()

        assert "NETWORK TOPOLOGY" in ascii_output
        assert "Local Node" in ascii_output
        assert "Nodes:" in ascii_output or "NODES:" in ascii_output

    def test_generate_html(self, tmp_path):
        """Test HTML visualization generation."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_node("local", name="Local", node_type="local")
        viz.add_node("remote", name="Remote", node_type="rns")
        viz.add_edge("local", "remote", hops=1, snr=10.0)

        output_path = str(tmp_path / "test_topology.html")
        result = viz.generate(output_path)

        assert result == output_path
        assert os.path.exists(output_path)

        # Check HTML content
        with open(output_path, 'r') as f:
            html = f.read()

        assert "<!DOCTYPE html>" in html
        assert "d3.js" in html or "d3.v7" in html
        assert "MeshForge" in html
        assert "Local" in html

    def test_generate_default_path(self, monkeypatch):
        """Test HTML generation with default path."""
        from utils.topology_visualizer import TopologyVisualizer
        import tempfile

        # Create a temp directory for the test
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Mock get_real_user_home to return temp directory
            monkeypatch.setattr(
                "utils.topology_visualizer.get_real_user_home",
                lambda: tmp_path
            )

            viz = TopologyVisualizer()
            viz.add_node("test", name="Test")

            result = viz.generate()

            expected_path = tmp_path / ".cache" / "meshforge" / "topology.html"
            assert result == str(expected_path)
            assert expected_path.exists()

    def test_from_topology_mock(self):
        """Test creating visualizer from a mock NetworkTopology."""
        from utils.topology_visualizer import TopologyVisualizer

        # Create a mock topology object
        mock_topology = MagicMock()
        mock_topology.to_dict.return_value = {
            "nodes": ["local", "rns_abc123", "mesh_def456"],
            "edges": [
                {
                    "source_id": "local",
                    "dest_id": "rns_abc123",
                    "hops": 1,
                    "snr": 8.5,
                    "rssi": -75,
                    "is_active": True,
                    "announce_count": 5,
                    "interface": "LoRa",
                    "weight": 2.0,
                },
            ],
            "stats": {
                "node_count": 3,
                "edge_count": 1,
            },
        }
        mock_topology.get_recent_events.return_value = [
            {
                "event_type": "PATH_DISCOVERED",
                "node_id": "rns_abc123",
                "timestamp": "2024-01-01T12:00:00",
            },
        ]

        viz = TopologyVisualizer.from_topology(mock_topology)

        assert "local" in viz._nodes
        assert "rns_abc123" in viz._nodes
        assert len(viz._edges) == 1
        assert viz._edges[0].snr == 8.5


class TestNodeColors:
    """Tests for node color constants."""

    def test_node_colors_defined(self):
        """Test that all expected node colors are defined."""
        from utils.topology_visualizer import TopologyVisualizer

        expected_types = ["local", "gateway", "router", "rns", "meshtastic", "both", "node"]
        for node_type in expected_types:
            assert node_type in TopologyVisualizer.NODE_COLORS
            # Verify it's a valid hex color
            color = TopologyVisualizer.NODE_COLORS[node_type]
            assert color.startswith("#")
            assert len(color) == 7

    def test_node_sizes_defined(self):
        """Test that all expected node sizes are defined."""
        from utils.topology_visualizer import TopologyVisualizer

        expected_types = ["local", "gateway", "router", "rns", "meshtastic", "both", "node"]
        for node_type in expected_types:
            assert node_type in TopologyVisualizer.NODE_SIZES
            # Verify it's a reasonable size
            size = TopologyVisualizer.NODE_SIZES[node_type]
            assert isinstance(size, int)
            assert 5 <= size <= 30


class TestHTMLGeneration:
    """Tests for HTML output quality."""

    def test_html_contains_d3_script(self, tmp_path):
        """Test that generated HTML includes D3.js."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_node("test", name="Test Node")

        output_path = str(tmp_path / "test.html")
        viz.generate(output_path)

        with open(output_path, 'r') as f:
            html = f.read()

        assert "d3.org" in html or "d3.v7" in html

    def test_html_contains_nodes_data(self, tmp_path):
        """Test that generated HTML includes node data."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_node("unique_node_id_12345", name="Unique Test Node")

        output_path = str(tmp_path / "test.html")
        viz.generate(output_path)

        with open(output_path, 'r') as f:
            html = f.read()

        assert "unique_node_id_12345" in html
        assert "Unique Test Node" in html

    def test_html_contains_stats(self, tmp_path):
        """Test that generated HTML includes statistics."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        for i in range(5):
            viz.add_node(f"node{i}")
        for i in range(3):
            viz.add_edge(f"node{i}", f"node{i+1}")

        output_path = str(tmp_path / "test.html")
        viz.generate(output_path)

        with open(output_path, 'r') as f:
            html = f.read()

        # Stats should be embedded in the HTML
        assert "stat-nodes" in html
        assert "stat-edges" in html

    def test_html_escapes_title(self, tmp_path):
        """Test that HTML title is properly escaped."""
        from utils.topology_visualizer import TopologyVisualizer

        viz = TopologyVisualizer()
        viz.add_node("test")

        # Title with HTML special characters
        output_path = str(tmp_path / "test.html")
        viz.generate(output_path, title="Test <script>alert('xss')</script>")

        with open(output_path, 'r') as f:
            html = f.read()

        # Should be escaped, not raw script tag
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html or "alert" not in html
