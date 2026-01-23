"""
Tests for log parsing patterns module.

Tests cover:
- Severity detection from log lines
- Meshtasticd error pattern matching
- RNS/rnsd error pattern matching
- Systemd error pattern matching
- MeshForge application error patterns
- Context line capture
- Multi-line parsing
- Error summary generation
- Source filtering
- Edge cases
- Format report output
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from utils.log_parser import (
    LogParser,
    LogEntry,
    LogPattern,
    LogSeverity,
    LogSource,
    ALL_PATTERNS,
    MESHTASTIC_PATTERNS,
    RNS_PATTERNS,
    SYSTEMD_PATTERNS,
    MESHFORGE_PATTERNS,
    parse_log_lines,
    format_error_report,
)


@pytest.fixture
def parser():
    """Create default log parser."""
    return LogParser()


# =============================================================================
# Severity Detection
# =============================================================================

class TestSeverityDetection:
    def test_detect_error(self, parser):
        """ERROR keyword detected."""
        entry = parser.parse_line("2026-01-23 ERROR: something failed")
        assert entry.severity == LogSeverity.ERROR

    def test_detect_warning(self, parser):
        """WARNING keyword detected."""
        entry = parser.parse_line("WARNING: low battery")
        assert entry.severity == LogSeverity.WARNING

    def test_detect_critical(self, parser):
        """CRITICAL keyword detected."""
        entry = parser.parse_line("CRITICAL: system failure")
        assert entry.severity == LogSeverity.CRITICAL

    def test_detect_info(self, parser):
        """INFO keyword detected."""
        entry = parser.parse_line("INFO: started normally")
        assert entry.severity == LogSeverity.INFO

    def test_detect_debug(self, parser):
        """DEBUG keyword detected."""
        entry = parser.parse_line("DEBUG: trace data")
        assert entry.severity == LogSeverity.DEBUG

    def test_default_info(self, parser):
        """No keyword defaults to INFO."""
        entry = parser.parse_line("just a normal line of output")
        assert entry.severity == LogSeverity.INFO

    def test_case_insensitive(self, parser):
        """Severity detection is case insensitive."""
        entry = parser.parse_line("error: something broke")
        assert entry.severity == LogSeverity.ERROR

    def test_fail_detected_as_error(self, parser):
        """FAILED keyword treated as error."""
        entry = parser.parse_line("Operation FAILED")
        assert entry.severity == LogSeverity.ERROR


# =============================================================================
# Meshtasticd Patterns
# =============================================================================

class TestMeshtasticPatterns:
    def test_serial_disconnect(self, parser):
        """Serial disconnection detected."""
        entry = parser.parse_line(
            "Serial port disconnected unexpectedly",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'serial_connection_lost'
        assert entry.category == 'hardware'
        assert entry.is_error

    def test_serial_port_busy(self, parser):
        """Port busy detected."""
        entry = parser.parse_line(
            "Error: device /dev/ttyUSB0 is busy",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'serial_port_busy'

    def test_radio_tx_fail(self, parser):
        """TX failure detected."""
        entry = parser.parse_line(
            "Radio TX failed: timeout waiting for ack",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'radio_tx_failed'
        assert entry.category == 'hardware'

    def test_channel_busy(self, parser):
        """Channel busy detected."""
        entry = parser.parse_line(
            "WARNING: Channel utilization at 85%",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'channel_busy'
        assert entry.category == 'performance'

    def test_node_timeout(self, parser):
        """Node timeout detected."""
        entry = parser.parse_line(
            "Node !abc123 heartbeat missed, timeout",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'node_timeout'

    def test_connection_refused(self, parser):
        """Connection refused detected."""
        entry = parser.parse_line(
            "TCP connection refused to localhost:4403",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'mesh_connection_refused'

    def test_firmware_mismatch(self, parser):
        """Firmware mismatch detected."""
        entry = parser.parse_line(
            "Warning: firmware version mismatch detected",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'firmware_mismatch'

    def test_encryption_error(self, parser):
        """Encryption error detected."""
        entry = parser.parse_line(
            "Decrypt error: invalid key for channel",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'encryption_error'
        assert entry.category == 'protocol'

    def test_queue_overflow(self, parser):
        """Queue overflow detected."""
        entry = parser.parse_line(
            "Message queue full, dropping oldest packet",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'queue_overflow'

    def test_gps_lost(self, parser):
        """GPS fix lost detected."""
        entry = parser.parse_line(
            "GPS no fix - waiting for satellites",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name == 'gps_fix_lost'

    def test_suggestions_present(self, parser):
        """All meshtastic patterns have suggestions."""
        for pattern in MESHTASTIC_PATTERNS:
            assert pattern.suggestion, f"{pattern.name} has no suggestion"


# =============================================================================
# RNS Patterns
# =============================================================================

class TestRNSPatterns:
    def test_transport_unavailable(self, parser):
        """Transport unavailable detected."""
        entry = parser.parse_line(
            "RNS transport unavailable, waiting for interface",
            source=LogSource.RNSD)
        assert entry.pattern_name == 'rns_transport_unavailable'
        assert entry.category == 'connectivity'

    def test_interface_error(self, parser):
        """Interface error detected."""
        entry = parser.parse_line(
            "Interface TCPClient disconnected with error",
            source=LogSource.RNSD)
        assert entry.pattern_name == 'rns_interface_error'

    def test_identity_error(self, parser):
        """Identity error detected."""
        entry = parser.parse_line(
            "Identity file corrupt or missing",
            source=LogSource.RNSD)
        assert entry.pattern_name == 'rns_identity_error'

    def test_path_timeout(self, parser):
        """Path timeout detected."""
        entry = parser.parse_line(
            "Path request timeout for destination abc123",
            source=LogSource.RNSD)
        assert entry.pattern_name == 'rns_path_timeout'

    def test_link_closed(self, parser):
        """Link closed detected."""
        entry = parser.parse_line(
            "Link to peer terminated unexpectedly",
            source=LogSource.RNSD)
        assert entry.pattern_name == 'rns_link_closed'

    def test_announce_collision(self, parser):
        """Announce collision detected (info, not error)."""
        entry = parser.parse_line(
            "Announce collision detected, already known",
            source=LogSource.RNSD)
        assert entry.pattern_name == 'rns_announce_collision'
        assert entry.severity == LogSeverity.INFO

    def test_suggestions_present(self, parser):
        """All RNS patterns have suggestions."""
        for pattern in RNS_PATTERNS:
            assert pattern.suggestion, f"{pattern.name} has no suggestion"


# =============================================================================
# Systemd Patterns
# =============================================================================

class TestSystemdPatterns:
    def test_service_crashed(self, parser):
        """Service crash detected."""
        entry = parser.parse_line(
            "meshtasticd.service: Main process exited, code=exited, status=1/FAILURE",
            source=LogSource.SYSTEMD)
        assert entry.pattern_name == 'service_crashed'
        assert entry.category == 'resource'

    def test_oom_killed(self, parser):
        """OOM kill detected."""
        entry = parser.parse_line(
            "Out of memory: Killed process 1234 (meshtasticd)",
            source=LogSource.SYSTEMD)
        assert entry.pattern_name == 'oom_killed'
        assert entry.severity == LogSeverity.CRITICAL

    def test_disk_full(self, parser):
        """Disk full detected."""
        entry = parser.parse_line(
            "write error: No space left on device",
            source=LogSource.SYSTEMD)
        assert entry.pattern_name == 'disk_full'
        assert entry.severity == LogSeverity.CRITICAL

    def test_restart_loop(self, parser):
        """Service restart loop detected."""
        entry = parser.parse_line(
            "meshtasticd.service: Start request repeated too many restarts",
            source=LogSource.SYSTEMD)
        assert entry.pattern_name == 'service_restart_loop'

    def test_permission_denied(self, parser):
        """Permission denied detected."""
        entry = parser.parse_line(
            "Permission denied accessing /dev/ttyUSB0",
            source=LogSource.SYSTEMD)
        assert entry.pattern_name == 'permission_denied'

    def test_network_unreachable(self, parser):
        """Network unreachable detected."""
        entry = parser.parse_line(
            "Network is unreachable for MQTT broker",
            source=LogSource.SYSTEMD)
        assert entry.pattern_name == 'network_unreachable'


# =============================================================================
# MeshForge Patterns
# =============================================================================

class TestMeshForgePatterns:
    def test_config_error(self, parser):
        """Config load error detected."""
        entry = parser.parse_line(
            "Settings file missing or invalid at /home/user/.config/meshforge",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'config_load_error'
        assert entry.category == 'configuration'

    def test_mqtt_disconnect(self, parser):
        """MQTT disconnect detected."""
        entry = parser.parse_line(
            "MQTT broker disconnected: Connection lost",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'mqtt_disconnect'

    def test_bridge_failure(self, parser):
        """Bridge failure detected."""
        entry = parser.parse_line(
            "Gateway bridge failed to forward message",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'bridge_failure'

    def test_database_error(self, parser):
        """Database error detected."""
        entry = parser.parse_line(
            "SQLite database error: disk I/O error",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'database_error'

    def test_map_generation_failed(self, parser):
        """Map generation failure detected."""
        entry = parser.parse_line(
            "Map render failed: missing tile data",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'map_generation_failed'

    def test_timeout_operation(self, parser):
        """Operation timeout detected."""
        entry = parser.parse_line(
            "Request timed out waiting for meshtasticd response",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'timeout_operation'

    def test_import_error(self, parser):
        """Import error detected."""
        entry = parser.parse_line(
            "Module 'folium' not found, map features disabled",
            source=LogSource.MESHFORGE)
        assert entry.pattern_name == 'import_error'


# =============================================================================
# Multi-line Parsing
# =============================================================================

class TestMultiLineParsing:
    def test_parse_multiple_lines(self, parser):
        """Parse multiple lines returns only errors."""
        lines = [
            "INFO: Started successfully",
            "DEBUG: Loading config",
            "ERROR: Serial port disconnected",
            "INFO: Reconnecting...",
            "WARNING: Channel busy at 75%",
        ]
        entries = parser.parse_lines(lines, LogSource.MESHTASTICD)
        assert len(entries) == 2  # Error + warning
        assert entries[0].pattern_name == 'serial_connection_lost'
        assert entries[1].pattern_name == 'channel_busy'

    def test_context_lines_captured(self, parser):
        """Context lines around errors are captured."""
        lines = [
            "Line 1: normal",
            "Line 2: normal",
            "ERROR: Serial port disconnected",
            "Line 4: normal",
            "Line 5: normal",
        ]
        entries = parser.parse_lines(lines, LogSource.MESHTASTICD)
        assert len(entries) == 1
        assert len(entries[0].context_lines) > 1

    def test_parse_text_block(self, parser):
        """Parse a text block works."""
        text = """INFO: Starting meshtasticd
