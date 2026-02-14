"""
MeshForge Conflict Resolver - Interactive Port Conflict Resolution

Provides TUI dialogs for resolving port conflicts detected at startup.
When a port needed by MeshForge is in use by another process, this
module guides the user through resolution options.

Usage:
    from conflict_resolver import ConflictResolver
    from startup_checks import StartupChecker

    checker = StartupChecker()
    env = checker.check_all()

    if env.conflicts:
        resolver = ConflictResolver(dialog_backend)
        resolver.resolve_all(env.conflicts)
"""

import subprocess
import time
import logging
from typing import List, Optional, Callable

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Module-level safe imports
_PortConflict, _resolve_conflict, _HAS_STARTUP_CHECKS = safe_import(
    'startup_checks', 'PortConflict', 'resolve_conflict'
)


class ConflictResolver:
    """Interactive conflict resolution for TUI."""

    def __init__(self, dialog):
        """Initialize resolver with dialog backend.

        Args:
            dialog: DialogBackend instance for TUI interaction
        """
        self.dialog = dialog

    def resolve_all(self, conflicts: list) -> bool:
        """
        Attempt to resolve all conflicts interactively.

        Args:
            conflicts: List of PortConflict objects

        Returns:
            True if all conflicts resolved, False if user chose to abort
        """
        if not conflicts:
            return True

        # Show summary first
        summary = self._build_conflict_summary(conflicts)
        result = self.dialog.yesno(
            "Port Conflicts Detected",
            f"{summary}\n\n"
            "Would you like to resolve these conflicts now?\n\n"
            "Select 'Yes' to resolve, 'No' to continue anyway."
        )

        if not result:
            # User chose to continue with conflicts
            return self._confirm_continue_with_conflicts(conflicts)

        # Resolve each conflict
        for conflict in conflicts:
            resolved = self._resolve_single(conflict)
            if not resolved:
                # User aborted
                return False

        return True

    def _build_conflict_summary(self, conflicts: list) -> str:
        """Build a summary string of all conflicts."""
        lines = [f"Found {len(conflicts)} port conflict(s):\n"]

        for c in conflicts:
            lines.append(
                f"  Port {c.port}: {c.actual_process} (PID {c.actual_pid})\n"
                f"           blocks {c.expected_service}"
            )

        return "\n".join(lines)

    def _resolve_single(self, conflict) -> bool:
        """
        Resolve a single conflict interactively.

        Args:
            conflict: The conflict to resolve

        Returns:
            True if resolved (or skipped), False if user aborted
        """
        choices = [
            ("stop", f"Stop {conflict.actual_process} (kill PID {conflict.actual_pid})"),
            ("skip", "Skip - continue with conflict"),
            ("abort", "Abort - exit MeshForge"),
        ]

        choice = self.dialog.menu(
            f"Resolve Port {conflict.port} Conflict",
            f"Port {conflict.port} is needed by {conflict.expected_service}\n"
            f"but is currently used by:\n\n"
            f"  Process: {conflict.actual_process}\n"
            f"  PID: {conflict.actual_pid}\n\n"
            f"How would you like to proceed?",
            choices
        )

        if choice is None or choice == "abort":
            return False

        if choice == "stop":
            return self._stop_process(conflict)

        # choice == "skip"
        return True

    def _stop_process(self, conflict) -> bool:
        """Attempt to stop the conflicting process."""
        # Confirm before killing
        confirm = self.dialog.yesno(
            "Confirm Stop Process",
            f"This will stop {conflict.actual_process} (PID {conflict.actual_pid}).\n\n"
            f"Are you sure you want to continue?"
        )

        if not confirm:
            return self._resolve_single(conflict)  # Back to options

        # Try graceful termination first
        success = self._kill_process(conflict.actual_pid, signal='TERM')

        if success:
            self.dialog.msgbox(
                "Process Stopped",
                f"Successfully stopped {conflict.actual_process}.\n"
                f"Port {conflict.port} is now available."
            )
            return True
        else:
            # Offer forceful kill
            force = self.dialog.yesno(
                "Process Not Responding",
                f"{conflict.actual_process} did not stop gracefully.\n\n"
                f"Force kill the process (SIGKILL)?"
            )

            if force:
                success = self._kill_process(conflict.actual_pid, signal='KILL')
                if success:
                    self.dialog.msgbox(
                        "Process Killed",
                        f"Force killed {conflict.actual_process}.\n"
                        f"Port {conflict.port} is now available."
                    )
                    return True
                else:
                    self.dialog.msgbox(
                        "Failed to Kill Process",
                        f"Could not kill {conflict.actual_process}.\n\n"
                        f"You may need to stop it manually:\n"
                        f"  sudo kill -9 {conflict.actual_pid}"
                    )
                    return self._resolve_single(conflict)

            return self._resolve_single(conflict)

    def _kill_process(self, pid: int, signal: str = 'TERM') -> bool:
        """Kill a process and verify it's gone.

        Args:
            pid: Process ID to kill
            signal: Signal to send ('TERM' or 'KILL')

        Returns:
            True if process was successfully terminated
        """
        try:
            sig_flag = '-TERM' if signal == 'TERM' else '-KILL'
            subprocess.run(
                ['kill', sig_flag, str(pid)],
                capture_output=True, timeout=5
            )

            # Wait for process to die
            for _ in range(10):
                time.sleep(0.5)
                result = subprocess.run(
                    ['ps', '-p', str(pid)],
                    capture_output=True, timeout=5
                )
                if result.returncode != 0:
                    return True  # Process is gone

            return False  # Still running after timeout

        except Exception as e:
            logger.error(f"Failed to kill process {pid}: {e}")
            return False

    def _confirm_continue_with_conflicts(self, conflicts: List[PortConflict]) -> bool:
        """Confirm user wants to continue despite conflicts."""
        warning = (
            "Continuing with port conflicts may cause errors:\n\n"
        )

        for c in conflicts:
            warning += f"  - {c.expected_service} may fail to connect\n"

        warning += "\nAre you sure you want to continue?"

        return self.dialog.yesno("Continue with Conflicts?", warning)


def check_and_resolve_conflicts(dialog, checker=None) -> bool:
    """
    Convenience function to check for conflicts and resolve them.

    Args:
        dialog: DialogBackend instance
        checker: Optional StartupChecker (creates one if not provided)

    Returns:
        True if no conflicts or all resolved, False if user aborted
    """
    try:
        from startup_checks import StartupChecker

        if checker is None:
            checker = StartupChecker()

        env = checker.check_all()

        if not env.conflicts:
            return True

        resolver = ConflictResolver(dialog)
        return resolver.resolve_all(env.conflicts)

    except ImportError:
        logger.warning("StartupChecker not available, skipping conflict check")
        return True
