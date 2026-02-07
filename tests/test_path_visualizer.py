"""Tests for PathVisualizer reliability - empty sequence guards."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest


class TestPathVisualizerStats:
    """Test get_path_stats with edge cases."""

    def test_empty_stats_no_crash(self):
        """Stats on empty visualizer returns consistent dict (not empty {})."""
        from monitoring.path_visualizer import PathVisualizer

        viz = PathVisualizer()
        stats = viz.get_path_stats()

        assert stats["total_paths"] == 0
        assert stats["success_rate"] == 0
        assert stats["avg_hops"] == 0
        assert stats["max_hops"] == 0
        assert stats["avg_snr"] is None
        assert stats["min_snr"] is None
        assert stats["avg_latency_ms"] is None
        assert stats["unique_nodes"] == 0

    def test_stats_with_single_path(self):
        """Stats work with a single path."""
        from monitoring.path_visualizer import PathVisualizer, TracedPath

        viz = PathVisualizer()
        path = TracedPath(
            path_id="path1",
            source="!src",
            destination="!dst",
            success=True,
            total_hops=1,
            weakest_snr=8.5,
            total_latency_ms=150.0,
        )
        viz._paths.append(path)

        stats = viz.get_path_stats()

        assert stats["total_paths"] == 1
        assert stats["success_rate"] == 1.0
        assert stats["avg_hops"] == 1
        assert stats["max_hops"] == 1
        assert stats["avg_snr"] == 8.5
        assert stats["min_snr"] == 8.5
        assert stats["avg_latency_ms"] == 150.0