DEBUG: Initializing radio
ERROR: device /dev/ttyUSB0 is busy
INFO: Retrying in 5s"""
        entries = parser.parse_text(text, LogSource.MESHTASTICD)
        assert len(entries) == 1
        assert entries[0].pattern_name == 'serial_port_busy'

    def test_empty_lines_skipped(self, parser):
        """Empty lines produce no output."""
        entries = parser.parse_lines(["", "  ", "\n"], LogSource.UNKNOWN)
        assert len(entries) == 0

    def test_large_log(self, parser):
        """Large log file doesn't crash."""
        lines = [f"INFO: Normal log line {i}" for i in range(1000)]
        lines[500] = "ERROR: Something went wrong with connection refused"
        entries = parser.parse_lines(lines, LogSource.MESHTASTICD)
        assert len(entries) >= 1


# =============================================================================
# Error Summary
# =============================================================================

class TestErrorSummary:
    def test_summary_structure(self, parser):
        """Summary has expected structure."""
        entries = [
            LogEntry(raw_line="err1", severity=LogSeverity.ERROR,
                    category='connectivity', pattern_name='mesh_connection_refused'),
            LogEntry(raw_line="err2", severity=LogSeverity.WARNING,
                    category='performance', pattern_name='channel_busy'),
        ]
        summary = parser.get_error_summary(entries)
        assert 'total_errors' in summary
        assert 'by_severity' in summary
        assert 'by_category' in summary
        assert 'top_patterns' in summary

    def test_summary_counts(self, parser):
        """Summary counts are correct."""
        entries = [
            LogEntry(raw_line="e1", severity=LogSeverity.ERROR,
                    category='connectivity', pattern_name='p1'),
            LogEntry(raw_line="e2", severity=LogSeverity.ERROR,
                    category='connectivity', pattern_name='p1'),
            LogEntry(raw_line="e3", severity=LogSeverity.WARNING,
                    category='hardware', pattern_name='p2'),
        ]
        summary = parser.get_error_summary(entries)
        assert summary['total_errors'] == 3
        assert summary['by_severity']['error'] == 2
        assert summary['by_severity']['warning'] == 1
        assert summary['by_category']['connectivity'] == 2
        assert summary['by_category']['hardware'] == 1

    def test_top_patterns_sorted(self, parser):
        """Top patterns sorted by frequency."""
        entries = [
            LogEntry(raw_line="", pattern_name='common',
                    severity=LogSeverity.ERROR, category='c'),
            LogEntry(raw_line="", pattern_name='common',
                    severity=LogSeverity.ERROR, category='c'),
            LogEntry(raw_line="", pattern_name='rare',
                    severity=LogSeverity.ERROR, category='c'),
        ]
        summary = parser.get_error_summary(entries)
        assert summary['top_patterns'][0][0] == 'common'
        assert summary['top_patterns'][0][1] == 2

    def test_empty_entries(self, parser):
        """Empty entry list gives zero counts."""
        summary = parser.get_error_summary([])
        assert summary['total_errors'] == 0


