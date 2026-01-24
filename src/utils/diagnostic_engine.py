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
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Tuple
from collections import deque
from pathlib import Path
import threading

logger = logging.getLogger(__name__)

# Try to use centralized service checker
try:
    from utils.service_check import check_service, check_systemd_service as _check_systemd_service, ServiceState
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False


# =============================================================================
# EVIDENCE CHECK FUNCTIONS
# These functions verify actual system state and return evidence strings
# =============================================================================

def check_port_open(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """Check if a TCP port is open. Returns evidence string or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            if result == 0:
                return f"Port {port} on {host} is open and accepting connections"
            else:
                return None  # Port closed - no positive evidence
    except (socket.error, OSError):
        return None


def check_port_closed(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """Check if a TCP port is closed. Returns evidence string or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            if result != 0:
                return f"Port {port} on {host} is NOT accepting connections"
            else:
                return None  # Port open - no evidence of problem
    except (socket.error, OSError):
        return f"Port {port} on {host} is unreachable"


def check_process_running(process_name: str) -> Optional[str]:
    """Check if a process is running. Returns evidence string or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", process_name],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split('\n')
            return f"Process '{process_name}' is running (PID: {', '.join(pids)})"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def check_process_not_running(process_name: str) -> Optional[str]:
    """Check if a process is NOT running. Returns evidence string or None."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", process_name],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return f"Process '{process_name}' is NOT running"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return f"Could not verify process '{process_name}' status"


def check_systemd_service_active(service_name: str) -> Optional[str]:
    """Check if a systemd service is active. Returns evidence string or None."""
    try:
        # Use centralized service checker if available
        if _HAS_SERVICE_CHECK:
            is_running, is_enabled = _check_systemd_service(service_name)
            if is_running:
                return f"Systemd service '{service_name}' is active"
            return None
        else:
            # Fallback to direct systemctl call
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and "active" in result.stdout:
                return f"Systemd service '{service_name}' is active"
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def check_systemd_service_inactive(service_name: str) -> Optional[str]:
    """Check if a systemd service is inactive. Returns evidence string or None."""
    try:
        # Use centralized service checker if available
        if _HAS_SERVICE_CHECK:
            is_running, is_enabled = _check_systemd_service(service_name)
            if not is_running:
                return f"Systemd service '{service_name}' is NOT active"
            return None
        else:
            # Fallback to direct systemctl call
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0 or "inactive" in result.stdout:
                return f"Systemd service '{service_name}' is NOT active"
            return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return f"Systemd service '{service_name}' status unknown"


def check_file_exists(file_path: str) -> Optional[str]:
    """Check if a file exists. Returns evidence string or None."""
    path = Path(file_path)
    if path.exists():
        return f"Config file exists: {file_path}"
    return None


def check_file_missing(file_path: str) -> Optional[str]:
    """Check if a file is missing. Returns evidence string or None."""
    path = Path(file_path)
    if not path.exists():
        return f"Config file missing: {file_path}"
    return None


def check_serial_device_exists(device_pattern: str = "/dev/ttyUSB*") -> Optional[str]:
    """Check if any serial device exists. Returns evidence string or None."""
    from pathlib import Path
    devices = list(Path("/dev").glob(device_pattern.replace("/dev/", "")))
    devices.extend(list(Path("/dev").glob("ttyACM*")))
    if devices:
        return f"Serial devices found: {', '.join(str(d) for d in devices[:3])}"
    return None


def check_no_serial_device() -> Optional[str]:
    """Check if NO serial devices exist. Returns evidence string or None."""
    from pathlib import Path
    devices = list(Path("/dev").glob("ttyUSB*"))
    devices.extend(list(Path("/dev").glob("ttyACM*")))
    if not devices:
        return "No serial devices (/dev/ttyUSB*, /dev/ttyACM*) found"
    return None


