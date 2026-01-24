"""
Tests for Channel Scan / Activity Monitor.

Tests cover:
- ChannelConfig properties
- ChannelActivity tracking and rate calculations
- ChannelMonitor recording and querying
- Device channel query (CLI parsing)
- MQTT topic channel detection
- Activity report formatting
- Statistics and edge cases

Run with: pytest tests/test_channel_scan.py -v
"""

import pytest
import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.channel_scan import (
    ChannelConfig, ChannelActivity, ChannelMonitor,
    MAX_CHANNELS, RECENT_WINDOW_SEC,
    UTILIZATION_LOW, UTILIZATION_MEDIUM, UTILIZATION_HIGH,
)


# =============================================================================
# ChannelConfig Tests
# =============================================================================


class TestChannelConfig:
    """Test channel configuration."""

    def test_creation(self):
        ch = ChannelConfig(index=0, name="LongFast", role="PRIMARY")
        assert ch.index == 0
        assert ch.name == "LongFast"
        assert ch.role == "PRIMARY"

    def test_is_active_primary(self):
        ch = ChannelConfig(index=0, role="PRIMARY")
        assert ch.is_active is True

    def test_is_active_secondary(self):
        ch = ChannelConfig(index=1, role="SECONDARY")
        assert ch.is_active is True

    def test_is_active_disabled(self):
        ch = ChannelConfig(index=2, role="DISABLED")
        assert ch.is_active is False

    def test_is_active_disabled_case(self):
        ch = ChannelConfig(index=2, role="disabled")
        assert ch.is_active is False

    def test_display_name_with_name(self):
        ch = ChannelConfig(index=0, name="LongFast")
        assert ch.display_name == "LongFast"

    def test_display_name_primary_no_name(self):
        ch = ChannelConfig(index=0)
        assert ch.display_name == "Primary"

    def test_display_name_secondary_no_name(self):
        ch = ChannelConfig(index=3)
        assert ch.display_name == "Channel 3"

    def test_has_encryption_true(self):
        ch = ChannelConfig(index=0, psk="abc123==")
        assert ch.has_encryption is True

    def test_has_encryption_false_empty(self):
        ch = ChannelConfig(index=0, psk="")
        assert ch.has_encryption is False

    def test_has_encryption_false_default(self):
        ch = ChannelConfig(index=0, psk="AQ==")
        assert ch.has_encryption is False

    def test_uplink_downlink(self):
        ch = ChannelConfig(index=0, uplink_enabled=True, downlink_enabled=True)
        assert ch.uplink_enabled is True
        assert ch.downlink_enabled is True


# =============================================================================
# ChannelActivity Tests
# =============================================================================


class TestChannelActivity:
    """Test channel activity tracking."""

    def test_creation(self):
        act = ChannelActivity(channel_index=0)
        assert act.message_count == 0
        assert act.first_activity == 0.0

    def test_record_increments_count(self):
        act = ChannelActivity(channel_index=0)
        act.record("text")
        assert act.message_count == 1
        assert act.text_count == 1

    def test_record_types(self):
        act = ChannelActivity(channel_index=0)
        act.record("text")
        act.record("position")
        act.record("telemetry")
        act.record("nodeinfo")
        act.record("other")
        assert act.text_count == 1
        assert act.position_count == 1
        assert act.telemetry_count == 1
        assert act.nodeinfo_count == 1
        assert act.other_count == 1
        assert act.message_count == 5

    def test_record_case_insensitive(self):
        act = ChannelActivity(channel_index=0)
        act.record("TEXT")
        act.record("Position")
        assert act.text_count == 1
        assert act.position_count == 1

    def test_record_unknown_type(self):
        act = ChannelActivity(channel_index=0)
        act.record("unknown_type")
        assert act.other_count == 1

    def test_timestamps_set(self):
        act = ChannelActivity(channel_index=0)
        before = time.time()
        act.record("text")
        assert act.first_activity >= before
        assert act.last_activity >= before

    def test_first_activity_stays(self):
        act = ChannelActivity(channel_index=0)
        act.record("text")
        first = act.first_activity
        time.sleep(0.01)
        act.record("text")
        assert act.first_activity == first

    def test_last_activity_updates(self):
        act = ChannelActivity(channel_index=0)
        act.record("text")
        first_last = act.last_activity
        time.sleep(0.01)
        act.record("text")
        assert act.last_activity > first_last

    def test_messages_per_hour_empty(self):
        act = ChannelActivity(channel_index=0)
        assert act.messages_per_hour == 0.0

    def test_messages_per_hour_with_activity(self):
        act = ChannelActivity(channel_index=0)
        now = time.time()
        # Simulate 10 messages over last 10 minutes (oldest = now - 540s)
        for i in range(10):
            act.timestamps.append(now - (i + 1) * 60)
        act.message_count = 10
        rate = act.messages_per_hour
        # 10 messages in 600s window → 60/hr
        assert 50 < rate < 70

    def test_utilization_level_quiet(self):
        act = ChannelActivity(channel_index=0)
        assert act.utilization_level == "quiet"

    def test_utilization_level_low(self):
        act = ChannelActivity(channel_index=0)
        now = time.time()
        # 5 messages over ~40 min → ~7.5/hr (below UTILIZATION_LOW=10)
        for i in range(5):
            act.timestamps.append(now - (i + 1) * 480)
        assert act.utilization_level == "low"

    def test_utilization_level_medium(self):
        act = ChannelActivity(channel_index=0)
        now = time.time()
        # 20 messages over ~57 min → ~21/hr (between 10-50)
        for i in range(20):
            act.timestamps.append(now - (i + 1) * 170)
        assert act.utilization_level == "medium"

    def test_utilization_level_high(self):
        act = ChannelActivity(channel_index=0)
        now = time.time()
        # 60 messages over ~60 min → ~60/hr (between 50-100)
        for i in range(60):
            act.timestamps.append(now - (i + 1) * 60)
        assert act.utilization_level == "high"

    def test_is_active_recently_true(self):
        act = ChannelActivity(channel_index=0)
        act.last_activity = time.time()
        assert act.is_active_recently is True

    def test_is_active_recently_false(self):
        act = ChannelActivity(channel_index=0)
        act.last_activity = time.time() - RECENT_WINDOW_SEC - 10
        assert act.is_active_recently is False

    def test_is_active_recently_no_activity(self):
        act = ChannelActivity(channel_index=0)
        assert act.is_active_recently is False

    def test_reset(self):
        act = ChannelActivity(channel_index=0)
        act.record("text")
        act.record("position")
        act.reset()
        assert act.message_count == 0
        assert act.text_count == 0
        assert act.first_activity == 0.0
        assert act.timestamps == []

    def test_timestamps_pruned(self):
        act = ChannelActivity(channel_index=0)
        # Add old timestamps
        old_time = time.time() - RECENT_WINDOW_SEC - 100
        act.timestamps = [old_time]
        # Record new — should prune old
        act.record("text")
        assert all(t > (time.time() - RECENT_WINDOW_SEC)
                   for t in act.timestamps)


