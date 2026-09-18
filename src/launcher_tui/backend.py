"""
Dialog Backend for MeshAnchor TUI Launcher

Provides a whiptail/dialog backend for terminal UI dialogs.
Works over SSH, without X display, on any terminal.
"""

import logging
import os
import shutil
import subprocess
import sys
import termios
from pathlib import Path
from typing import Tuple, Optional, List

logger = logging.getLogger(__name__)


def clear_screen() -> None:
    """Clear the terminal including scrollback buffer.

    Uses three ANSI sequences:
    - \\033[H     Move cursor to home position (top-left)
    - \\033[2J    Clear the visible viewport
    - \\033[3J    Clear the scrollback buffer

    The scrollback clear (\\033[3J) prevents "screen roll" where old
    print() output bleeds through when whiptail/dialog redraws.
    """
    sys.stdout.write('\033[H\033[2J\033[3J')
    sys.stdout.flush()


class DialogBackend:
    """Backend for whiptail/dialog TUI dialogs."""

    def __init__(self):
        self.backend = self._detect_backend()
        self.width = 78
        self.height = 22
        self.list_height = 14
        self._status_bar = None

    def set_status_bar(self, status_bar) -> None:
        """Set a StatusBar instance for persistent --backtitle display.

        Args:
            status_bar: StatusBar instance (from status_bar module).
        """
        self._status_bar = status_bar

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

    def _run(self, args: List[str], timeout: Optional[int] = None) -> Tuple[int, str]:
        """
        Run dialog/whiptail command and return (returncode, output).

        whiptail uses stderr for returning selection.
        newt library opens /dev/tty directly for ncurses display.
        stderr is redirected to a temp file to capture the selection.

        Args:
            args: Command arguments for the dialog backend.
            timeout: Optional subprocess timeout in seconds. Defaults to
                None (no timeout). whiptail/dialog opens /dev/tty directly,
                so when the terminal disconnects the process receives SIGHUP
                and terminates naturally — no timeout needed for orphan
                prevention.
        """
        import tempfile

        # Create temp file to capture selection output
        fd, tmp_path = tempfile.mkstemp(suffix='.txt', prefix='meshanchor_')
        os.close(fd)

        try:
            # Inject --backtitle from status bar if available
            full_args = list(args)
            if self._status_bar is not None:
                try:
                    backtitle = self._status_bar.get_status_line()
                    if backtitle:
                        full_args = ['--backtitle', backtitle] + full_args
                except Exception as e:
                    logger.debug("Status bar update failed: %s", e)

            # Build command as list args (safe, no shell needed)
            cmd_parts = [self.backend] + [str(a) for a in full_args]

            # Flush stale input from terminal before launching dialog.
            # Without this, leftover keystrokes (Enter, ESC sequences) from
            # the previous menu interaction can be read by the new whiptail
            # instance, causing it to immediately exit or select an item.
            try:
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
            except (termios.error, ValueError, OSError):
                pass  # Not a terminal or already closed

            # Clear screen before launching dialog so whiptail saves a clean
            # main buffer. Without this, whiptail saves whatever print() output
            # was on the main buffer and restores it on exit — causing the
            # "screen roll" where old text bleeds through between dialogs.
            clear_screen()

            # Run with stderr redirected to file to capture selection.
            # No default timeout — whiptail opens /dev/tty so SIGHUP
            # handles terminal disconnect. The old 3600s timeout caused
            # the TUI to silently exit after 1 hour of idle.
            with open(tmp_path, 'w') as stderr_file:
                result = subprocess.run(
                    cmd_parts, stderr=stderr_file, timeout=timeout,
                )

            # Read the captured selection
            with open(tmp_path, 'r') as f:
                output = f.read().strip()

            if result.returncode != 0:
                try:
                    term_size = os.get_terminal_size()
                    term_info = f"{term_size.lines}x{term_size.columns}"
                except (ValueError, OSError):
                    term_info = "unknown"
                logger.warning(
                    "Dialog exited %d (cmd=%s, term=%s, output=%r)",
                    result.returncode,
                    ' '.join(cmd_parts[:6]),
                    term_info,
                    output[:80] if output else '',
                )

            return result.returncode, output

        except subprocess.TimeoutExpired:
            logger.warning("Dialog subprocess timed out after %ss", timeout)
            return 1, ""
        except OSError as e:
            logger.error("Dialog subprocess failed: %s", e)
            return 1, ""
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def msgbox(self, title: str, text: str, height: int = None, width: int = None) -> None:
        """Display a message box."""
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        self._run([
            '--title', title,
            '--msgbox', text,
            str(h), str(w)
        ])

    def yesno(self, title: str, text: str, default_no: bool = False,
              height: int = None, width: int = None) -> bool:
        """Display yes/no dialog. Returns True for yes."""
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        args = ['--title', title]
        if default_no:
            args.append('--defaultno')
        args += ['--yesno', text, str(h), str(w)]
        code, _ = self._run(args)
        return code == 0

    def _cancel_flag(self, label: str) -> List[str]:
        """Relabel the always-painted Cancel button.

        The button is CHROME: unlike a list row it can never scroll off a
        short terminal, which is why it — not the "back" row — is the
        escape hatch a 24x80 newcomer actually sees. Ported from
        MeshForge 54880d62 (TUI audit Phase 5).

        whiptail and dialog(1) spell the flag differently, and an
        unsupported flag KILLS the dialog rather than degrading, so an
        unrecognised backend gets nothing: a mislabelled button is
        survivable, a dead menu is not.
        """
        if self.backend == 'whiptail':
            return ['--cancel-button', label]
        if self.backend == 'dialog':
            return ['--cancel-label', label]
        return []

    #: Sentinel for "no explicit label given — infer one from the choices".
    #: A plain None cannot do this job: None is also how a caller says
    #: "emit no flag at all", and the two need to stay distinguishable.
    _AUTO_CANCEL = object()

    #: The tag every navigational menu uses for its return-to-parent row.
    BACK_TAG = 'back'

    #: Rows a whiptail --menu box spends on everything that is not text or
    #: list: borders, title, the gaps above/below the list, the button row.
    #: MEASURED 2026-09-18 against real whiptail (newt 0.52.25) under a pty
    #: at 24 and 40 rows — this was 6 from 2026-03-07 to 2026-09-18, and 6
    #: is one short: a 2-line subtitle painted 1 line, 3 painted 2, 6
    #: painted 5, and a 21-row menu at 24 rows painted ZERO subtitle lines
    #: (18 list rows). With 7 every subtitle line renders and the list
    #: gives up exactly one row when the terminal binds. Text cannot
    #: scroll; the list can — so the row belongs to the text. The
    #: scripts/tui_smoke.py stub records list rows only and was blind to
    #: this for six months; the witness that is not ours is whiptail's own
    #: bytes. dialog(1) NOT measured (not installed on the measuring box).
    MENU_CHROME_ROWS = 7

    def _infer_cancel_label(self, choices, cancel_label):
        """Derive the Cancel button's label from the menu's own rows.

        A navigational menu carries a ``back`` row AND maps menu()'s None
        to that same row, so the row and the button are ONE control with
        two faces — but only the row can scroll off a short terminal.
        Deriving the label from the choices means the two faces cannot
        disagree (honest_failure_modes #5 satisfied structurally, not by
        a constant two places have to keep in sync).

        Inference, NOT a sweep: an operational picker has no ``back`` row,
        so it keeps whiptail's "Cancel" — which is correct, because
        cancelling an operation is not navigating back.

        Measured 2026-09-18 across both repos: the "None means back"
        contract is written at least five different ways
        (``is None or x == "back"``, ``x in (None, "back")``,
        ``if choice and choice != "back"``, …), so no regex over call
        sites can classify these reliably — but the presence of the
        ``back`` ROW is unambiguous. That is why this is inferred here
        rather than passed at ~284 call sites that would drift.
        MeshAnchor measured 214 menu call sites, ~167 carrying a back row.

        An explicit ``cancel_label`` always wins, including
        ``cancel_label="Cancel"`` as the deliberate opt-out.
        """
        if cancel_label is not self._AUTO_CANCEL:
            return cancel_label
        for row in choices or ():
            try:
                if row[0] == self.BACK_TAG:
                    return 'Back'
            except (IndexError, TypeError):
                continue
        return None

    def menu(self, title: str, text: str, choices: List[Tuple[str, str]],
             height: int = None, width: int = None, list_height: int = None,
             cancel_label=_AUTO_CANCEL) -> Optional[str]:
        """
        Display a menu and return selected tag.

        Args:
            title: Window title
            text: Description text
            choices: List of (tag, description) tuples
            height: Optional dialog height (uses default if not specified)
            width: Optional dialog width (uses default if not specified)
            list_height: Optional list height (uses default if not specified)
            cancel_label: Text for the Cancel button. Left unset it is
                INFERRED from the choices: a menu carrying a ``back`` row
                gets "Back", anything else keeps whiptail's "Cancel" (an
                operational picker should say Cancel — aborting an
                operation is not navigating back). Pass a string to
                override, including cancel_label="Cancel" as the
                deliberate opt-out. Pass None to emit no flag at all.

        Returns:
            Selected tag or None if cancelled
        """
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        lh = list_height if list_height is not None else self.list_height

        # Auto-fit: shrink list_height/height to fit within terminal.
        # Without this, menus with multi-line text overflow height=22
        # on 24-row terminals when backtitle is active (2 lines overhead).
        try:
            term_rows = os.get_terminal_size().lines
        except (ValueError, OSError):
            term_rows = 24
        backtitle_overhead = 2 if self._status_bar else 0
        max_h = term_rows - backtitle_overhead
        # Estimate text lines (account for \n and line wrapping)
        inner_w = max(w - 4, 20)
        text_lines = sum(
            max(1, (len(line) + inner_w - 1) // inner_w)
            for line in text.split('\n')
        )
        # Chrome rows: see MENU_CHROME_ROWS — MEASURED, not derived.
        chrome = self.MENU_CHROME_ROWS
        # GROW the box to fit its content up to the terminal, then shrink
        # the list if it still doesn't fit.
        #
        # MeshAnchor carried the ORIGINAL shrink-only fit, so it had two
        # defects at once. (1) A multi-line text panel inside the fixed
        # 22-row box was clipped even on a tall terminal — MeshForge fixed
        # that half in review F3 and the fix never ported here. (2) The
        # list never grew either: `lh` stayed at the default 14 no matter
        # how tall the terminal, so a 22-item menu scrolled identically on
        # a 60-row terminal that could show every row with space to spare.
        # MeshForge measured (2) with scripts/tui_smoke.py against real
        # whiptail on 2026-09-16; this port cures both.
        #
        # Small terminals are unaffected: when avail_lh binds, this
        # computes exactly what the old shrink branch did. Swept 12,296
        # combinations of terminal height x backtitle overhead x text
        # lines x item count against MeshAnchor's old formula: 5,754
        # identical, 6,542 changed, ZERO regressions (never fewer rows
        # shown, never a box taller than the terminal).
        wanted_lh = max(lh, len(choices))
        avail_lh = max(4, max_h - chrome - text_lines)
        lh = min(wanted_lh, avail_lh)
        h = min(max(h, chrome + text_lines + lh), max_h)

        args = ['--title', title]
        # Must precede the --menu box option: whiptail/dialog parse
        # [options] --menu text h w lh [tag item]... — a flag placed after
        # --menu would be read as a menu ITEM.
        _label = self._infer_cancel_label(choices, cancel_label)
        args.extend(self._cancel_flag(_label) if _label else [])
        args.extend([
            '--menu', text,
            str(h), str(w), str(lh),
            '--',  # End of options — menu items are positional args
        ])
        for tag, desc in choices:
            args.extend([tag, desc])

        code, output = self._run(args)
        if code == 0:
            return output
        if code in (1, 255):
            # User pressed Cancel (1) or Escape (255) — an ANSWER, not a
            # failure. Never retry it: the blanket retry below made every
            # Cancel and every Escape need TWO presses, because the first
            # one was spent re-rendering the same menu. Ported from
            # MeshForge review F4.
            #
            # Safe to stop retrying these because the stale-terminal-input
            # problem the blanket retry was papering over is fixed at the
            # source: _run() calls termios.tcflush() before every dialog.
            return None

        # Retry once on a GENUINE dialog failure (subprocess death, timeout,
        # exotic exit code) — the case the original retry was added for.
        logger.debug("Menu '%s' failed (code=%d), retrying once", title, code)
        code, output = self._run(args)
        if code == 0:
            return output
        # NOTE: MeshForge raises DialogError here, so a dead dialog cannot
        # impersonate a user cancel (review F7). MeshAnchor has no
        # DialogError type and no caller that catches one, so returning
        # None keeps the existing contract; the main menu's consecutive-
        # failure counter remains the backstop. Closing that gap needs the
        # exception type threaded through main.py — queued, not done here.
        return None

    def inputbox(self, title: str, text: str, init: str = "",
                 height: int = None, width: int = None) -> Optional[str]:
        """Display input box and return text."""
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        args = [
            '--title', title,
            '--inputbox', text,
            str(h), str(w),
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
                  choices: List[Tuple[str, str, bool]],
                  height: int = None, width: int = None, list_height: int = None) -> Optional[List[str]]:
        """
        Display checklist dialog.

        Args:
            choices: List of (tag, description, selected) tuples
            height: Optional dialog height (uses default if not specified)
            width: Optional dialog width (uses default if not specified)
            list_height: Optional list height (uses default if not specified)

        Returns:
            List of selected tags or None if cancelled
        """
        h = height if height is not None else self.height
        w = width if width is not None else self.width
        lh = list_height if list_height is not None else self.list_height

        args = [
            '--title', title,
            '--checklist', text,
            str(h), str(w), str(lh),
            '--',  # End of options — checklist items are positional args
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


# Alias for convenience
Dialog = DialogBackend
