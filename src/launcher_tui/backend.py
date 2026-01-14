"""
Dialog Backend for MeshForge TUI Launcher

Provides a whiptail/dialog backend for terminal UI dialogs.
Works over SSH, without X display, on any terminal.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Optional, List


class DialogBackend:
    """Backend for whiptail/dialog TUI dialogs."""

    def __init__(self):
        self.backend = self._detect_backend()
        self.width = 78
        self.height = 22
        self.list_height = 14

    def _detect_backend(self) -> Optional[str]:
        """Detect available dialog backend."""
        # Prefer whiptail (Debian/Ubuntu default, like raspi-config)
        if shutil.which('whiptail'):
            return 'whiptail'
        elif shutil.which('dialog'):
            return 'dialog'
        return None

    @property
    def available(self) -> bool:
        return self.backend is not None

    def _run(self, args: List[str]) -> Tuple[int, str]:
        """
        Run dialog/whiptail command and return (returncode, output).

        whiptail uses stderr for returning selection.
        newt library opens /dev/tty directly for ncurses display.
        We use os.system for proper terminal inheritance and redirect
        stderr to a temp file to capture the selection.
        """
        import tempfile
        import shlex

        # Create temp file to capture selection output
        fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='meshforge_')
        os.close(fd)

        try:
            # Build command with proper shell quoting
            cmd_parts = [self.backend] + [str(a) for a in args]
            escaped_cmd = ' '.join(shlex.quote(p) for p in cmd_parts)

            # Use os.system for proper terminal inheritance
            # stderr redirected to file captures selection
            # newt library opens /dev/tty directly for display
            exit_code = os.system(f'{escaped_cmd} 2>{shlex.quote(tmp_path)}')

            # Read the captured selection
            with open(tmp_path, 'r') as f:
                output = f.read().strip()

            # os.system returns wait status, extract exit code
            return os.waitstatus_to_exitcode(exit_code) if hasattr(os, 'waitstatus_to_exitcode') else (exit_code >> 8), output

        except Exception as e:
            return 1, str(e)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def msgbox(self, title: str, text: str) -> None:
        """Display a message box."""
        self._run([
            '--title', title,
            '--msgbox', text,
            str(self.height), str(self.width)
        ])

    def yesno(self, title: str, text: str, default_no: bool = False) -> bool:
        """Display yes/no dialog. Returns True for yes."""
        args = ['--title', title]
        if default_no:
            args.append('--defaultno')
        args += ['--yesno', text, str(self.height), str(self.width)]
        code, _ = self._run(args)
        return code == 0

    def menu(self, title: str, text: str, choices: List[Tuple[str, str]]) -> Optional[str]:
        """
        Display a menu and return selected tag.

        Args:
            title: Window title
            text: Description text
            choices: List of (tag, description) tuples

        Returns:
            Selected tag or None if cancelled
        """
        args = [
            '--title', title,
            '--menu', text,
            str(self.height), str(self.width), str(self.list_height)
        ]
        for tag, desc in choices:
            args.extend([tag, desc])

        code, output = self._run(args)
        if code == 0:
            return output
        return None

    def inputbox(self, title: str, text: str, init: str = "") -> Optional[str]:
        """Display input box and return text."""
        args = [
            '--title', title,
            '--inputbox', text,
            str(self.height), str(self.width),
            init
        ]
        code, output = self._run(args)
        if code == 0:
            return output
        return None

    def infobox(self, title: str, text: str) -> None:
        """Display info box (no wait for input)."""
        self._run([
            '--title', title,
            '--infobox', text,
            str(8), str(self.width)
        ])

    def gauge(self, title: str, text: str, percent: int) -> None:
        """Display progress gauge."""
        args = [
            '--title', title,
            '--gauge', text,
            str(8), str(self.width), str(percent)
        ]
        # Gauge needs stdin for progress updates
        try:
            proc = subprocess.Popen(
                [self.backend] + args,
                stdin=subprocess.PIPE,
                text=True
            )
            proc.communicate(input=str(percent), timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            # Gauge timeout or display issue - non-critical
            pass

    def checklist(self, title: str, text: str,
                  choices: List[Tuple[str, str, bool]]) -> Optional[List[str]]:
        """
        Display checklist dialog.

        Args:
            choices: List of (tag, description, selected) tuples

        Returns:
            List of selected tags or None if cancelled
        """
        args = [
            '--title', title,
            '--checklist', text,
            str(self.height), str(self.width), str(self.list_height)
        ]
        for tag, desc, selected in choices:
            status = 'ON' if selected else 'OFF'
            args.extend([tag, desc, status])

        code, output = self._run(args)
        if code == 0:
            # Parse quoted output (whiptail uses quotes)
            selected = output.replace('"', '').split()
            return selected
        return None