# =============================================================================
# Source Filtering
# =============================================================================

class TestSourceFiltering:
    def test_patterns_for_source(self, parser):
        """Filter patterns by source."""
        mesh_patterns = parser.patterns_for_source(LogSource.MESHTASTICD)
        assert len(mesh_patterns) >= len(MESHTASTIC_PATTERNS)

    def test_source_mismatch_no_match(self):
        """Pattern for wrong source doesn't match."""
        # Create parser with only meshtasticd patterns
        parser = LogParser(patterns=MESHTASTIC_PATTERNS)
        entry = parser.parse_line(
            "RNS transport unavailable",
            source=LogSource.RNSD)
        # Meshtasticd patterns shouldn't match for RNSD source
        assert entry.pattern_name == ''

    def test_unknown_source_matches_all(self, parser):
        """UNKNOWN source matches any pattern."""
        entry = parser.parse_line(
            "Serial port disconnected unexpectedly",
            source=LogSource.UNKNOWN)
        assert entry.pattern_name == 'serial_connection_lost'

    def test_pattern_count(self, parser):
        """Pattern count is correct."""
        assert parser.pattern_count == len(ALL_PATTERNS)
        assert parser.pattern_count >= 29  # At least our defined patterns


# =============================================================================
# Convenience Function
# =============================================================================

