"""
Log parsing patterns — extract common errors from service logs.

Parses log output from meshtasticd, rnsd, systemd journal, and MeshForge
itself. Identifies known error patterns, classifies severity, and produces
structured LogEntry objects for the diagnostic engine.

Supported log sources:
- meshtasticd: Connection errors, serial issues, radio events
- rnsd/RNS: Transport errors, interface failures, identity issues
- systemd: Service start/stop/crash, OOM kills, resource exhaustion
- MeshForge: Application-level errors from Python logging

Usage:
    from utils.log_parser import LogParser, parse_log_lines

    parser = LogParser()
    entries = parser.parse_lines(log_lines, source='meshtasticd')

    for entry in entries:
        if entry.is_error:
            print(f"[{entry.severity}] {entry.pattern_name}: {entry.message}")
            print(f"  Suggested action: {entry.suggestion}")
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Pattern, Tuple


class LogSeverity(Enum):
    """Log entry severity levels."""
    DEBUG = 'debug'
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


class LogSource(Enum):
    """Known log sources."""
    MESHTASTICD = 'meshtasticd'
    RNSD = 'rnsd'
    SYSTEMD = 'systemd'
    MESHFORGE = 'meshforge'
    UNKNOWN = 'unknown'


@dataclass
class LogEntry:
    """Parsed and classified log entry."""
    raw_line: str
    timestamp: Optional[float] = None
    severity: LogSeverity = LogSeverity.INFO
    source: LogSource = LogSource.UNKNOWN
    message: str = ""
    pattern_name: str = ""  # Which pattern matched
    category: str = ""      # connectivity, hardware, protocol, resource, etc.
    suggestion: str = ""    # Recommended action
    is_error: bool = False  # Quick check: severity >= WARNING
    context_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'severity': self.severity.value,
            'source': self.source.value,
            'message': self.message,
            'pattern_name': self.pattern_name,
            'category': self.category,
            'suggestion': self.suggestion,
            'is_error': self.is_error,
            'raw_line': self.raw_line,
        }


@dataclass
class LogPattern:
    """A known log pattern to match against."""
    name: str
    regex: Pattern
    severity: LogSeverity
    category: str
    suggestion: str
    source: LogSource = LogSource.UNKNOWN  # Empty = match any source


# Common timestamp patterns
TIMESTAMP_PATTERNS = [
    # ISO 8601: 2026-01-23T14:30:00.000Z
    re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)'),
    # Syslog: Jan 23 14:30:00
    re.compile(r'([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})'),
    # Python logging: 2026-01-23 14:30:00,000
    re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3})'),
    # Epoch seconds (float)
    re.compile(r'^(\d{10}\.\d+)'),
]

# Severity detection patterns (applied when source doesn't specify)
SEVERITY_PATTERNS = [
    (re.compile(r'\b(CRITICAL|FATAL|PANIC)\b', re.IGNORECASE), LogSeverity.CRITICAL),
    (re.compile(r'\b(ERROR|ERR|FAIL(?:ED)?|EXCEPTION)\b', re.IGNORECASE), LogSeverity.ERROR),
    (re.compile(r'\b(WARN(?:ING)?|ALERT)\b', re.IGNORECASE), LogSeverity.WARNING),
    (re.compile(r'\b(INFO|NOTICE)\b', re.IGNORECASE), LogSeverity.INFO),
    (re.compile(r'\b(DEBUG|TRACE|VERBOSE)\b', re.IGNORECASE), LogSeverity.DEBUG),
]


# =============================================================================
# Pattern Definitions — meshtasticd
# =============================================================================

MESHTASTIC_PATTERNS = [
    LogPattern(
        name='serial_connection_lost',
        regex=re.compile(r'(serial|uart).*(disconnect|lost|closed|error)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='hardware',
        suggestion='Check USB cable connection and device power',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='serial_port_busy',
        regex=re.compile(r'(port|device).*(busy|in use|lock)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='hardware',
        suggestion='Another process is using the serial port. Check for other meshtastic clients.',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='radio_tx_failed',
        regex=re.compile(r'(tx|transmit).*(fail|error|timeout)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='hardware',
        suggestion='Radio TX failure — check antenna connection and channel congestion',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='channel_busy',
        regex=re.compile(r'channel\s+(busy|utilization|congesti)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='performance',
        suggestion='High channel utilization — consider longer TX intervals or different channel',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='node_timeout',
        regex=re.compile(r'node.*timeout|heartbeat.*miss', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='connectivity',
        suggestion='Node not responding — may be out of range or powered off',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='mesh_connection_refused',
        regex=re.compile(r'connection\s+(refused|reset|closed)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='connectivity',
        suggestion='Service connection refused — check if meshtasticd is running',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='firmware_mismatch',
        regex=re.compile(r'(firmware|version)\s*(mismatch|incompatible|unsupported)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='protocol',
        suggestion='Firmware version mismatch — update device firmware',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='encryption_error',
        regex=re.compile(r'(encrypt|decrypt|crypto|key).*(error|fail|invalid)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='protocol',
        suggestion='Encryption error — verify channel keys match across all nodes',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='queue_overflow',
        regex=re.compile(r'(queue|buffer).*(full|overflow|drop)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='performance',
        suggestion='Message queue full — reduce message rate or increase queue size',
        source=LogSource.MESHTASTICD,
    ),
    LogPattern(
        name='gps_fix_lost',
        regex=re.compile(r'gps.*(no fix|lost|unavailable)', re.IGNORECASE),
        severity=LogSeverity.INFO,
        category='hardware',
        suggestion='GPS fix lost — ensure clear sky view for GPS antenna',
        source=LogSource.MESHTASTICD,
    ),
]


# =============================================================================
# Pattern Definitions — RNS/rnsd
# =============================================================================

RNS_PATTERNS = [
    LogPattern(
        name='rns_transport_unavailable',
        regex=re.compile(r'transport.*(unavailable|not ready|offline)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='connectivity',
        suggestion='RNS transport unavailable — check rnsd service and interface config',
        source=LogSource.RNSD,
    ),
    LogPattern(
        name='rns_interface_error',
        regex=re.compile(r'interface.*(error|fail|disconnect|down)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='connectivity',
        suggestion='RNS interface error — check physical connection and interface config',
        source=LogSource.RNSD,
    ),
    LogPattern(
        name='rns_identity_error',
        regex=re.compile(r'identity.*(error|invalid|corrupt|missing)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='protocol',
        suggestion='RNS identity error — regenerate identity with rnid',
        source=LogSource.RNSD,
    ),
    LogPattern(
        name='rns_path_timeout',
        regex=re.compile(r'path.*(timeout|expired|not found)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='connectivity',
        suggestion='RNS path timeout — destination may be offline or out of range',
        source=LogSource.RNSD,
    ),
    LogPattern(
        name='rns_link_closed',
        regex=re.compile(r'link.*(closed|broken|terminated)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='connectivity',
        suggestion='RNS link closed — peer may have disconnected',
        source=LogSource.RNSD,
    ),
    LogPattern(
        name='rns_announce_collision',
        regex=re.compile(r'announce.*(collision|duplicate|already)', re.IGNORECASE),
        severity=LogSeverity.INFO,
        category='protocol',
        suggestion='RNS announce collision — normal in dense networks, no action needed',
        source=LogSource.RNSD,
    ),
]


# =============================================================================
# Pattern Definitions — systemd
# =============================================================================

SYSTEMD_PATTERNS = [
    LogPattern(
        name='service_crashed',
        regex=re.compile(r'(process exited.*status=[1-9]|service.*failed|terminated.*signal)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='resource',
        suggestion='Service crashed — check logs with journalctl -u <service>',
        source=LogSource.SYSTEMD,
    ),
    LogPattern(
        name='oom_killed',
        regex=re.compile(r'(oom|out of memory|killed process|oom-killer)', re.IGNORECASE),
        severity=LogSeverity.CRITICAL,
        category='resource',
        suggestion='Process killed by OOM — system needs more RAM or has a memory leak',
        source=LogSource.SYSTEMD,
    ),
    LogPattern(
        name='disk_full',
        regex=re.compile(r'(no space|disk full|write error.*ENOSPC)', re.IGNORECASE),
        severity=LogSeverity.CRITICAL,
        category='resource',
        suggestion='Disk full — free space with: sudo journalctl --vacuum-size=100M',
        source=LogSource.SYSTEMD,
    ),
    LogPattern(
        name='service_restart_loop',
        regex=re.compile(r'(start.*limit|too many restarts|restart.*failed)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='resource',
        suggestion='Service in restart loop — check config and dependencies',
        source=LogSource.SYSTEMD,
    ),
    LogPattern(
        name='permission_denied',
        regex=re.compile(r'permission\s+denied|access\s+denied|EACCES', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='configuration',
        suggestion='Permission denied — check file ownership and service user',
        source=LogSource.SYSTEMD,
    ),
    LogPattern(
        name='network_unreachable',
        regex=re.compile(r'network.*(unreachable|down)|ENETUNREACH', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='connectivity',
        suggestion='Network unreachable — check network configuration and connectivity',
        source=LogSource.SYSTEMD,
    ),
]


# =============================================================================
# Pattern Definitions — MeshForge application
# =============================================================================

MESHFORGE_PATTERNS = [
    LogPattern(
        name='config_load_error',
        regex=re.compile(r'(config|settings).*(error|fail|missing|invalid)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='configuration',
        suggestion='Configuration error — check ~/.config/meshforge/ files',
        source=LogSource.MESHFORGE,
    ),
    LogPattern(
        name='mqtt_disconnect',
        regex=re.compile(r'mqtt.*(disconnect|lost|closed|error)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='connectivity',
        suggestion='MQTT broker disconnected — will auto-reconnect',
        source=LogSource.MESHFORGE,
    ),
    LogPattern(
        name='bridge_failure',
        regex=re.compile(r'bridge.*(fail|error|crash)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='connectivity',
        suggestion='Gateway bridge failure — check both Meshtastic and RNS connections',
        source=LogSource.MESHFORGE,
    ),
    LogPattern(
        name='database_error',
        regex=re.compile(r'(sqlite|database|db).*(error|corrupt|locked)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='resource',
        suggestion='Database error — check disk space and file permissions',
        source=LogSource.MESHFORGE,
    ),
    LogPattern(
        name='map_generation_failed',
        regex=re.compile(r'map.*(fail|error|generate|render)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='performance',
        suggestion='Map generation failed — check folium/leaflet dependencies',
        source=LogSource.MESHFORGE,
    ),
    LogPattern(
        name='timeout_operation',
        regex=re.compile(r'(operation|request|command).*(timeout|timed out)', re.IGNORECASE),
        severity=LogSeverity.WARNING,
        category='performance',
        suggestion='Operation timeout — service may be overloaded or unresponsive',
        source=LogSource.MESHFORGE,
    ),
    LogPattern(
        name='import_error',
        regex=re.compile(r'(import|module).*(error|not found|missing)', re.IGNORECASE),
        severity=LogSeverity.ERROR,
        category='configuration',
        suggestion='Module import error — install missing dependency with pip',
        source=LogSource.MESHFORGE,
    ),
]


# All patterns combined
ALL_PATTERNS: List[LogPattern] = (
    MESHTASTIC_PATTERNS +
    RNS_PATTERNS +
    SYSTEMD_PATTERNS +
    MESHFORGE_PATTERNS
)


class LogParser:
    """Parses log lines and matches against known error patterns.

    Extracts timestamps, severity, and matches lines against a library
    of known error patterns to produce structured LogEntry objects.

    Args:
        patterns: Custom pattern list (default: ALL_PATTERNS).
        context_lines: Number of surrounding lines to capture as context.
    """

    def __init__(self,
                 patterns: Optional[List[LogPattern]] = None,
                 context_lines: int = 2):
        """Initialize log parser.

        Args:
            patterns: List of LogPattern to match. Default: all built-in.
            context_lines: Lines of context to include around matches.
        """
        self.patterns = patterns if patterns is not None else ALL_PATTERNS
        self.context_lines = context_lines

    def parse_line(self, line: str,
                   source: LogSource = LogSource.UNKNOWN) -> LogEntry:
        """Parse a single log line.

        Args:
            line: Raw log line text.
            source: Known source of this line.

        Returns:
            LogEntry with classification.
        """
        line = line.strip()
        if not line:
            return LogEntry(raw_line=line, severity=LogSeverity.DEBUG)

        # Extract timestamp
        timestamp = self._extract_timestamp(line)

        # Detect severity from line content
        severity = self._detect_severity(line)

        # Match against patterns
        matched_pattern = self._match_pattern(line, source)

        if matched_pattern:
            return LogEntry(
                raw_line=line,
                timestamp=timestamp,
                severity=matched_pattern.severity,
                source=matched_pattern.source if matched_pattern.source != LogSource.UNKNOWN else source,
                message=line,
                pattern_name=matched_pattern.name,
                category=matched_pattern.category,
                suggestion=matched_pattern.suggestion,
                is_error=matched_pattern.severity.value in ('warning', 'error', 'critical'),
            )

        # No pattern match — return with detected severity
        return LogEntry(
            raw_line=line,
            timestamp=timestamp,
            severity=severity,
            source=source,
            message=line,
            is_error=severity.value in ('warning', 'error', 'critical'),
        )

    def parse_lines(self, lines: List[str],
                    source: LogSource = LogSource.UNKNOWN) -> List[LogEntry]:
        """Parse multiple log lines with context tracking.

        Args:
            lines: List of raw log lines.
            source: Source for all lines.

        Returns:
            List of LogEntry, only errors/warnings included.
            Each entry includes surrounding context lines.
        """
        entries = []
        parsed = [self.parse_line(l, source) for l in lines]

        for i, entry in enumerate(parsed):
            if entry.is_error:
                # Add context lines
                start = max(0, i - self.context_lines)
                end = min(len(lines), i + self.context_lines + 1)
                entry.context_lines = [l.strip() for l in lines[start:end]
                                       if l.strip()]
                entries.append(entry)

        return entries

    def parse_text(self, text: str,
                   source: LogSource = LogSource.UNKNOWN) -> List[LogEntry]:
        """Parse a block of log text.

        Args:
            text: Multi-line log text.
            source: Source identifier.

        Returns:
            List of error/warning LogEntry objects.
        """
        lines = text.splitlines()
        return self.parse_lines(lines, source)

    def get_error_summary(self, entries: List[LogEntry]) -> Dict[str, Any]:
        """Summarize parsed errors by category and severity.

        Args:
            entries: List of LogEntry from parse_lines/parse_text.

        Returns:
            Summary dict with counts and top patterns.
        """
        by_severity: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        by_pattern: Dict[str, int] = {}

        for entry in entries:
            sev = entry.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1

            if entry.category:
                by_category[entry.category] = by_category.get(entry.category, 0) + 1

            if entry.pattern_name:
                by_pattern[entry.pattern_name] = by_pattern.get(entry.pattern_name, 0) + 1

        return {
            'total_errors': len(entries),
            'by_severity': by_severity,
            'by_category': by_category,
            'top_patterns': sorted(by_pattern.items(), key=lambda x: x[1], reverse=True)[:10],
        }

    def _extract_timestamp(self, line: str) -> Optional[float]:
        """Try to extract a timestamp from a log line.

        Args:
            line: Raw log line.

        Returns:
            Unix timestamp or None if not parseable.
        """
        for pattern in TIMESTAMP_PATTERNS:
            m = pattern.search(line)
            if m:
                # Return match position as approximation
                # Full timestamp parsing would need datetime, kept simple here
                return None  # Timestamp string found but not converted
        return None

    def _detect_severity(self, line: str) -> LogSeverity:
        """Detect severity level from line content.

        Args:
            line: Raw log line.

        Returns:
            Detected LogSeverity.
        """
        for pattern, severity in SEVERITY_PATTERNS:
            if pattern.search(line):
                return severity
        return LogSeverity.INFO

    def _match_pattern(self, line: str,
                       source: LogSource = LogSource.UNKNOWN) -> Optional[LogPattern]:
        """Match line against known error patterns.

        Args:
            line: Raw log line.
            source: Known source to filter patterns.

        Returns:
            First matching LogPattern or None.
        """
        for pattern in self.patterns:
            # Filter by source if specified
            if (pattern.source != LogSource.UNKNOWN and
                    source != LogSource.UNKNOWN and
                    pattern.source != source):
                continue

            if pattern.regex.search(line):
                return pattern

        return None

    @property
    def pattern_count(self) -> int:
        """Number of patterns loaded."""
        return len(self.patterns)

    def patterns_for_source(self, source: LogSource) -> List[LogPattern]:
        """Get patterns applicable to a specific source.

        Args:
            source: Log source to filter by.

        Returns:
            List of applicable LogPattern.
        """
        return [p for p in self.patterns
                if p.source == source or p.source == LogSource.UNKNOWN]


def parse_log_lines(lines: List[str],
                    source: str = 'unknown') -> List[LogEntry]:
    """Convenience function to parse log lines.

    Args:
        lines: Raw log lines.
        source: Source name string.

    Returns:
        List of error/warning LogEntry objects.
    """
    source_enum = LogSource.UNKNOWN
    for s in LogSource:
        if s.value == source.lower():
            source_enum = s
            break

    parser = LogParser()
    return parser.parse_lines(lines, source_enum)


def format_error_report(entries: List[LogEntry]) -> str:
    """Format parsed log errors as a text report.

    Args:
        entries: List of LogEntry (typically errors/warnings only).

    Returns:
        Formatted multi-line string report.
    """
    if not entries:
        return "No errors found in log output."

    lines = []
    lines.append("=" * 60)
    lines.append(f"  Log Analysis: {len(entries)} issues found")
    lines.append("=" * 60)
    lines.append("")

    # Group by category
    by_category: Dict[str, List[LogEntry]] = {}
    for entry in entries:
        cat = entry.category or 'uncategorized'
        by_category.setdefault(cat, []).append(entry)

    for category, cat_entries in sorted(by_category.items()):
        lines.append(f"  [{category.upper()}] ({len(cat_entries)} issues)")
        for entry in cat_entries[:5]:  # Limit to 5 per category
            sev_mark = {
                LogSeverity.CRITICAL: 'XXX',
                LogSeverity.ERROR: 'ERR',
                LogSeverity.WARNING: 'WRN',
            }.get(entry.severity, '   ')
            lines.append(f"    {sev_mark} {entry.pattern_name or 'unknown'}")
            if entry.suggestion:
                lines.append(f"        -> {entry.suggestion}")
        if len(cat_entries) > 5:
            lines.append(f"    ... and {len(cat_entries) - 5} more")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)
