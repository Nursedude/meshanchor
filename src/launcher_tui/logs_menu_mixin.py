"""
Logs Menu Mixin - Log viewing functionality.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import subprocess
from pathlib import Path

# Import centralized path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    import os

    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')


class LogsMenuMixin:
    """Mixin providing log viewing functionality."""

    def _logs_menu(self):
        """Log viewer - all terminal-native."""
        while True:
            choices = [
                ("live-mesh", "Live: meshtasticd (Ctrl+C to stop)"),
                ("live-rns", "Live: rnsd (Ctrl+C to stop)"),
                ("live-all", "Live: all services (Ctrl+C to stop)"),
                ("errors", "Errors (last hour)"),
                ("mesh-50", "meshtasticd (last 50 lines)"),
                ("rns-50", "rnsd (last 50 lines)"),
                ("boot", "Boot messages (this boot)"),
                ("kernel", "Kernel messages (dmesg)"),
                ("meshforge", "MeshForge app logs"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Log Viewer",
                "Terminal-native logs (real journalctl):",
                choices
            )

            if choice is None or choice == "back":
                break

            try:
                subprocess.run(['clear'], check=False, timeout=5)

                if choice == "live-mesh":
                    print("=== meshtasticd live log (Ctrl+C to stop) ===\n")
                    try:
                        subprocess.run(
                            ['journalctl', '-u', 'meshtasticd', '-f', '-n', '30', '--no-pager'],
                            timeout=None
                        )
                    except KeyboardInterrupt:
                        pass
                elif choice == "live-rns":
                    print("=== rnsd live log (Ctrl+C to stop) ===\n")
                    try:
                        subprocess.run(
                            ['journalctl', '-u', 'rnsd', '-f', '-n', '30', '--no-pager'],
                            timeout=None
                        )
                    except KeyboardInterrupt:
                        pass
                elif choice == "live-all":
                    print("=== All services live log (Ctrl+C to stop) ===\n")
                    try:
                        subprocess.run(
                            ['journalctl', '-f', '-n', '30', '--no-pager'],
                            timeout=None
                        )
                    except KeyboardInterrupt:
                        pass
                elif choice == "errors":
                    print("=== Errors (last hour, priority err+) ===\n")
                    subprocess.run(
                        ['journalctl', '-p', 'err', '--since', '1 hour ago', '--no-pager'],
                        timeout=30
                    )
                    self._wait_for_enter()
                elif choice == "mesh-50":
                    print("=== meshtasticd (last 50 lines) ===\n")
                    subprocess.run(
                        ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'],
                        timeout=15
                    )
                    self._wait_for_enter()
                elif choice == "rns-50":
                    print("=== rnsd (last 50 lines) ===\n")
                    subprocess.run(
                        ['journalctl', '-u', 'rnsd', '-n', '50', '--no-pager'],
                        timeout=15
                    )
                    self._wait_for_enter()
                elif choice == "boot":
                    print("=== Boot messages (this boot) ===\n")
                    subprocess.run(
                        ['journalctl', '-b', '-n', '100', '--no-pager'],
                        timeout=15
                    )
                    self._wait_for_enter()
                elif choice == "kernel":
                    print("=== Kernel messages (dmesg) ===\n")
                    subprocess.run(['dmesg', '--time-format=reltime'], timeout=10)
                    self._wait_for_enter()
                elif choice == "meshforge":
                    self._view_meshforge_logs()
            except KeyboardInterrupt:
                pass  # Return to log menu
            except Exception as e:
                self.dialog.msgbox(
                    "Log Viewer Error",
                    f"Failed to view logs:\n{type(e).__name__}: {e}"
                )

    def _view_meshforge_logs(self):
        """View MeshForge application logs."""
        log_dir = get_real_user_home() / ".config" / "meshforge" / "logs"

        if not log_dir.exists():
            self.dialog.msgbox("Logs", "No MeshForge logs found yet.\n\nLogs are created when you use MeshForge.")
            return

        log_files = list(log_dir.glob("*.log"))
        if not log_files:
            self.dialog.msgbox("Logs", "No log files found in:\n" + str(log_dir))
            return

        # Show most recent log
        latest_log = max(log_files, key=lambda f: f.stat().st_mtime)

        try:
            content = latest_log.read_text()
            lines = content.strip().split('\n')[-50:]  # Last 50 lines

            subprocess.run(['clear'], check=False, timeout=5)
            print(f"=== MeshForge Log: {latest_log.name} ===\n")
            print('\n'.join(lines))
            print("\n" + "=" * 50)
            self._wait_for_enter()
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to read log: {e}")
