"""
MeshForge Logging Configuration

Provides centralized logging setup for consistent log formatting
and configuration across the application.

Designed for Raspberry Pi deployments with journalctl integration:
    - Logs to journald when available (viewable via: journalctl -t meshforge)
    - Falls back to file/console logging
    - Structured logging for easy parsing

Usage:
    from utils.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Message")

Or for module-level configuration:
    from utils.logging_config import setup_logging
    setup_logging(level=logging.DEBUG, log_file="/var/log/meshforge.log")

View logs on RPi:
    journalctl -t meshforge -f          # Follow live logs
    journalctl -t meshforge --since today
    journalctl -t meshforge -p err      # Errors only
"""

import logging
import logging.handlers
import sys
import os
from pathlib import Path
from typing import Optional, List, Callable
import threading
from datetime import datetime

# Thread-safe initialization
_initialized = False
_lock = threading.Lock()

# Default format
DEFAULT_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
SIMPLE_FORMAT = "%(levelname)s: %(message)s"
DEBUG_FORMAT = "%(asctime)s | %(name)s:%(lineno)d | %(levelname)s | %(message)s"

# Color codes for terminal (if coloredlogs not available)
LEVEL_COLORS = {
    'DEBUG': '\033[36m',     # Cyan
    'INFO': '\033[32m',      # Green
    'WARNING': '\033[33m',   # Yellow
    'ERROR': '\033[31m',     # Red
    'CRITICAL': '\033[35m',  # Magenta
}
RESET = '\033[0m'


class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to log levels for terminal output."""

    def __init__(self, fmt=None, datefmt=None, use_colors=True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record):
        if self.use_colors:
            levelname = record.levelname
            if levelname in LEVEL_COLORS:
                record.levelname = f"{LEVEL_COLORS[levelname]}{levelname}{RESET}"
        return super().format(record)


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    log_format: str = DEFAULT_FORMAT,
    use_colors: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    suppress_libs: bool = True,
) -> None:
    """
    Configure the root logger with consistent settings.

    Args:
        level: Logging level (default INFO)
        log_file: Optional file path for logging
        log_format: Log message format string
        use_colors: Enable colored output in terminal
        max_bytes: Max log file size before rotation
        backup_count: Number of backup files to keep
        suppress_libs: Suppress noisy third-party loggers
    """
    global _initialized

    with _lock:
        if _initialized:
            return

        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # Remove existing handlers
        root_logger.handlers.clear()

        # Console handler with colors
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)

        if use_colors:
            console_formatter = ColoredFormatter(log_format)
        else:
            console_formatter = logging.Formatter(log_format)

        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

        # File handler if specified
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(log_format))
            root_logger.addHandler(file_handler)

        # Suppress noisy third-party loggers
        if suppress_libs:
            for lib_name in [
                'urllib3',
                'requests',
                'meshtastic',
                'serial',
                'asyncio',
                'PIL',
            ]:
                logging.getLogger(lib_name).setLevel(logging.WARNING)

        _initialized = True


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance with the given name.

    This ensures logging is configured before returning the logger.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured Logger instance
    """
    # Ensure basic setup
    if not _initialized:
        setup_logging()

    return logging.getLogger(name)


def set_level(level: int, logger_name: str = None) -> None:
    """
    Set logging level for a specific logger or root logger.

    Args:
        level: Logging level
        logger_name: Optional specific logger name
    """
    if logger_name:
        logging.getLogger(logger_name).setLevel(level)
    else:
        logging.getLogger().setLevel(level)


def enable_debug(logger_name: str = None) -> None:
    """Enable debug logging"""
    set_level(logging.DEBUG, logger_name)


def suppress_logger(logger_name: str) -> None:
    """Suppress a specific logger to WARNING level"""
    logging.getLogger(logger_name).setLevel(logging.WARNING)


# Convenience aliases
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


# ============================================================================
# Journald Integration (for RPi/systemd deployments)
# ============================================================================

_journald_available = False
_JournaldLogHandler = None

try:
    from systemd.journal import JournalHandler
    _journald_available = True
    _JournaldLogHandler = JournalHandler
except ImportError:
    pass


def is_journald_available() -> bool:
    """Check if systemd journald logging is available."""
    return _journald_available


