"""
TUI Error Logging — shared helpers for error log path and rotation.

Extracted from MeshAnchorLauncher._log_error() and TUIContext.log_error()
to eliminate duplication (DRY). Both call sites now delegate here.
"""

import datetime
import logging
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_LOG_BYTES = 1_048_576  # 1 MB


def get_error_log_path() -> Path:
    """Get the path to the TUI error log file.

    Uses get_real_user_home() to resolve the correct home directory
    even when running under sudo (MF001 compliance).

    Falls back to /tmp if the log directory cannot be created.
    """
    try:
        from utils.paths import get_real_user_home
        log_dir = get_real_user_home() / ".cache" / "meshanchor" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "tui_errors.log"
    except Exception as e:
        logger.debug("Cannot create log directory, using /tmp fallback: %s", e)
        return Path("/tmp/meshanchor_tui_errors.log")


def log_error(context: str, exc: Exception) -> None:
    """Write error details to the TUI error log file.

    Preserves full tracebacks for debugging while keeping the TUI
    display clean for the user.

    Rotates the log when it exceeds 1 MB to prevent unbounded disk
    growth on resource-constrained systems (e.g. Pi).
    """
    try:
        log_path = get_error_log_path()

        # Rotate if log exceeds 1 MB
        try:
            if log_path.exists() and log_path.stat().st_size > _MAX_LOG_BYTES:
                rotated = log_path.with_suffix('.log.1')
                if rotated.exists():
                    rotated.unlink()
                log_path.rename(rotated)
        except OSError:
            pass  # Rotation failure is non-critical

        with open(log_path, 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{datetime.datetime.now().isoformat()}] {context}\n")
            f.write(f"Exception: {type(exc).__name__}: {exc}\n")
            f.write(traceback.format_exc())
            f.write(f"{'='*60}\n")
    except Exception:
        pass  # Logging failure must never compound the original error