class TestConvenienceFunction:
    def test_parse_log_lines_basic(self):
        """Convenience function works."""
        lines = ["ERROR: connection refused to meshtasticd"]
        entries = parse_log_lines(lines, source='meshtasticd')
        assert len(entries) == 1
        assert entries[0].is_error

    def test_parse_log_lines_unknown_source(self):
        """Unknown source string handled."""
        lines = ["ERROR: something broke"]
        entries = parse_log_lines(lines, source='unknown_thing')
        assert len(entries) == 1

    def test_parse_log_lines_empty(self):
        """Empty input returns empty list."""
        entries = parse_log_lines([], source='meshtasticd')
        assert entries == []


# =============================================================================
# Format Report
# =============================================================================

class TestFormatReport:
    def test_no_errors_message(self):
        """Empty entries shows 'no errors' message."""
        report = format_error_report([])
        assert 'No errors' in report

    def test_report_contains_categories(self):
        """Report groups by category."""
        entries = [
            LogEntry(raw_line="err", severity=LogSeverity.ERROR,
                    category='connectivity', pattern_name='p1',
                    suggestion='fix it', is_error=True),
        ]
        report = format_error_report(entries)
        assert 'CONNECTIVITY' in report

    def test_report_contains_suggestions(self):
        """Report includes suggestions."""
        entries = [
            LogEntry(raw_line="err", severity=LogSeverity.ERROR,
                    category='hardware', pattern_name='serial_lost',
                    suggestion='Check USB cable', is_error=True),
        ]
        report = format_error_report(entries)
        assert 'Check USB cable' in report

    def test_report_nonempty(self):
        """Report is non-empty for errors."""
        entries = [
            LogEntry(raw_line="err", severity=LogSeverity.ERROR,
                    category='test', pattern_name='test_pattern',
                    is_error=True),
        ]
        report = format_error_report(entries)
        assert len(report) > 50

    def test_report_limits_per_category(self):
        """Report limits entries per category."""
        entries = [
            LogEntry(raw_line=f"err{i}", severity=LogSeverity.ERROR,
                    category='same', pattern_name=f'p{i}', is_error=True)
            for i in range(10)
        ]
        report = format_error_report(entries)
        assert 'more' in report  # Should say "and X more"