def setup_journald_logging(
    level: int = logging.INFO,
    identifier: str = "meshforge",
    suppress_libs: bool = True,
) -> bool:
    """
    Configure logging to use systemd journald.

    This is the preferred logging method on Raspberry Pi and other
    systemd-based systems. Logs can be viewed with:
        journalctl -t meshforge -f

    Args:
        level: Logging level
        identifier: Syslog identifier (shows in journalctl -t <identifier>)
        suppress_libs: Suppress noisy third-party loggers

    Returns:
        True if journald was configured, False if unavailable
    """
    global _initialized

    if not _journald_available:
        return False

    with _lock:
        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # Remove existing handlers
        root_logger.handlers.clear()

        # Add journald handler
        journal_handler = _JournaldLogHandler(SYSLOG_IDENTIFIER=identifier)
        journal_handler.setLevel(level)

        # Use structured format for journald
        journal_formatter = logging.Formatter(
            "%(name)s | %(levelname)s | %(message)s"
        )
        journal_handler.setFormatter(journal_formatter)
        root_logger.addHandler(journal_handler)

        # Suppress noisy third-party loggers
        if suppress_libs:
            for lib_name in [
                'urllib3', 'requests',
                'meshtastic', 'serial', 'asyncio', 'PIL',
            ]:
                logging.getLogger(lib_name).setLevel(logging.WARNING)

        _initialized = True
        return True


# ============================================================================
# Log Callbacks for UI Integration
# ============================================================================

_log_callbacks: List[Callable[[logging.LogRecord], None]] = []
_callback_lock = threading.Lock()


class CallbackHandler(logging.Handler):
    """Handler that forwards log records to registered callbacks."""

    def emit(self, record: logging.LogRecord) -> None:
        with _callback_lock:
            callbacks = list(_log_callbacks)

        for callback in callbacks:
            try:
                callback(record)
            except Exception:
                pass  # Don't let callback errors break logging


def register_log_callback(callback: Callable[[logging.LogRecord], None]) -> None:
    """
    Register a callback to receive log records.

    This allows UI components to display logs in real-time.

    Args:
        callback: Function that receives logging.LogRecord objects
    """
    with _callback_lock:
        if callback not in _log_callbacks:
            _log_callbacks.append(callback)


def unregister_log_callback(callback: Callable[[logging.LogRecord], None]) -> None:
    """Unregister a previously registered callback."""
    with _callback_lock:
        if callback in _log_callbacks:
            _log_callbacks.remove(callback)


def enable_ui_logging() -> None:
    """Enable log forwarding to UI callbacks."""
    root_logger = logging.getLogger()

    # Check if callback handler already exists
    for handler in root_logger.handlers:
        if isinstance(handler, CallbackHandler):
            return

    callback_handler = CallbackHandler()
    callback_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(callback_handler)


# ============================================================================
# Smart Setup (auto-detect best logging method)
# ============================================================================

def setup_smart_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    identifier: str = "meshforge",
    use_colors: bool = True,
    enable_callbacks: bool = True,
) -> str:
    """
    Automatically configure the best logging method for the environment.

    Priority:
    1. Journald (if available on systemd systems)
    2. File logging (if log_file specified)
    3. Console logging (fallback)

    Args:
        level: Logging level
        log_file: Optional log file path
        identifier: Syslog identifier for journald
        use_colors: Enable colored console output
        enable_callbacks: Enable UI callback handler

    Returns:
        String describing which method was configured
    """
    method = "console"

    # Try journald first (preferred on RPi)
    if _journald_available and os.path.exists('/run/systemd/system'):
        if setup_journald_logging(level, identifier):
            method = "journald"

    # If journald not used, fall back to standard setup
    if method != "journald":
        setup_logging(
            level=level,
            log_file=log_file,
            use_colors=use_colors,
        )
        method = "file" if log_file else "console"

    # Enable UI callbacks if requested
    if enable_callbacks:
        enable_ui_logging()

    return method


# ============================================================================
# Log Record Utilities
# ============================================================================

def format_log_record(record: logging.LogRecord) -> dict:
    """
    Format a log record as a dictionary for UI display.

    Args:
        record: The log record to format

    Returns:
        Dictionary with timestamp, level, name, message
    """
    return {
        'timestamp': datetime.fromtimestamp(record.created).isoformat(),
        'level': record.levelname,
        'name': record.name,
        'message': record.getMessage(),
        'lineno': record.lineno,
        'pathname': record.pathname,
    }


def get_level_color(level: str) -> str:
    """Get ANSI color code for a log level."""
    return LEVEL_COLORS.get(level, '')
