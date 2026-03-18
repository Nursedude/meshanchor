"""Resource diagnostic rules for MeshForge Diagnostic Engine."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.diagnostic_engine import DiagnosticEngine

from utils.diagnostic_engine import (
    Category,
    DiagnosticRule,
)


def load_resource_rules(engine: "DiagnosticEngine") -> None:
    """Load resource diagnostic rules."""

    engine.add_rule(DiagnosticRule(
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

    engine.add_rule(DiagnosticRule(
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

    engine.add_rule(DiagnosticRule(
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

    # ── Extended resource rules ──

    engine.add_rule(DiagnosticRule(
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

    engine.add_rule(DiagnosticRule(
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

    engine.add_rule(DiagnosticRule(
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

    engine.add_rule(DiagnosticRule(
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
