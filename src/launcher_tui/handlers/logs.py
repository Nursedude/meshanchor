"""
Logs Handler — Log viewing functionality.

Converted from logs_menu_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import subprocess
from pathlib import Path
from typing import List

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.paths import ReticulumPaths, get_real_user_home
from utils.service_check import is_service_unit_installed

try:
    from utils.logging_config import set_log_level, get_current_log_level, cleanup_old_logs
    _HAS_LOG_LEVEL = True
    _HAS_GET_LEVEL = True
    _HAS_CLEANUP = True
except ImportError:
    _HAS_LOG_LEVEL = False
    _HAS_GET_LEVEL = False
    _HAS_CLEANUP = False


class LogsHandler(BaseHandler):
    """TUI handler for log viewing."""

    handler_id = "logs"
    menu_section = "system"

    MESH_UNITS = ['meshtasticd', 'rnsd', 'mosquitto', 'nomadnet']

    def menu_items(self):
        return [
            ("logs", "Logs                View/follow logs", None),
        ]

    def execute(self, action):
        if action == "logs":
            self._logs_menu()

    def _logs_menu(self):
        while True:
            choices = [
                ("live-mesh", "Live: meshtasticd      (Ctrl+C to stop)"),
                ("live-rns", "Live: rnsd             (Ctrl+C to stop)"),
                ("live-all", "Live: all services     (Ctrl+C to stop)"),
                ("errors", "Errors                 Last hour, priority err+"),
                ("mesh-50", "meshtasticd            Last 50 lines"),
                ("rns-50", "rnsd                   Last 50 lines"),
                ("boot", "Boot Messages          This boot"),
                ("kernel", "Kernel Messages        dmesg"),
                ("meshanchor", "MeshAnchor App Logs     Browse log files"),
                ("crash", "Crash Log              TUI error output"),
                ("level", "Log Level              Change runtime verbosity"),
                ("cleanup", "Log Cleanup            Remove old log files"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Log Viewer",
                "Terminal-native logs (real journalctl):",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "live-mesh": ("Live meshtasticd Logs", self._view_live_meshtasticd),
                "live-rns": ("Live rnsd Logs", self._view_live_rnsd),
                "live-all": ("Live All Logs", self._view_live_all),
                "errors": ("Error Logs", self._view_error_logs),
                "mesh-50": ("meshtasticd Logs", self._view_meshtasticd_recent),
                "rns-50": ("rnsd Logs", self._view_rnsd_recent),
                "boot": ("Boot Messages", self._view_boot_messages),
                "kernel": ("Kernel Messages", self._view_kernel_messages),
                "meshanchor": ("MeshAnchor Logs", self._view_meshanchor_logs),
                "crash": ("Crash Log", self._view_crash_log),
                "level": ("Log Level", self._change_log_level),
                "cleanup": ("Log Cleanup", self._cleanup_logs),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "LogsHandler._logs_menu")

    def _view_live_log(self, title: str, cmd: List[str]) -> None:
        clear_screen()
        print(f"=== {title} (Ctrl+C to stop) ===\n")
        proc = None
        try:
            proc = subprocess.Popen(cmd)
            proc.wait(timeout=300)
        except FileNotFoundError:
            # The tool is absent (no journalctl on this box). Say so here;
            # letting it escape gave safe_call's generic "File Not Found"
            # (KNOWN_CRASHED_L2, 2026-09-22).
            print(f"  UNKNOWN — '{cmd[0]}' is not installed on this box, so this")
            print("  log cannot be followed here. Nothing was read.")
            self.ctx.wait_for_enter()
        except subprocess.TimeoutExpired:
            print("\n[Log view timed out after 5 minutes]")
        except KeyboardInterrupt:
            pass
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def _view_live_meshtasticd(self):
        self._view_live_log(
            "meshtasticd live log",
            ['journalctl', '-u', 'meshtasticd', '-f', '-n', '30', '--no-pager'],
        )

    def _view_live_rnsd(self):
        self._view_live_log(
            "rnsd live log",
            ['journalctl', '-u', 'rnsd', '-f', '-n', '30', '--no-pager'],
        )

    def _mesh_journal_args(self):
        """(journalctl match args, notes) for the mesh units THIS box has.

        A unit with no unit file made the journal's unit filter print "-- No
        entries --", which read as a quiet service; a USER-scope unit (nomadnet
        on meshanchor-server) never matches the system unit filter at all
        (2026-09-25, ported from MeshForge e52a04f6)."""
        args, absent, user = [], [], []
        for unit in self.MESH_UNITS:
            if is_service_unit_installed(unit):
                args += ['-u', unit]
            elif is_service_unit_installed(unit, user=True):
                args += ['--user-unit', unit]
                user.append(unit)
            else:
                absent.append(unit)
        notes = []
        if absent:
            notes.append(f"Not installed on this box: {', '.join(absent)} — no lines "
                         "from them means ABSENT, not quiet.")
        if user:
            notes.append(f"User-scope unit(s): {', '.join(user)} (read with --user-unit).")
        if 'rnsd' not in absent:
            notes.append("rnsd runs with --service and logs to its config dir's "
                         "'logfile', not the journal — see rnsd Logs.")
        return args, notes

    def _run_units_snapshot(self, title: str, base_cmd: List[str], timeout: int = 15) -> None:
        args, notes = self._mesh_journal_args()
        if not args:
            clear_screen()
            print(f"=== {title} ===\n")
            for n in notes:
                print(f"  {n}")
            print("  None of the mesh units exist on this box.")
            self.ctx.wait_for_enter()
            return
        self._run_snapshot(title, base_cmd + args, timeout=timeout, notes=notes)

    def _view_live_all(self):
        args, _notes = self._mesh_journal_args()
        if not args:
            self.ctx.dialog.msgbox("Mesh services live log",
                                   "None of the mesh units exist on this box.")
            return
        self._view_live_log("Mesh services live log",
                            ['journalctl', '-f', '-n', '30', '--no-pager'] + args)

    def _run_snapshot(self, title: str, cmd: List[str], timeout: int = 15,
                      notes: List[str] = ()) -> None:
        """Print one command's output in the terminal, or say why it could not.

        A missing tool (no journalctl / dmesg on this box) used to escape into
        safe_call as a generic "File Not Found" dialog, and a timeout escaped
        the same way (truth sweep level two, 2026-09-24 — MeshForge fixed its
        copy by moving these into an in-app pane; MA keeps terminal output).
        """
        clear_screen()
        print(f"=== {title} ===\n")
        for n in notes:
            print(f"  {n}")
        if notes:
            print()
        try:
            subprocess.run(cmd, timeout=timeout)
        except FileNotFoundError:
            print(f"  UNKNOWN — '{cmd[0]}' is not installed on this box, so this")
            print("  log cannot be shown here. Nothing was read.")
        except subprocess.TimeoutExpired:
            print(f"\n  UNKNOWN — '{cmd[0]}' did not finish within {timeout}s;")
            print("  the output above (if any) is incomplete.")
        except OSError as e:
            print(f"  UNKNOWN — '{cmd[0]}' could not be started: {e}")
        self.ctx.wait_for_enter()

    def _view_error_logs(self):
        self._run_units_snapshot("Mesh Service Errors (last hour, priority err+)",
                                 ['journalctl', '-p', 'err', '--since', '1 hour ago',
                                  '--no-pager'], timeout=30)

    def _view_meshtasticd_recent(self):
        self._run_snapshot("meshtasticd (last 50 lines)",
                           ['journalctl', '-u', 'meshtasticd', '-n', '50', '--no-pager'])

    def _view_rnsd_recent(self):
        """rnsd's REAL log is its config dir's 'logfile' (`rnsd --service` logs
        to file); the journal holds only systemd's start/stop lines, so this
        screen could never show an RNS error (MeshForge e52a04f6, 2026-09-25)."""
        path = ReticulumPaths.get_config_dir() / "logfile"
        notes = []
        try:
            raw = path.read_bytes()
            lines = raw.replace(b"\x00", b"").decode("utf-8", "replace").splitlines()
            nul = raw.count(b"\x00")
            notes.append(f"{path}: last 50 of {len(lines)} lines"
                         + (f"; {nul} NUL byte(s) stripped (likely power-loss truncation)" if nul else ""))
            notes.extend(lines[-50:] or ["(file is empty)"])
        except FileNotFoundError:
            notes.append(f"{path}: not found — rnsd may not run with --service here, "
                         "or uses another config dir")
        except OSError as e:
            notes.append(f"{path}: UNREADABLE ({e}) — rnsd's log could not be read")
        notes.append("--- systemd journal (start/stop only under --service) ---")
        self._run_snapshot("rnsd Logs",
                           ['journalctl', '-u', 'rnsd', '-n', '15', '--no-pager'], notes=notes)

    def _view_boot_messages(self):
        self._run_units_snapshot("Mesh Service Boot Messages (this boot)",
                                 ['journalctl', '-b', '-n', '100', '--no-pager'])

    def _view_kernel_messages(self):
        self._run_snapshot("Kernel messages (dmesg)",
                           ['dmesg', '--time-format=reltime'], timeout=10)

    def _view_meshanchor_logs(self):
        home = get_real_user_home()
        log_dirs = [
            home / ".config" / "meshanchor" / "logs",
            home / ".cache" / "meshanchor" / "logs",
        ]

        all_logs = []
        for d in log_dirs:
            if d.exists():
                all_logs.extend(d.glob("meshanchor_*.log"))
                all_logs.extend(d.glob("meshanchor_*.log.*"))

        if not all_logs:
            self.ctx.dialog.msgbox(
                "MeshAnchor Logs",
                "No MeshAnchor application logs found.\n\n"
                "Logs are written to:\n"
                f"  {log_dirs[0]}\n\n"
                "Logs are created automatically during each session."
            )
            return

        all_logs.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        if len(all_logs) == 1:
            self._display_log_file(all_logs[0])
            return

        choices = []
        for i, log_file in enumerate(all_logs[:10]):
            stat = log_file.stat()
            size_kb = stat.st_size / 1024
            from datetime import datetime
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
            label = f"{log_file.name:<30s} {size_kb:>6.1f}KB  {mtime}"
            choices.append((str(i), label))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "MeshAnchor Log Files",
            f"Found {len(all_logs)} log file(s). Newest first:",
            choices
        )

        if choice is None or choice == "back":
            return

        try:
            idx = int(choice)
            self._display_log_file(all_logs[idx])
        except (ValueError, IndexError):
            pass

    def _display_log_file(self, log_path: Path, tail_lines: int = 80) -> None:
        try:
            content = log_path.read_text()
            lines = content.strip().split('\n')
            total = len(lines)
            shown = lines[-tail_lines:]

            clear_screen()
            print(f"=== {log_path.name} ({total} total lines, showing last {len(shown)}) ===\n")
            print('\n'.join(shown))
            print(f"\n{'=' * 60}")
            print(f"Full path: {log_path}")
            print(f"Size: {log_path.stat().st_size / 1024:.1f} KB")
            self.ctx.wait_for_enter()
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to read log file:\n{e}")

    def _view_crash_log(self):
        crash_paths = [
            get_real_user_home() / ".cache" / "meshanchor" / "logs" / "tui_errors.log",
            Path("/tmp") / "tui_errors.log",
        ]

        crash_log = None
        for p in crash_paths:
            if p.exists() and p.stat().st_size > 0:
                crash_log = p
                break

        if not crash_log:
            self.ctx.dialog.msgbox(
                "Crash Log",
                "No crash log found (good news!).\n\n"
                "The crash log captures unhandled exceptions\n"
                "and stderr output from the TUI process."
            )
            return

        self._display_log_file(crash_log, tail_lines=50)

    def _change_log_level(self):
        """Change the runtime log level."""
        if not _HAS_LOG_LEVEL:
            self.ctx.dialog.msgbox("Error", "Log level control unavailable.")
            return

        current = get_current_log_level() if _HAS_GET_LEVEL else "UNKNOWN"

        choices = [
            ("DEBUG", f"DEBUG          {'(current)' if current == 'DEBUG' else 'Verbose'}"),
            ("INFO", f"INFO           {'(current)' if current == 'INFO' else 'Normal'}"),
            ("WARNING", f"WARNING        {'(current)' if current == 'WARNING' else 'Quiet'}"),
            ("ERROR", f"ERROR          {'(current)' if current == 'ERROR' else 'Errors only'}"),
        ]

        choice = self.ctx.dialog.menu(
            "Log Level",
            f"Current level: {current}\nChange runtime log verbosity:",
            choices
        )

        if choice and choice in ("DEBUG", "INFO", "WARNING", "ERROR"):
            level = getattr(logging, choice)
            set_log_level(level)
            self.ctx.dialog.msgbox(
                "Log Level Changed",
                f"Log level set to {choice}.\n\n"
                "This affects the current session only.\n"
                "File logging always captures DEBUG level."
            )

    def _cleanup_logs(self):
        """Remove old log files."""
        if not _HAS_CLEANUP:
            self.ctx.dialog.msgbox("Error", "Log cleanup unavailable.")
            return

        choice = self.ctx.dialog.yesno(
            "Log Cleanup",
            "Remove log files older than 30 days?\n\n"
            "This frees disk space on long-running deployments.\n"
            "Current session logs will not be affected."
        )

        if choice:
            deleted = cleanup_old_logs(max_age_days=30)
            self.ctx.dialog.msgbox(
                "Log Cleanup Complete",
                f"Removed {deleted} old log file(s)." if deleted
                else "No old log files found."
            )