def check_meshtasticd_clients() -> Optional[str]:
    """Check for other meshtastic clients that might be blocking connection."""
    try:
        result = subprocess.run(
            ["pgrep", "-af", "meshtastic|nomadnet"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            clients = result.stdout.strip().split('\n')
            return f"Found {len(clients)} potential Meshtastic client(s) running"
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def check_rns_config_exists() -> Optional[str]:
    """Check if RNS config exists at standard location."""
    from utils.paths import get_real_user_home
    config_path = get_real_user_home() / ".reticulum" / "config"
    if config_path.exists():
        return f"RNS config exists at {config_path}"
    return None


def check_rns_config_missing() -> Optional[str]:
    """Check if RNS config is missing."""
    from utils.paths import get_real_user_home
    config_path = get_real_user_home() / ".reticulum" / "config"
    if not config_path.exists():
        return f"RNS config missing at {config_path}"
    return None


# Evidence check factory functions (create checks with parameters)

def make_port_check(host: str, port: int) -> Callable[[Dict], Optional[str]]:
    """Factory: create a port open check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_port_open(host, port)
    return check


def make_port_closed_check(host: str, port: int) -> Callable[[Dict], Optional[str]]:
    """Factory: create a port closed check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_port_closed(host, port)
    return check


def make_process_check(process_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a process running check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_process_running(process_name)
    return check


def make_process_not_running_check(process_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a process NOT running check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_process_not_running(process_name)
    return check


def make_service_active_check(service_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a systemd service active check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_systemd_service_active(service_name)
    return check


def make_service_inactive_check(service_name: str) -> Callable[[Dict], Optional[str]]:
    """Factory: create a systemd service inactive check function."""
    def check(ctx: Dict) -> Optional[str]:
        return check_systemd_service_inactive(service_name)
    return check


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
    PREDICTIVE = "predictive"  # Sprint B: Proactive alerts from trend analysis


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
    - Persistent diagnostic history (SQLite)
    """

    # Symptom history retention
    HISTORY_MAX_SIZE = 1000
    HISTORY_MAX_AGE = timedelta(hours=24)

    # Correlation window for related symptoms
    CORRELATION_WINDOW = timedelta(minutes=5)

    def __init__(self, persist_history: bool = True):
        """Initialize the diagnostic engine.

        Args:
            persist_history: If True, save diagnoses to SQLite for history tracking
        """
        self._rules: List[DiagnosticRule] = []
        self._symptom_history: deque = deque(maxlen=self.HISTORY_MAX_SIZE)
        self._diagnosis_history: deque = deque(maxlen=500)
        self._lock = threading.Lock()
        self._persist_history = persist_history

        # Callbacks for auto-recovery
        self._recovery_handlers: Dict[str, Callable] = {}

        # Load built-in rules
        self._load_mesh_rules()

        # Initialize persistent storage
        if self._persist_history:
            self._init_db()

        # Stats
        self._stats = {
            "symptoms_processed": 0,
            "diagnoses_made": 0,
            "auto_recoveries": 0,
            "correlations_found": 0,
        }

    def _get_db_path(self) -> Path:
        """Get path to diagnostic history database."""
        from utils.paths import get_real_user_home
        db_dir = get_real_user_home() / ".config" / "meshforge"
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "diagnostic_history.db"

    def _init_db(self):
        """Initialize SQLite database for persistent history."""
        import sqlite3
        try:
            db_path = self._get_db_path()
            conn = sqlite3.connect(str(db_path))
            conn.execute('''
                CREATE TABLE IF NOT EXISTS diagnoses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    symptom_message TEXT NOT NULL,
                    symptom_category TEXT NOT NULL,
                    symptom_severity TEXT NOT NULL,
                    symptom_source TEXT,
                    likely_cause TEXT NOT NULL,
                    confidence REAL,
                    evidence TEXT,
                    suggestions TEXT,
                    auto_recoverable BOOLEAN,
                    rule_name TEXT
                )
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_diagnoses_timestamp
                ON diagnoses(timestamp DESC)
            ''')
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_diagnoses_category
                ON diagnoses(symptom_category)
            ''')
            conn.commit()
            conn.close()
            logger.debug(f"Diagnostic history DB initialized at {db_path}")
        except Exception as e:
            logger.warning(f"Failed to initialize diagnostic history DB: {e}")
            self._persist_history = False

    def _save_diagnosis(self, symptom: 'Symptom', diagnosis: 'Diagnosis', rule_name: str = ""):
        """Save a diagnosis to persistent storage."""
        if not self._persist_history:
            return

        import sqlite3
        import json
        try:
            conn = sqlite3.connect(str(self._get_db_path()))
            conn.execute('''
                INSERT INTO diagnoses
                (symptom_message, symptom_category, symptom_severity, symptom_source,
                 likely_cause, confidence, evidence, suggestions, auto_recoverable, rule_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                symptom.message,
                symptom.category.value,
                symptom.severity.value,
                symptom.source,
                diagnosis.likely_cause,
                diagnosis.confidence,
                json.dumps(diagnosis.evidence),
                json.dumps(diagnosis.suggestions),
                diagnosis.auto_recoverable,
                rule_name,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"Failed to save diagnosis: {e}")

    def get_history(self, limit: int = 50, category: Optional[Category] = None,
                    since_hours: int = 24) -> List[Dict]:
        """
        Get recent diagnostic history.

        Args:
            limit: Maximum number of diagnoses to return
            category: Filter by category (None = all)
            since_hours: Only return diagnoses from last N hours

        Returns:
            List of diagnosis records as dicts
        """
        if not self._persist_history:
            return []

        import sqlite3
        import json
        try:
            conn = sqlite3.connect(str(self._get_db_path()))
            conn.row_factory = sqlite3.Row

            query = '''
                SELECT * FROM diagnoses
                WHERE timestamp > datetime('now', ? || ' hours')
            '''
            params = [f'-{since_hours}']

            if category:
                query += " AND symptom_category = ?"
                params.append(category.value)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                results.append({
                    'id': row['id'],
                    'timestamp': row['timestamp'],
                    'symptom_message': row['symptom_message'],
                    'symptom_category': row['symptom_category'],
                    'symptom_severity': row['symptom_severity'],
                    'symptom_source': row['symptom_source'],
                    'likely_cause': row['likely_cause'],
                    'confidence': row['confidence'],
                    'evidence': json.loads(row['evidence']) if row['evidence'] else [],
                    'suggestions': json.loads(row['suggestions']) if row['suggestions'] else [],
                    'auto_recoverable': bool(row['auto_recoverable']),
                    'rule_name': row['rule_name'],
                })
            return results
        except Exception as e:
            logger.warning(f"Failed to get diagnostic history: {e}")
            return []

    def get_recurring_issues(self, threshold: int = 3, hours: int = 24) -> List[Dict]:
        """
        Find recurring issues (same symptom/cause appearing multiple times).

        Args:
            threshold: Minimum occurrences to be considered recurring
            hours: Time window to search

        Returns:
            List of recurring issues with count
        """
        if not self._persist_history:
            return []

        import sqlite3
        try:
            conn = sqlite3.connect(str(self._get_db_path()))
            conn.row_factory = sqlite3.Row

            cursor = conn.execute('''
                SELECT
                    likely_cause,
                    symptom_category,
                    COUNT(*) as occurrence_count,
                    MAX(timestamp) as last_seen,
                    MIN(timestamp) as first_seen
                FROM diagnoses
                WHERE timestamp > datetime('now', ? || ' hours')
                GROUP BY likely_cause, symptom_category
                HAVING COUNT(*) >= ?
                ORDER BY occurrence_count DESC
            ''', (f'-{hours}', threshold))

            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    'likely_cause': row['likely_cause'],
                    'category': row['symptom_category'],
                    'count': row['occurrence_count'],
                    'first_seen': row['first_seen'],
                    'last_seen': row['last_seen'],
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning(f"Failed to get recurring issues: {e}")
            return []

    def clear_history(self, older_than_days: int = 30) -> int:
        """
        Clear old diagnostic history.

        Args:
            older_than_days: Delete records older than this

        Returns:
            Number of records deleted
        """
        if not self._persist_history:
            return 0

        import sqlite3
        try:
            conn = sqlite3.connect(str(self._get_db_path()))
            cursor = conn.execute('''
                DELETE FROM diagnoses
                WHERE timestamp < datetime('now', ? || ' days')
            ''', (f'-{older_than_days}',))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            return deleted
        except Exception as e:
            logger.warning(f"Failed to clear history: {e}")
            return 0

    def _load_mesh_rules(self) -> None:
        """Load built-in diagnostic rules for mesh networking."""

        # ===== CONNECTIVITY RULES =====

        self.add_rule(DiagnosticRule(
            name="meshtasticd_connection_refused",
            pattern=r"(?i)connection\s+(refused|rejected).*meshtastic",
            category=Category.CONNECTIVITY,
            cause_template="Another client is likely connected to meshtasticd (single-client limitation)",
            evidence_checks=[
                make_port_check("localhost", 4403),  # Verify port is actually open
                lambda ctx: check_meshtasticd_clients(),  # Check for other clients
                make_service_active_check("meshtasticd"),  # Verify service running
            ],
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
            evidence_checks=[
                make_port_closed_check("localhost", 4403),  # Confirm port is closed
                make_service_inactive_check("meshtasticd"),  # Confirm service not running
                make_process_not_running_check("meshtasticd"),  # Confirm process not running
            ],
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
            evidence_checks=[
                make_service_inactive_check("rnsd"),  # Check rnsd not running
                lambda ctx: check_rns_config_missing(),  # Check config missing
                lambda ctx: check_no_serial_device(),  # Check for serial devices
            ],
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
            evidence_checks=[
                lambda ctx: check_serial_device_exists(),  # Verify device exists
                make_process_check("meshtasticd"),  # meshtasticd might be using it
            ],
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
            evidence_checks=[
                lambda ctx: check_no_serial_device(),  # Verify no serial devices
            ],
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

        self.add_rule(DiagnosticRule(
            name="permission_denied",
            pattern=r"(?i)(permission|access).*(denied|forbidden|not allowed)",
            category=Category.CONFIGURATION,
            cause_template="Insufficient permissions to access resource",
            suggestions=[
                "Check file/device permissions: ls -la <path>",
                "Run with sudo for privileged operations",
                "Add user to required groups: sudo usermod -aG dialout,plugdev $USER",
                "Check SELinux/AppArmor if applicable",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="port_conflict",
            pattern=r"(?i)(port|address).*(in use|already|occupied|bind.*fail)",
            category=Category.CONFIGURATION,
            cause_template="Network port is already in use by another process",
            suggestions=[
                "Find process using port: sudo lsof -i :<port>",
                "Kill conflicting process or use different port",
                "Check for duplicate service instances",
                "Verify port configuration in settings",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="invalid_frequency_setting",
            pattern=r"(?i)(frequency|freq|region).*(invalid|out of range|illegal|not allowed)",
            category=Category.CONFIGURATION,
            cause_template="Radio frequency or region setting is invalid for the hardware/location",
            suggestions=[
                "Check region setting matches your location: meshtastic --get lora.region",
                "Valid regions: US, EU_868, CN, JP, ANZ, KR, TW, RU, IN, NZ_865, TH",
                "Reset to correct region: meshtastic --set lora.region US",
                "Verify antenna matches frequency band",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="duplicate_node_id",
            pattern=r"(?i)(node|device).*(duplicate|conflict|already exists|collision).*id",
            category=Category.CONFIGURATION,
            cause_template="Two or more nodes share the same ID, causing routing conflicts",
            suggestions=[
                "Factory reset the duplicate node: meshtastic --factory-reset",
                "Each node must have unique hardware ID",
                "Check mesh for duplicates: meshtastic --nodes",
                "Firmware reflash may regenerate ID",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="wrong_modem_preset",
            pattern=r"(?i)(modem|preset|lora).*(wrong|mismatch|incompatible|different)",
            category=Category.CONFIGURATION,
            cause_template="Nodes are using different LoRa modem presets and cannot communicate",
            suggestions=[
                "All nodes on same channel must use same preset",
                "Check current preset: meshtastic --get lora.modem_preset",
                "Set matching preset: meshtastic --set lora.modem_preset LONG_FAST",
                "Available: LONG_FAST, LONG_SLOW, MEDIUM_FAST, SHORT_FAST, etc.",
            ],
            confidence_base=0.9,
        ))

        # ===== CONNECTIVITY RULES (EXTENDED) =====

        self.add_rule(DiagnosticRule(
            name="dns_resolution_failed",
            pattern=r"(?i)(dns|resolve|lookup|hostname).*(failed|error|timeout|not found)",
            category=Category.CONNECTIVITY,
            cause_template="DNS resolution failed — cannot resolve hostname",
            suggestions=[
                "Check internet connectivity: ping 8.8.8.8",
                "Verify DNS settings: cat /etc/resolv.conf",
                "Try alternative DNS: echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf",
                "Check if running in offline/air-gapped mode",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="network_interface_down",
            pattern=r"(?i)(interface|eth|wlan|wifi|network).*(down|no carrier|link.*lost|disconnected)",
            category=Category.CONNECTIVITY,
            cause_template="Network interface is down or has no link",
            suggestions=[
                "Check interface status: ip link show",
                "Bring interface up: sudo ip link set <iface> up",
                "Check physical cable connection",
                "For WiFi: nmcli device wifi connect <SSID>",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="mqtt_subscription_lost",
            pattern=r"(?i)mqtt.*(subscription|subscribe).*(lost|failed|disconnect|error)",
            category=Category.CONNECTIVITY,
            cause_template="MQTT topic subscription was lost, no longer receiving messages",
            suggestions=[
                "Reconnect to broker and resubscribe",
                "Check broker logs for disconnect reason",
                "Verify topic permissions on broker",
                "Check for client ID conflicts (two clients with same ID)",
            ],
            auto_recoverable=True,
            recovery_action="Will resubscribe to topics on reconnect",
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="rns_path_not_found",
            pattern=r"(?i)(rns|reticulum).*(path|route|destination).*(not found|unknown|no path)",
            category=Category.CONNECTIVITY,
            cause_template="No RNS path exists to the destination — node may be unreachable",
            suggestions=[
                "Check if destination is announcing: rnpath <destination_hash>",
                "Verify transport nodes are online",
                "Wait for path discovery (can take minutes on mesh)",
                "Check if destination has changed identity",
            ],
            confidence_base=0.75,
        ))

        self.add_rule(DiagnosticRule(
            name="tcp_handshake_timeout",
            pattern=r"(?i)(tcp|connection).*(handshake|establish).*(timeout|timed out|slow)",
            category=Category.CONNECTIVITY,
            cause_template="TCP connection handshake is timing out — remote host unreachable or slow",
            suggestions=[
                "Check if remote host is reachable: ping <host>",
                "Verify firewall rules: sudo iptables -L",
                "Check if service is listening on remote: nc -zv <host> <port>",
                "Increase connection timeout if on slow network",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="bridge_reconnecting",
            pattern=r"(?i)(bridge|gateway).*(reconnect|retry|backoff|attempt \d+)",
            category=Category.CONNECTIVITY,
            cause_template="Bridge is repeatedly reconnecting, indicating unstable connection",
            suggestions=[
                "Check underlying transport (serial, TCP, MQTT) stability",
                "Review bridge health metrics for error patterns",
                "Check for resource exhaustion on bridge host",
                "Verify both endpoints are stable and responsive",
            ],
            auto_recoverable=True,
            recovery_action="Exponential backoff reconnection in progress",
            confidence_base=0.7,
        ))

        self.add_rule(DiagnosticRule(
            name="websocket_disconnected",
            pattern=r"(?i)(websocket|ws).*(disconnect|closed|error|failed)",
            category=Category.CONNECTIVITY,
            cause_template="WebSocket connection was closed unexpectedly",
            suggestions=[
                "Check if server process is still running",
                "Verify network stability between client and server",
                "Check for proxy/firewall WebSocket blocking",
                "Review server logs for disconnect reason",
            ],
            auto_recoverable=True,
            recovery_action="Will attempt WebSocket reconnection",
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="api_endpoint_unreachable",
            pattern=r"(?i)(api|endpoint|http).*(unreachable|503|502|504|unavailable)",
            category=Category.CONNECTIVITY,
            cause_template="API endpoint is unreachable or returning server errors",
            suggestions=[
                "Check if API service is running",
                "Verify network connectivity to API host",
                "Check for rate limiting (429 responses)",
                "Try alternative endpoint or fallback",
            ],
            auto_recoverable=True,
            recovery_action="Will retry with exponential backoff",
            confidence_base=0.8,
        ))

        # ===== HARDWARE RULES (EXTENDED) =====

        self.add_rule(DiagnosticRule(
            name="usb_power_insufficient",
            pattern=r"(?i)(usb|power).*(insufficient|undervolt|brownout|over.?current)",
            category=Category.HARDWARE,
            cause_template="USB port is not providing sufficient power to the device",
            suggestions=[
                "Use a powered USB hub",
                "Try a different USB port (rear ports often have more power)",
                "Use a shorter, higher-quality USB cable",
                "Check dmesg for USB power warnings: dmesg | grep -i 'over-current'",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="gps_lock_lost",
            pattern=r"(?i)(gps|gnss|position|location).*(lost|no fix|no lock|timeout|unavailable)",
            category=Category.HARDWARE,
            cause_template="GPS receiver has lost satellite lock",
            suggestions=[
                "Ensure clear view of sky (GPS needs satellite visibility)",
                "Check GPS antenna connection",
                "Cold start may take 2-15 minutes for first fix",
                "Verify GPS is enabled: meshtastic --get position.gps_enabled",
            ],
            confidence_base=0.75,
        ))

        self.add_rule(DiagnosticRule(
            name="radio_reset_detected",
            pattern=r"(?i)(radio|lora|sx127|sx126|chip).*(reset|reboot|reinit|watchdog)",
            category=Category.HARDWARE,
            cause_template="Radio chip has reset unexpectedly — possible power or hardware issue",
            suggestions=[
                "Check power supply stability",
                "Verify SPI/I2C connections to radio chip",
                "Check for overheating (feel the device)",
                "Update firmware — may be a known chip driver bug",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="battery_low",
            pattern=r"(?i)(battery|batt|power).*(low|critical|<\s*2[0-9]%|dying|shutdown)",
            category=Category.HARDWARE,
            cause_template="Device battery is critically low",
            suggestions=[
                "Connect to power source immediately",
                "Enable power-saving mode to extend life",
                "Check charging circuit if connected but not charging",
                "Consider solar panel for remote deployments",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="overheating",
            pattern=r"(?i)(temperature|thermal|heat|overheat).*(high|warning|critical|throttl)",
            category=Category.HARDWARE,
            cause_template="Device is overheating — may throttle or shut down",
            suggestions=[
                "Move device to shaded/ventilated location",
                "Reduce TX power to lower heat generation",
                "Check for direct sunlight exposure",
                "Add heatsink or ventilation to enclosure",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="spi_bus_error",
            pattern=r"(?i)(spi|i2c|bus).*(error|timeout|nak|collision|stuck)",
            category=Category.HARDWARE,
            cause_template="Communication bus error between processor and peripheral",
            suggestions=[
                "Check wiring/connections to peripheral",
                "Verify bus clock speed is within spec",
                "Check for bus contention (multiple devices on same bus)",
                "Power cycle the device",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="firmware_flash_failed",
            pattern=r"(?i)(firmware|flash|update|ota).*(failed|error|abort|corrupt|verify)",
            category=Category.HARDWARE,
            cause_template="Firmware update/flash operation failed",
            suggestions=[
                "Do NOT power off device — retry the flash",
                "Use wired connection (USB) instead of OTA for reliability",
                "Verify firmware file integrity (checksum)",
                "Try recovery/DFU mode if device is bricked",
            ],
            confidence_base=0.9,
        ))

        # ===== PROTOCOL RULES (EXTENDED) =====

        self.add_rule(DiagnosticRule(
            name="channel_config_mismatch",
            pattern=r"(?i)(channel|freq|frequency).*(mismatch|wrong|different|hop)",
            category=Category.PROTOCOL,
            cause_template="Nodes are on different channels or frequencies",
            suggestions=[
                "Verify all nodes use same channel config",
                "Check channel index: meshtastic --ch-index 0 --info",
                "Share channel via QR code or URL for consistency",
                "Check for accidental frequency offset",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="crc_error_high",
            pattern=r"(?i)(crc|checksum|integrity).*(error|fail|mismatch|corrupt|high rate)",
            category=Category.PROTOCOL,
            cause_template="High CRC error rate indicating RF interference or hardware issue",
            suggestions=[
                "Check for nearby RF interference sources",
                "Verify antenna connection (loose SMA = high CRC)",
                "Try different frequency/channel to avoid interference",
                "Check if nodes are too close (receiver saturation)",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="mesh_routing_loop",
            pattern=r"(?i)(routing|route|mesh).*(loop|circular|infinite|ttl.*expir)",
            category=Category.PROTOCOL,
            cause_template="Message routing loop detected — packets circling without delivery",
            suggestions=[
                "Check hop limit settings (default 3, max 7)",
                "Verify no duplicate node IDs in mesh",
                "Reboot nodes to clear stale routing tables",
                "Check for misconfigured relay/router roles",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="beacon_timeout",
            pattern=r"(?i)(beacon|heartbeat|keepalive|announce).*(timeout|missed|expired|lost)",
            category=Category.PROTOCOL,
            cause_template="Node beacon/heartbeat not received — node may be offline or out of range",
            suggestions=[
                "Check if node is powered on and operational",
                "Verify RF path between nodes",
                "Node may have moved out of range",
                "Check node's configured beacon interval",
            ],
            confidence_base=0.7,
        ))

        self.add_rule(DiagnosticRule(
            name="identity_collision",
            pattern=r"(?i)(identity|hash|address).*(collision|duplicate|conflict)",
            category=Category.PROTOCOL,
            cause_template="Identity hash collision — two nodes resolving to same address",
            suggestions=[
                "This is extremely rare — verify it's a real collision",
                "One node should regenerate identity",
                "For RNS: delete identity file and restart",
                "Check for cloned devices (same keys on multiple nodes)",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="lxmf_delivery_failed",
            pattern=r"(?i)(lxmf|message|delivery).*(failed|timeout|undelivered|expired)",
            category=Category.PROTOCOL,
            cause_template="LXMF message delivery failed — destination unreachable or not accepting",
            suggestions=[
                "Check if destination node is online and reachable",
                "Verify LXMF propagation node is available",
                "Message may be queued for later delivery",
                "Check message size (large messages need more airtime)",
            ],
            auto_recoverable=True,
            recovery_action="Message queued for retry on next contact",
            confidence_base=0.75,
        ))

        self.add_rule(DiagnosticRule(
            name="packet_decode_error",
            pattern=r"(?i)(packet|frame|message).*(decode|deseriali|unmarshal|parse).*(error|fail)",
            category=Category.PROTOCOL,
            cause_template="Received packet could not be decoded — possible version mismatch or corruption",
            suggestions=[
                "Check firmware versions match across nodes",
                "Verify encryption keys are synchronized",
                "May be interference corrupting packets",
                "Check for mixed Meshtastic protocol versions",
            ],
            confidence_base=0.75,
        ))

        # ===== PERFORMANCE RULES (EXTENDED) =====

        self.add_rule(DiagnosticRule(
            name="high_packet_loss",
            pattern=r"(?i)(packet|message).*(loss|drop|lost|missing).*(high|>\s*[2-9]\d%|\d{2,3}\s*%)",
            category=Category.PERFORMANCE,
            cause_template="High packet loss rate indicating poor RF link or congestion",
            suggestions=[
                "Check signal strength (SNR should be > -10 dB for reliable links)",
                "Improve antenna height or orientation",
                "Reduce message rate to decrease channel congestion",
                "Consider switching to a longer-range LoRa preset",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="latency_spike",
            pattern=r"(?i)(latency|delay|response time).*(spike|high|excessive|>\s*\d+\s*s)",
            category=Category.PERFORMANCE,
            cause_template="Network latency has spiked — congestion or multi-hop delay",
            suggestions=[
                "Check channel utilization (congestion causes queueing delay)",
                "Reduce hop count if possible (each hop adds ~50-200ms)",
                "Check if relay nodes are overloaded",
                "Consider faster modem preset for lower latency",
            ],
            confidence_base=0.75,
        ))

        self.add_rule(DiagnosticRule(
            name="tx_duty_cycle_exceeded",
            pattern=r"(?i)(duty cycle|tx time|transmit).*(exceed|limit|restrict|legal|regulat)",
            category=Category.PERFORMANCE,
            cause_template="Transmit duty cycle limit exceeded — legally required quiet period",
            suggestions=[
                "Reduce message frequency",
                "Use shorter messages to reduce airtime",
                "Switch to faster modem preset (less airtime per message)",
                "Duty cycle limits are regulatory — cannot be bypassed",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="hop_count_excessive",
            pattern=r"(?i)(hop|relay).*(count|limit).*(exceed|too many|max|>\s*[4-7])",
            category=Category.PERFORMANCE,
            cause_template="Message exceeding practical hop limit — each hop degrades reliability",
            suggestions=[
                "Add relay node closer to destination to reduce hop count",
                "Use longer-range preset to skip intermediate hops",
                "Consider infrastructure node placement (elevated, powered)",
                "Maximum reliable hops is typically 3-4 for Meshtastic",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="retransmission_high",
            pattern=r"(?i)(retransmit|retry|resend).*(rate|count|high|excessive|>\s*\d+)",
            category=Category.PERFORMANCE,
            cause_template="High retransmission rate — ACKs not being received",
            suggestions=[
                "Poor link quality causing lost ACKs — improve antenna",
                "Hidden node problem — nodes can hear base but not each other",
                "Reduce traffic to decrease collision probability",
                "Check if destination node is processing messages fast enough",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="airtime_limit_approaching",
            pattern=r"(?i)(airtime|air time).*(limit|approach|warning|>\s*[5-9]\d%)",
            category=Category.PERFORMANCE,
            cause_template="Channel airtime limit is approaching — risk of message drops",
            suggestions=[
                "Reduce message rate across all nodes on this channel",
                "Switch some nodes to a different channel",
                "Use shorter messages",
                "Consider faster LoRa preset to reduce per-message airtime",
            ],
            confidence_base=0.8,
        ))

        # ===== RESOURCE RULES (EXTENDED) =====

        self.add_rule(DiagnosticRule(
            name="cpu_overload",
            pattern=r"(?i)(cpu|processor|load).*(high|overload|100%|maxed|throttl)",
            category=Category.RESOURCE,
            cause_template="CPU is overloaded, may cause dropped messages or slow responses",
            suggestions=[
                "Check running processes: top -bn1 | head -20",
                "Identify heavy processes: ps aux --sort=-%cpu | head -10",
                "Reduce monitoring frequency if applicable",
                "Check for runaway processes or infinite loops",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="file_descriptor_limit",
            pattern=r"(?i)(file descriptor|fd|too many open|ulimit|EMFILE|ENFILE)",
            category=Category.RESOURCE,
            cause_template="File descriptor limit reached — cannot open new files or sockets",
            suggestions=[
                "Check current limits: ulimit -n",
                "Increase limit: ulimit -n 65536",
                "Find FD-heavy processes: ls -la /proc/*/fd 2>/dev/null | wc -l",
                "Check for connection leaks in application code",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="database_corruption",
            pattern=r"(?i)(database|db|sqlite).*(corrupt|malformed|integrity|damaged)",
            category=Category.RESOURCE,
            cause_template="Database file is corrupted — data may be lost",
            suggestions=[
                "Try SQLite integrity check: sqlite3 <db> 'PRAGMA integrity_check'",
                "Restore from backup if available",
                "Delete and recreate database (data loss)",
                "Check for incomplete writes (power loss during write)",
            ],
            confidence_base=0.9,
        ))

        self.add_rule(DiagnosticRule(
            name="log_file_too_large",
            pattern=r"(?i)(log|journal).*(large|size|growing|full|>\s*\d+\s*[GM]B)",
            category=Category.RESOURCE,
            cause_template="Log files consuming excessive disk space",
            suggestions=[
                "Rotate logs: sudo journalctl --vacuum-size=100M",
                "Set log retention: sudo journalctl --vacuum-time=7d",
                "Check for excessive debug logging",
                "Configure logrotate for application logs",
            ],
            confidence_base=0.8,
        ))

        # ===== SECURITY RULES =====

        self.add_rule(DiagnosticRule(
            name="unauthorized_access_attempt",
            pattern=r"(?i)(unauthorized|auth|login).*(attempt|failed|denied|invalid|rejected)",
            category=Category.SECURITY,
            cause_template="Unauthorized access attempt detected",
            suggestions=[
                "Review access logs for source of attempts",
                "Verify credentials are correct",
                "Check for brute-force patterns",
                "Consider enabling fail2ban if repeated",
            ],
            confidence_base=0.85,
        ))

        self.add_rule(DiagnosticRule(
            name="rogue_node_detected",
            pattern=r"(?i)(rogue|unknown|unexpected|foreign).*(node|device|station).*detect",
            category=Category.SECURITY,
            cause_template="Unknown node detected on mesh network — may be unauthorized",
            suggestions=[
                "Verify node ID against known device inventory",
                "Check if new node was authorized by network admin",
                "Enable encryption on all channels",
                "Monitor node for suspicious activity patterns",
            ],
            confidence_base=0.7,
        ))

        self.add_rule(DiagnosticRule(
            name="key_rotation_needed",
            pattern=r"(?i)(key|certificate|credential).*(expir|rotat|old|stale|renew)",
            category=Category.SECURITY,
            cause_template="Encryption keys or certificates need rotation",
            suggestions=[
                "Rotate channel encryption keys on all nodes",
                "Update TLS certificates before expiry",
                "Regenerate RNS identity if compromised",
                "Document new key distribution to all operators",
            ],
            confidence_base=0.8,
        ))

        self.add_rule(DiagnosticRule(
            name="insecure_channel_detected",
            pattern=r"(?i)(channel|link).*(unencrypted|insecure|plaintext|no.*encrypt)",
            category=Category.SECURITY,
            cause_template="Communication channel is not encrypted — messages visible to anyone",
            suggestions=[
                "Enable encryption: meshtastic --ch-set psk random",
                "Use AES256 for sensitive communications",
                "Share keys only through secure channels",
                "Default PSK 'AQ==' is publicly known — always change it",
            ],
            confidence_base=0.9,
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
        # Find matching rules (match by category only)
        matching_rules = []
        for rule in self._rules:
            if rule.category == symptom.category:
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

        # Save to persistent history
        self._save_diagnosis(symptom, diagnosis, rule_name=best_rule.name)

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
                    # Same category symptoms are related
                    if s.category == symptom.category:
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

    def check_predictive_alerts(self) -> List[Diagnosis]:
        """
        Check for predictive alerts from analytics data.

        This integrates the PredictiveAnalyzer to proactively detect
        network health issues before they become critical.

        Returns:
            List of Diagnosis objects for any predicted issues

        API Contract:
            - Returns list (may be empty if no predictions or insufficient data)
            - Thread-safe
            - Does not raise exceptions (logs failures)
            - Tests: tests/test_predictive_analytics.py
        """
        diagnoses: List[Diagnosis] = []

        try:
            # Import here to avoid circular imports
            from utils.analytics import get_predictive_analyzer, PredictiveAlert
            analyzer = get_predictive_analyzer()
            alerts = analyzer.analyze_all()

            for alert in alerts:
                # Convert PredictiveAlert to Diagnosis
                severity = {
                    'info': Severity.INFO,
                    'warning': Severity.WARNING,
                    'critical': Severity.CRITICAL,
                }.get(alert.severity, Severity.WARNING)

                symptom = Symptom(
                    message=alert.message,
                    category=Category.PREDICTIVE,
                    severity=severity,
                    context={
                        'alert_type': alert.alert_type,
                        'predicted_time_hours': alert.predicted_time_hours,
                        'affected_nodes': alert.affected_nodes,
                    },
                    source='predictive_analyzer',
                )

                # Build cause based on alert type
                cause_map = {
                    'link_snr_degradation': "Link signal quality is degrading over time",
                    'link_packet_loss': "Link experiencing significant packet loss",
                    'metric_critical': "Network metric has reached critical threshold",
                    'metric_degradation': "Network metric showing degradation trend",
                    'node_count_decline': "Network losing active nodes",
                }
                cause = cause_map.get(alert.alert_type, "Predicted network issue detected")

                # Add time prediction to explanation if available
                explanation = f"PREDICTIVE ALERT: {alert.message}"
                if alert.predicted_time_hours:
                    if alert.predicted_time_hours < 24:
                        explanation += f" Expected to reach critical in ~{alert.predicted_time_hours:.0f} hours."
                    else:
                        days = alert.predicted_time_hours / 24
                        explanation += f" Expected to reach critical in ~{days:.1f} days."

                diagnosis = Diagnosis(
                    symptom=symptom,
                    likely_cause=cause,
                    confidence=alert.confidence,
                    evidence=alert.evidence,
                    suggestions=alert.suggestions,
                    related_symptoms=[],
                    auto_recoverable=False,
                    recovery_action=None,
                    explanation=explanation,
                    expertise_level="intermediate",
                )

                diagnoses.append(diagnosis)

                # Save to persistent history
                self._save_diagnosis(symptom, diagnosis, rule_name=f"predictive_{alert.alert_type}")

                # Log the predictive alert
                logger.info(f"[PREDICTIVE] {diagnosis.to_log_format()}")

        except ImportError:
            logger.debug("Predictive analytics not available")
        except Exception as e:
            logger.warning(f"Failed to check predictive alerts: {e}")

        return diagnoses

    def get_network_forecast(self, hours_ahead: int = 24) -> Dict[str, Any]:
        """
        Get a network health forecast.

        Args:
            hours_ahead: How many hours to forecast

        Returns:
            Dict with forecast data or error info
        """
        try:
            from utils.analytics import get_predictive_analyzer
            analyzer = get_predictive_analyzer()
            return analyzer.get_network_forecast(hours_ahead)
        except ImportError:
            return {'has_forecast': False, 'reason': 'Analytics module not available'}
        except Exception as e:
            return {'has_forecast': False, 'reason': str(e)}


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

    API Contract:
        - Returns Diagnosis object if symptom matched, None otherwise
        - Callers MUST check 'if diagnosis:' before accessing attributes
        - Diagnosis.likely_cause: str explaining the probable cause
        - Diagnosis.suggestions: List[str] of actionable fixes (may be empty)
        - Diagnosis.auto_recovery: Optional[str] recovery action
        - Thread-safe (uses singleton engine)
        - Tests: tests/test_diagnostics.py
    """
    engine = get_diagnostic_engine()
    return engine.report_symptom(message, category, severity, context, source)
