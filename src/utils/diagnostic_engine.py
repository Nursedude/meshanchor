"""
MeshForge Diagnostic Intelligence Engine.

Provides intelligent, context-aware diagnostics for mesh network operations.
Works standalone (rule-based) or enhanced with Claude API (PRO mode).

The engine correlates symptoms, applies domain knowledge, and provides
actionable diagnoses that help users understand and fix issues.

Architecture:
    Symptoms → Context → Analysis → Diagnosis → Suggestions

Usage:
    engine = DiagnosticEngine()

    # Report a symptom
    diagnosis = engine.diagnose(
        symptom="Connection refused to meshtasticd",
        context={"port": 4403, "service_running": True}
    )

    print(diagnosis.explanation)
    print(diagnosis.suggestions)
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import deque
import threading

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Diagnostic severity levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Category(Enum):
    """Diagnostic categories for mesh operations."""
    CONNECTIVITY = "connectivity"
    HARDWARE = "hardware"
    PROTOCOL = "protocol"
    PERFORMANCE = "performance"
    SECURITY = "security"
    CONFIGURATION = "configuration"
    RESOURCE = "resource"


@dataclass
class Symptom:
    """A reported symptom or event."""
    message: str
    category: Category
    severity: Severity
    timestamp: datetime = field(default_factory=datetime.now)
    context: Dict[str, Any] = field(default_factory=dict)
    source: str = ""  # Which component reported this

    def __hash__(self):
        return hash((self.message, self.category, self.source))


@dataclass
class Diagnosis:
    """Result of diagnostic analysis."""
    symptom: Symptom
    likely_cause: str
    confidence: float  # 0.0 to 1.0
    evidence: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    related_symptoms: List[Symptom] = field(default_factory=list)
    auto_recoverable: bool = False
    recovery_action: Optional[str] = None
    explanation: str = ""  # Human-readable explanation
    expertise_level: str = "intermediate"  # novice, intermediate, expert

    def to_log_format(self) -> str:
        """Format diagnosis for logging."""
        lines = [
            f"[DIAGNOSIS] {self.symptom.message}",
            f"├── Likely cause: {self.likely_cause}",
        ]
        for ev in self.evidence[:3]:
            lines.append(f"├── Evidence: {ev}")
        if self.suggestions:
            lines.append(f"├── Suggested fix: {self.suggestions[0]}")
        if self.auto_recoverable:
            lines.append(f"└── Auto-recovery: {self.recovery_action}")
        else:
            lines.append(f"└── Confidence: {self.confidence:.0%}")
        return "\n".join(lines)


@dataclass
class DiagnosticRule:
    """A rule for diagnosing symptoms."""
    name: str
    pattern: str  # Regex pattern to match symptom message
    category: Category
    cause_template: str
    evidence_checks: List[Callable[[Dict], Optional[str]]] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    auto_recoverable: bool = False
    recovery_action: Optional[str] = None
    confidence_base: float = 0.7
    expertise_level: str = "intermediate"


class DiagnosticEngine:
    """
    Intelligent diagnostic engine for mesh network operations.

    Features:
    - Rule-based symptom analysis (standalone)
    - Symptom correlation over time
    - Context-aware diagnosis
    - Auto-recovery suggestions
    - Expertise-level explanations
    """

    # Symptom history retention
    HISTORY_MAX_SIZE = 1000
    HISTORY_MAX_AGE = timedelta(hours=24)

    # Correlation window for related symptoms
    CORRELATION_WINDOW = timedelta(minutes=5)

    def __init__(self):
        """Initialize the diagnostic engine."""
        self._rules: List[DiagnosticRule] = []
        self._symptom_history: deque = deque(maxlen=self.HISTORY_MAX_SIZE)
        self._diagnosis_history: deque = deque(maxlen=500)
        self._lock = threading.Lock()

        # Callbacks for auto-recovery
        self._recovery_handlers: Dict[str, Callable] = {}

        # Load built-in rules
        self._load_mesh_rules()

        # Stats
        self._stats = {
            "symptoms_processed": 0,
            "diagnoses_made": 0,
            "auto_recoveries": 0,
            "correlations_found": 0,
        }

    def _load_mesh_rules(self) -> None:
        """Load built-in diagnostic rules for mesh networking."""

        # ===== CONNECTIVITY RULES =====

        self.add_rule(DiagnosticRule(
            name="meshtasticd_connection_refused",
            pattern=r"(?i)connection\s+(refused|rejected).*meshtastic",
            category=Category.CONNECTIVITY,
            cause_template="Another client is likely connected to meshtasticd (single-client limitation)",
            suggestions=[
                "Check for other Meshtastic clients: ps aux | grep -E 'nomadnet|meshing|meshtastic'",
                "Restart meshtasticd: sudo systemctl restart meshtasticd",
                "Use --host to connect to a different instance",
            ],
            auto_recoverable=True,
            recovery_action="Will retry connection with exponential backoff",
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="meshtasticd_not_running",
            pattern=r"(?i)(meshtasticd|4403).*(not running|refused|unavailable)",
            category=Category.CONNECTIVITY,
            cause_template="meshtasticd service is not running or not listening on port 4403",
            suggestions=[
                "Check service status: sudo systemctl status meshtasticd",
                "Start the service: sudo systemctl start meshtasticd",
                "Check logs: journalctl -u meshtasticd -n 50",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="rns_transport_unavailable",
            pattern=r"(?i)(rns|reticulum).*(transport|interface).*(unavailable|failed|error)",
            category=Category.CONNECTIVITY,
            cause_template="RNS transport interface failed to initialize",
            suggestions=[
                "Check rnsd status: sudo systemctl status rnsd",
                "Verify config: cat ~/.reticulum/config",
                "Check interface availability (serial port, network)",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="mqtt_connection_failed",
            pattern=r"(?i)mqtt.*(connection|connect).*(failed|error|refused|timeout)",
            category=Category.CONNECTIVITY,
            cause_template="MQTT broker connection failed",
            suggestions=[
                "Verify broker address and port",
                "Check network connectivity: ping mqtt.meshtastic.org",
                "Verify credentials if authentication required",
                "Check TLS certificate if using secure connection",
            ],
            auto_recoverable=True,
            recovery_action="Will reconnect with exponential backoff",
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="node_timeout",
            pattern=r"(?i)node.*(timeout|not responding|unreachable)",
            category=Category.CONNECTIVITY,
            cause_template="Remote node is not responding within expected timeframe",
            suggestions=[
                "Node may be out of range or powered off",
                "Check if node appears in mesh: meshtastic --nodes",
                "Verify RF path (line of sight, obstacles)",
                "Node may be in sleep mode for power saving",
            ],
            confidence_base=0.7,
        ))

        # ===== HARDWARE RULES =====

        self.add_rule(DiagnosticRule(
            name="serial_port_busy",
            pattern=r"(?i)(serial|tty|usb).*(busy|in use|locked|permission)",
            category=Category.HARDWARE,
            cause_template="Serial port is in use by another process or has permission issues",
            suggestions=[
                "Find process using port: sudo lsof /dev/ttyUSB0",
                "Kill blocking process or use different port",
                "Check permissions: ls -la /dev/ttyUSB*",
                "Add user to dialout group: sudo usermod -aG dialout $USER",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="device_disconnected",
            pattern=r"(?i)(device|radio|hardware).*(disconnect|removed|not found|missing)",
            category=Category.HARDWARE,
            cause_template="Hardware device was disconnected or not detected",
            suggestions=[
                "Check USB connection: lsusb",
                "Check dmesg for device events: dmesg | tail -20",
                "Try different USB port or cable",
                "Device may need power cycle",
            ],
            confidence_base=0.9,
        ))

        # ===== PROTOCOL RULES =====

        self.add_rule(DiagnosticRule(
            name="encryption_mismatch",
            pattern=r"(?i)(encryption|decrypt|key).*(mismatch|failed|invalid|error)",
            category=Category.PROTOCOL,
            cause_template="Encryption key mismatch between nodes",
            suggestions=[
                "Verify channel encryption key matches on all nodes",
                "Check channel settings: meshtastic --ch-index 0 --info",
                "Reset to default key if needed: meshtastic --ch-set psk default",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="protocol_version_mismatch",
            pattern=r"(?i)(protocol|version|firmware).*(mismatch|incompatible|unsupported)",
            category=Category.PROTOCOL,
            cause_template="Protocol or firmware version incompatibility",
            suggestions=[
                "Update firmware on all nodes to same version",
                "Check firmware: meshtastic --info | grep firmware",
                "See https://meshtastic.org/docs/getting-started/flashing-firmware",
            ],
            confidence_base=0.8,
        ))

        # ===== PERFORMANCE RULES =====

        self.add_rule(DiagnosticRule(
            name="high_channel_utilization",
            pattern=r"(?i)channel\s*utilization.*(high|>50%|warning)",
            category=Category.PERFORMANCE,
            cause_template="Channel utilization is high, may cause message delays or drops",
            suggestions=[
                "Reduce message frequency",
                "Use shorter messages",
                "Consider different channel or modem preset",
                "Spread nodes across multiple channels",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="message_queue_full",
            pattern=r"(?i)(queue|buffer).*(full|overflow|dropping)",
            category=Category.PERFORMANCE,
            cause_template="Message queue is full, new messages may be dropped",
            suggestions=[
                "Reduce outgoing message rate",
                "Check for stuck/slow destination",
                "Increase queue size if persistent",
                "Check for network congestion",
            ],
            auto_recoverable=True,
            recovery_action="Oldest messages will be dropped to make room",
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="low_snr",
            pattern=r"(?i)snr.*(low|weak|poor|<-10|-\d{2})",
            category=Category.PERFORMANCE,
            cause_template="Signal-to-noise ratio is low, indicating weak signal",
            suggestions=[
                "Improve antenna positioning (height, orientation)",
                "Check for obstructions in RF path",
                "Consider higher gain antenna",
                "Reduce distance or add relay node",
            ],
            confidence_base=0.75,
        ))

        # ===== RESOURCE RULES =====

        self.add_rule(DiagnosticRule(
            name="memory_pressure",
            pattern=r"(?i)(memory|ram|heap).*(low|pressure|warning|exhausted)",
            category=Category.RESOURCE,
            cause_template="System is running low on memory",
            suggestions=[
                "Check memory usage: free -h",
                "Identify memory-heavy processes: top -o %MEM",
                "Restart MeshForge to free memory",
                "Consider increasing system RAM",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="disk_space_low",
            pattern=r"(?i)(disk|storage|space).*(low|full|warning|<\d+%)",
            category=Category.RESOURCE,
            cause_template="Disk space is running low",
            suggestions=[
                "Check disk usage: df -h",
                "Clean log files: sudo journalctl --vacuum-time=7d",
                "Remove old message queue entries",
                "Check for large files: du -sh /* | sort -h",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="zombie_process",
            pattern=r"(?i)(zombie|defunct|orphan).*(process|pid)",
            category=Category.RESOURCE,
            cause_template="Zombie or defunct process detected",
            suggestions=[
                "Identify zombie processes: ps aux | grep Z",
                "Kill parent process to clean up zombies",
                "Restart affected service",
            ],
            auto_recoverable=True,
            recovery_action="Will attempt to clean up zombie processes",
            confidence_base=0.9,
        ))

        # ===== CONFIGURATION RULES =====

        self.add_rule(DiagnosticRule(
            name="config_file_missing",
            pattern=r"(?i)(config|configuration).*(missing|not found|does not exist)",
            category=Category.CONFIGURATION,
            cause_template="Configuration file is missing",
            suggestions=[
                "Run setup wizard: python3 src/setup_wizard.py",
                "Copy example config: cp config.example.json ~/.config/meshforge/config.json",
                "Check file permissions",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="invalid_config",
            pattern=r"(?i)(config|configuration).*(invalid|error|malformed|parse)",
            category=Category.CONFIGURATION,
            cause_template="Configuration file contains errors",
            suggestions=[
                "Validate JSON syntax: python3 -m json.tool < config.json",
                "Check for missing required fields",
                "Compare with example config",
                "Restore from backup if available",
            ],
            confidence_base=0.85,
        ))

    def add_rule(self, rule: DiagnosticRule) -> None:
        """Add a diagnostic rule."""
        self._rules.append(rule)

    def register_recovery_handler(self, action: str, handler: Callable) -> None:
        """Register a handler for auto-recovery actions."""
        self._recovery_handlers[action] = handler

    def report_symptom(self, message: str, category: Category = Category.CONNECTIVITY,
                       severity: Severity = Severity.WARNING,
                       context: Optional[Dict] = None, source: str = "") -> Optional[Diagnosis]:
        """
        Report a symptom and get diagnosis.

        Args:
            message: The symptom message
            category: Category of the symptom
            severity: Severity level
            context: Additional context (port numbers, node IDs, etc.)
            source: Source component

        Returns:
            Diagnosis if a matching rule was found
        """
        symptom = Symptom(
            message=message,
            category=category,
            severity=severity,
            context=context or {},
            source=source,
        )

        with self._lock:
            self._symptom_history.append(symptom)
            self._stats["symptoms_processed"] += 1

        return self.diagnose(symptom)

    def diagnose(self, symptom: Symptom) -> Optional[Diagnosis]:
        """
        Analyze a symptom and produce a diagnosis.

        Args:
            symptom: The symptom to diagnose

        Returns:
            Diagnosis with cause, evidence, and suggestions
        """
        # Find matching rules
        matching_rules = []
        for rule in self._rules:
            if rule.category == symptom.category or rule.category == Category.CONNECTIVITY:
                if re.search(rule.pattern, symptom.message, re.IGNORECASE):
                    matching_rules.append(rule)

        if not matching_rules:
            return None

        # Use the highest confidence matching rule
        best_rule = max(matching_rules, key=lambda r: r.confidence_base)

        # Build evidence
        evidence = []
        confidence = best_rule.confidence_base

        # Check evidence functions
        for check in best_rule.evidence_checks:
            try:
                result = check(symptom.context)
                if result:
                    evidence.append(result)
                    confidence = min(1.0, confidence + 0.05)
            except Exception as e:
                logger.debug(f"Evidence check failed: {e}")

        # Find related symptoms
        related = self._find_related_symptoms(symptom)
        if related:
            self._stats["correlations_found"] += 1
            confidence = min(1.0, confidence + 0.1 * len(related))

        # Build diagnosis
        diagnosis = Diagnosis(
            symptom=symptom,
            likely_cause=best_rule.cause_template,
            confidence=confidence,
            evidence=evidence,
            suggestions=list(best_rule.suggestions),
            related_symptoms=related,
            auto_recoverable=best_rule.auto_recoverable,
            recovery_action=best_rule.recovery_action,
            explanation=self._build_explanation(symptom, best_rule, related),
            expertise_level=best_rule.expertise_level,
        )

        with self._lock:
            self._diagnosis_history.append(diagnosis)
            self._stats["diagnoses_made"] += 1

        # Log the diagnosis
        logger.info(diagnosis.to_log_format())

        # Attempt auto-recovery if applicable
        if diagnosis.auto_recoverable and diagnosis.recovery_action:
            self._attempt_recovery(diagnosis)

        return diagnosis

    def _find_related_symptoms(self, symptom: Symptom) -> List[Symptom]:
        """Find symptoms related in time and category."""
        related = []
        cutoff = symptom.timestamp - self.CORRELATION_WINDOW

        with self._lock:
            for s in self._symptom_history:
                if s == symptom:
                    continue
                if s.timestamp >= cutoff:
                    # Same category or connectivity issues
                    if s.category == symptom.category or s.category == Category.CONNECTIVITY:
                        related.append(s)

        return related[:5]  # Limit to 5 most relevant

    def _build_explanation(self, symptom: Symptom, rule: DiagnosticRule,
                          related: List[Symptom]) -> str:
        """Build a human-readable explanation."""
        parts = [f"The symptom '{symptom.message}' indicates {rule.cause_template.lower()}."]

        if related:
            parts.append(f"This may be related to {len(related)} other recent issue(s).")

        if rule.suggestions:
            parts.append(f"The most likely fix is: {rule.suggestions[0]}")

        if rule.auto_recoverable:
            parts.append(f"MeshForge will automatically attempt recovery.")

        return " ".join(parts)

    def _attempt_recovery(self, diagnosis: Diagnosis) -> None:
        """Attempt automatic recovery for a diagnosis."""
        if diagnosis.recovery_action in self._recovery_handlers:
            try:
                handler = self._recovery_handlers[diagnosis.recovery_action]
                handler(diagnosis)
                self._stats["auto_recoveries"] += 1
                logger.info(f"Auto-recovery initiated: {diagnosis.recovery_action}")
            except Exception as e:
                logger.error(f"Auto-recovery failed: {e}")

    def get_recent_diagnoses(self, limit: int = 10,
                            category: Optional[Category] = None) -> List[Diagnosis]:
        """Get recent diagnoses, optionally filtered by category."""
        with self._lock:
            diagnoses = list(self._diagnosis_history)

        if category:
            diagnoses = [d for d in diagnoses if d.symptom.category == category]

        return diagnoses[-limit:]

    def get_health_summary(self) -> Dict[str, Any]:
        """Get a summary of system health based on recent symptoms."""
        with self._lock:
            recent = [s for s in self._symptom_history
                     if s.timestamp > datetime.now() - timedelta(hours=1)]

        # Count by category and severity
        by_category = {}
        by_severity = {}

        for s in recent:
            by_category[s.category.value] = by_category.get(s.category.value, 0) + 1
            by_severity[s.severity.value] = by_severity.get(s.severity.value, 0) + 1

        # Determine overall health
        critical_count = by_severity.get("critical", 0)
        error_count = by_severity.get("error", 0)
        warning_count = by_severity.get("warning", 0)

        if critical_count > 0:
            health = "critical"
        elif error_count > 2:
            health = "degraded"
        elif warning_count > 5:
            health = "warning"
        else:
            health = "healthy"

        return {
            "overall_health": health,
            "symptoms_last_hour": len(recent),
            "by_category": by_category,
            "by_severity": by_severity,
            "stats": dict(self._stats),
        }

    def get_stats(self) -> Dict[str, int]:
        """Get diagnostic engine statistics."""
        return dict(self._stats)


# Singleton instance
_engine: Optional[DiagnosticEngine] = None
_engine_lock = threading.Lock()


def get_diagnostic_engine() -> DiagnosticEngine:
    """Get the global diagnostic engine instance."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = DiagnosticEngine()
        return _engine


def diagnose(message: str, category: Category = Category.CONNECTIVITY,
             severity: Severity = Severity.WARNING,
             context: Optional[Dict] = None, source: str = "") -> Optional[Diagnosis]:
    """
    Convenience function to diagnose a symptom.

    Usage:
        from utils.diagnostic_engine import diagnose, Category, Severity

        diagnosis = diagnose(
            "Connection refused to meshtasticd",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR
        )
        if diagnosis:
            print(diagnosis.explanation)
    """
    engine = get_diagnostic_engine()
    return engine.report_symptom(message, category, severity, context, source)