# =============================================================================
# ChannelMonitor Tests
# =============================================================================


class TestChannelMonitorCreation:
    """Test monitor initialization."""

    def test_creation(self):
        monitor = ChannelMonitor()
        assert len(monitor._activity) == MAX_CHANNELS

    def test_all_channels_start_empty(self):
        monitor = ChannelMonitor()
        for i in range(MAX_CHANNELS):
            assert monitor._activity[i].message_count == 0


class TestChannelMonitorRecording:
    """Test activity recording."""

    def test_record_activity(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        assert monitor._activity[0].message_count == 1

    def test_record_multiple_channels(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        monitor.record_activity(1, "position")
        monitor.record_activity(2, "telemetry")
        assert monitor._activity[0].text_count == 1
        assert monitor._activity[1].position_count == 1
        assert monitor._activity[2].telemetry_count == 1

    def test_record_invalid_channel_low(self):
        monitor = ChannelMonitor()
        monitor.record_activity(-1, "text")  # Should not crash
        assert monitor.get_total_messages() == 0

    def test_record_invalid_channel_high(self):
        monitor = ChannelMonitor()
        monitor.record_activity(MAX_CHANNELS, "text")  # Should not crash
        assert monitor.get_total_messages() == 0

    def test_get_channel_activity(self):
        monitor = ChannelMonitor()
        monitor.record_activity(3, "text")
        act = monitor.get_channel_activity(3)
        assert act is not None
        assert act.message_count == 1

    def test_get_channel_activity_invalid(self):
        monitor = ChannelMonitor()
        assert monitor.get_channel_activity(-1) is None
        assert monitor.get_channel_activity(MAX_CHANNELS) is None

    def test_get_active_channels(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        monitor.record_activity(2, "position")
        active = monitor.get_active_channels()
        assert 0 in active
        assert 2 in active
        assert 1 not in active

    def test_get_total_messages(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        monitor.record_activity(0, "text")
        monitor.record_activity(1, "position")
        assert monitor.get_total_messages() == 3


class TestChannelMonitorConfig:
    """Test channel configuration management."""

    def test_set_channels(self):
        monitor = ChannelMonitor()
        channels = [
            ChannelConfig(index=0, name="LongFast", role="PRIMARY"),
            ChannelConfig(index=1, name="Admin", role="SECONDARY"),
        ]
        monitor.set_channels(channels)
        assert len(monitor.get_channels()) == 2

    def test_get_channels_sorted(self):
        monitor = ChannelMonitor()
        channels = [
            ChannelConfig(index=2, name="C", role="SECONDARY"),
            ChannelConfig(index=0, name="A", role="PRIMARY"),
            ChannelConfig(index=1, name="B", role="SECONDARY"),
        ]
        monitor.set_channels(channels)
        result = monitor.get_channels()
        assert [c.index for c in result] == [0, 1, 2]

    def test_set_channels_replaces(self):
        monitor = ChannelMonitor()
        monitor.set_channels([ChannelConfig(index=0, name="Old")])
        monitor.set_channels([ChannelConfig(index=0, name="New")])
        assert monitor.get_channels()[0].name == "New"


class TestChannelMonitorDeviceQuery:
    """Test device channel querying."""

    @patch('subprocess.run')
    def test_query_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Channels:\n  0: LongFast (PRIMARY)\n  1: Admin (SECONDARY)\n"
        )
        monitor = ChannelMonitor()
        channels = monitor.query_device_channels()
        # Should parse at least one channel
        assert mock_run.called

    @patch('subprocess.run')
    def test_query_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError("meshtastic not found")
        monitor = ChannelMonitor()
        channels = monitor.query_device_channels()
        assert channels == []

    @patch('subprocess.run')
    def test_query_timeout(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=['meshtastic'], timeout=15)
        monitor = ChannelMonitor()
        channels = monitor.query_device_channels()
        assert channels == []

    def test_parse_channel_info_simple(self):
        monitor = ChannelMonitor()
        output = """Channels:
  0: LongFast (PRIMARY)
  1: Admin (SECONDARY)
  2: (DISABLED)
Preferences:
"""
        channels = monitor._parse_channel_info(output)
        assert len(channels) >= 2
        primary = next((c for c in channels if c.index == 0), None)
        if primary:
            assert "PRIMARY" in primary.role.upper()

    def test_parse_channel_info_empty(self):
        monitor = ChannelMonitor()
        channels = monitor._parse_channel_info("")
        assert channels == []


class TestChannelMonitorTopicDetection:
    """Test MQTT topic channel detection."""

    def test_detect_json_topic(self):
        monitor = ChannelMonitor()
        ch = monitor.detect_channel_from_topic("msh/US/json/0/!abc12345")
        assert ch == 0

    def test_detect_encrypted_topic(self):
        monitor = ChannelMonitor()
        ch = monitor.detect_channel_from_topic("msh/US/2/e/0/!abc12345")
        assert ch == 0

    def test_detect_named_channel(self):
        monitor = ChannelMonitor()
        ch = monitor.detect_channel_from_topic("msh/US/json/LongFast/!abc")
        assert ch == 0  # Default to primary for named

    def test_detect_no_match(self):
        monitor = ChannelMonitor()
        ch = monitor.detect_channel_from_topic("random/topic/here")
        assert ch is None


class TestChannelMonitorReport:
    """Test activity report generation."""

    def test_report_empty(self):
        monitor = ChannelMonitor()
        report = monitor.get_activity_report()
        assert "Channel Activity Report" in report
        assert "Active channels: 0" in report

    def test_report_with_activity(self):
        monitor = ChannelMonitor()
        monitor.set_channels([
            ChannelConfig(index=0, name="LongFast", role="PRIMARY"),
        ])
        monitor.record_activity(0, "text")
        monitor.record_activity(0, "position")
        report = monitor.get_activity_report()
        assert "LongFast" in report
        assert "PRIMARY" in report

    def test_report_shows_type_breakdown(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        monitor.record_activity(0, "telemetry")
        report = monitor.get_activity_report()
        assert "text=1" in report
        assert "telemetry=1" in report


class TestChannelMonitorStats:
    """Test statistics."""

    def test_stats_empty(self):
        monitor = ChannelMonitor()
        stats = monitor.get_stats()
        assert stats['total_messages'] == 0
        assert stats['active_channels'] == 0

    def test_stats_with_activity(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        monitor.record_activity(0, "text")
        monitor.record_activity(1, "position")
        stats = monitor.get_stats()
        assert stats['total_messages'] == 3
        assert stats['active_channels'] == 2

    def test_stats_busiest_channel(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        for _ in range(10):
            monitor.record_activity(2, "text")
        stats = monitor.get_stats()
        assert stats['busiest_channel'] == 2

    def test_stats_per_channel(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        stats = monitor.get_stats()
        assert 0 in stats['per_channel']
        assert stats['per_channel'][0]['messages'] == 1


class TestChannelMonitorReset:
    """Test reset functionality."""

    def test_reset_all(self):
        monitor = ChannelMonitor()
        monitor.record_activity(0, "text")
        monitor.record_activity(1, "position")
        monitor.reset_all()
        assert monitor.get_total_messages() == 0
        assert monitor.get_active_channels() == []


class TestChannelMonitorThreadSafety:
    """Test thread safety."""

    def test_concurrent_recording(self):
        import threading
        monitor = ChannelMonitor()
        errors = []

        def record_many(channel):
            try:
                for _ in range(100):
                    monitor.record_activity(channel, "text")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=record_many, args=(i % MAX_CHANNELS,))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert monitor.get_total_messages() == 800