# =============================================================================
# LogEntry
# =============================================================================

class TestLogEntry:
    def test_to_dict(self):
        """LogEntry.to_dict has correct structure."""
        entry = LogEntry(
            raw_line="test line",
            severity=LogSeverity.ERROR,
            source=LogSource.MESHTASTICD,
            message="test message",
            pattern_name='test_pattern',
            category='connectivity',
            suggestion='do something',
            is_error=True,
        )
        d = entry.to_dict()
        assert d['severity'] == 'error'
        assert d['source'] == 'meshtasticd'
        assert d['pattern_name'] == 'test_pattern'
        assert d['is_error'] is True

    def test_is_error_flag(self, parser):
        """is_error set for warning and above."""
        err = parser.parse_line("ERROR: bad thing")
        warn = parser.parse_line("WARNING: not great")
        info = parser.parse_line("INFO: all good")
        assert err.is_error is True
        assert warn.is_error is True
        assert info.is_error is False


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    def test_very_long_line(self, parser):
        """Very long line doesn't crash."""
        line = "ERROR: " + "x" * 10000
        entry = parser.parse_line(line)
        assert entry.severity == LogSeverity.ERROR

    def test_unicode_in_logs(self, parser):
        """Unicode characters handled."""
        entry = parser.parse_line("ERROR: Ñoño device disconnected")
        assert entry.severity == LogSeverity.ERROR

    def test_binary_like_content(self, parser):
        """Lines with special characters don't crash."""
        entry = parser.parse_line("Data: \\x00\\xff\\xfe ERROR in stream")
        assert entry.severity == LogSeverity.ERROR

    def test_multiple_patterns_first_wins(self, parser):
        """When multiple patterns match, first wins."""
        # This line could match both serial and connection patterns
        entry = parser.parse_line(
            "Serial connection error: port closed",
            source=LogSource.MESHTASTICD)
        assert entry.pattern_name != ''  # Something matched

    def test_no_patterns_parser(self):
        """Parser with empty patterns still works."""
        parser = LogParser(patterns=[])
        entry = parser.parse_line("ERROR: something")
        assert entry.severity == LogSeverity.ERROR
        assert entry.pattern_name == ''

    def test_custom_context_lines(self):
        """Custom context line count works."""
        parser = LogParser(context_lines=5)
        lines = [f"line {i}" for i in range(20)]
        lines[10] = "ERROR: something failed badly"
        entries = parser.parse_lines(lines, LogSource.UNKNOWN)
        assert len(entries) == 1
        assert len(entries[0].context_lines) >= 5


# =============================================================================
# Pattern Coverage
# =============================================================================

class TestPatternCoverage:
    def test_all_patterns_have_names(self):
        """Every pattern has a non-empty name."""
        for p in ALL_PATTERNS:
            assert p.name, f"Pattern with regex {p.regex.pattern} has no name"

    def test_all_patterns_have_suggestions(self):
        """Every pattern has a suggestion."""
        for p in ALL_PATTERNS:
            assert p.suggestion, f"Pattern {p.name} has no suggestion"

    def test_all_patterns_have_categories(self):
        """Every pattern has a category."""
        for p in ALL_PATTERNS:
            assert p.category, f"Pattern {p.name} has no category"

    def test_pattern_names_unique(self):
        """All pattern names are unique."""
        names = [p.name for p in ALL_PATTERNS]
        assert len(names) == len(set(names)), "Duplicate pattern names found"

    def test_minimum_pattern_count(self):
        """At least 29 patterns defined."""
        assert len(ALL_PATTERNS) >= 29

    def test_all_sources_have_patterns(self):
        """Each source type has at least some patterns."""
        for source in [LogSource.MESHTASTICD, LogSource.RNSD,
                      LogSource.SYSTEMD, LogSource.MESHFORGE]:
            source_patterns = [p for p in ALL_PATTERNS if p.source == source]
            assert len(source_patterns) >= 3, f"{source} has too few patterns"
