#!/usr/bin/env python3
"""
MeshAnchor Linter - Check for common issues and coding standards.

Checks:
- MF001: Path.home() violations (must use get_real_user_home for sudo compatibility)
- MF002: shell=True in subprocess calls (security risk)
- MF003: Bare except: clauses (should use except Exception:)
- MF004: Missing timeout in subprocess calls
- MF005: (removed — was GLib.idle_add check, GTK4 removed in v0.5.x)
- MF006: safe_import for first-party modules (must use direct imports)
- MF007: Direct TCPInterface creation (must use connection manager, Issue #17)
- MF008: Raw systemctl for service state decisions (must use service_check, Issue #20)
- MF009: RNS.Reticulum() without configdir (causes EADDRINUSE, Issue #12)
- MF010: time.sleep() in daemon loops (must use _stop_event.wait(), H1)
- MF011: Repair logic in _nomadnet_rns_checks.py (must be in _rns_repair.py/diagnostics)
- MF012: Context-loaded doc size (persistent_issues.md must stay under 40k chars)
- MF013: Bare sqlite3.connect() outside db_helpers.py (must use connect_tuned)
- MF014: Direct MeshCore.create_serial / create_tcp / serial.Serial outside meshcore_connection.py
- MF016: @patch('src.utils.paths.…') in tests — production imports via bare 'utils.paths', divergent class objects
- MA017: hardened systemd unit (ProtectHome=read-only) ReadWritePaths drift vs the three meshanchor buckets (Issue #58 class, ported from MeshForge MF017)
- MF019: RNS.Reticulum() outside the guarded chokepoint (must use open_reticulum from utils.rns_init; #68/#69, ported from MeshForge 2026-05-31)
- MF020: apply_config_and_restart() return (bool, msg) discarded in TUI handlers (hardcoded-success-after-unchecked-action, honest-signal #74-#77; ported from MeshForge 2026-06-08)
- MA022: bare/exit-code-masked pip & swallowed apt in shell installers (must route through scripts/lib/install_common.sh — pip-presence + PEP 668 + checked rc; install-hardening arc, ported from MeshForge MF022)

Usage:
    python3 scripts/lint.py [files...]
    python3 scripts/lint.py --all
    python3 scripts/lint.py --staged
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class LintIssue:
    file: str
    line: int
    severity: Severity
    code: str
    message: str

    def __str__(self):
        icon = {"error": "E", "warning": "W", "info": "I"}[self.severity.value]
        return f"{self.file}:{self.line}: [{icon}] {self.code}: {self.message}"


class MeshAnchorLinter:
    """Linter for MeshAnchor-specific coding standards."""

    def __init__(self):
        self.issues: List[LintIssue] = []

    def lint_file(self, filepath: str) -> List[LintIssue]:
        """Lint a single file and return issues found."""
        issues = []

        if not filepath.endswith('.py'):
            return issues

        # Self-skip: the linter source legitimately contains every pattern
        # it detects (in detection regexes, docstrings, allowlist comments).
        if os.path.basename(filepath) == 'lint.py' and 'scripts' in filepath.split(os.sep):
            return issues

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except (IOError, OSError) as e:
            return [LintIssue(filepath, 0, Severity.ERROR, "MF000", f"Cannot read file: {e}")]

        content = ''.join(lines)

        # Check each line
        for i, line in enumerate(lines, 1):
            issues.extend(self._check_line(filepath, i, line, content))

        return issues

    def _check_line(self, filepath: str, lineno: int, line: str, content: str) -> List[LintIssue]:
        """Check a single line for issues."""
        issues = []
        stripped = line.strip()

        # Skip comments
        if stripped.startswith('#'):
            return issues

        # MF001: Path.home() violation
        # Skip the paths.py utility file that defines get_real_user_home()
        if 'Path.home()' in line and 'paths.py' not in filepath:
            # Skip string literals (changelog entries, documentation,
            # error messages — including f-strings and r-strings that the
            # lint rule formerly missed).
            is_string_literal = (
                stripped.startswith('"') or stripped.startswith("'")
                or stripped.startswith('f"') or stripped.startswith("f'")
                or stripped.startswith('rf"') or stripped.startswith("rf'")
                or stripped.startswith('fr"') or stripped.startswith("fr'")
                or stripped.startswith('r"') or stripped.startswith("r'")
            )
            # Acceptable fallback patterns:
            # 1. return Path.home() in a fallback function
            # 2. else Path.home() in a ternary after SUDO_USER check
            # 3. Inside an except ImportError block with SUDO_USER handling nearby
            is_fallback_pattern = (
                'return Path.home()' in line or
                'else Path.home()' in line or
                ('def get_real_user_home' in content and 'Path.home()' in line)
            )
            # Also check if this is in an except block after trying to import paths
            context_start = max(0, content.find(line) - 500)
            nearby_context = content[context_start:content.find(line) + len(line)]
            has_import_fallback = (
                'from utils.paths import' in nearby_context and
                'except ImportError' in nearby_context
            )
            if not is_string_literal and not is_fallback_pattern and not has_import_fallback:
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF001",
                    "Use get_real_user_home() instead of Path.home() for sudo compatibility"
                ))

        # MF002: shell=True security risk
        # Only flag actual subprocess calls, not comments/docstrings/patterns
        if 'shell=True' in line and 'subprocess' in content:
            # Must look like actual code: subprocess.run(..., shell=True, ...)
            # Skip if: in docstring, comment, string literal, or pattern definition
            is_actual_call = (
                re.search(r'subprocess\.\w+\s*\([^)]*shell\s*=\s*True', line) or
                (stripped.startswith('subprocess.') and 'shell=True' in line) or
                ('shell=True' in line and '(' in line and ')' in line and 'subprocess' in line)
            )
            # Exclude comments and docstring-like content
            is_doc_or_comment = (
                stripped.startswith('#') or
                stripped.startswith('"""') or
                stripped.startswith("'''") or
                'Security:' in line or  # Common docstring pattern
                'NEVER' in line or      # Documentation
                'pattern' in line.lower() or
                line.strip().startswith('"') or
                line.strip().startswith("'")
            )
            if is_actual_call and not is_doc_or_comment:
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF002",
                    "Avoid shell=True in subprocess calls - use list args instead"
                ))

        # MF003: Bare except clause
        if re.match(r'^\s*except\s*:\s*(#.*)?$', line):
            issues.append(LintIssue(
                filepath, lineno, Severity.WARNING, "MF003",
                "Bare except: clause - use 'except Exception:' at minimum"
            ))

        # MF004: subprocess.run/call/Popen without timeout
        subprocess_pattern = r'subprocess\.(run|call|Popen)\s*\('
        if re.search(subprocess_pattern, line):
            # Skip if marked as interactive or intentionally no timeout
            if '# Interactive' in line or '# no timeout' in line.lower():
                pass  # Skip interactive commands
            # Skip if it's inside a string (changelog, pattern definition)
            elif (stripped.startswith('"') or stripped.startswith("'") or
                  'SECURITY:' in line or 'IMPROVED:' in line or 'pattern' in line.lower()):
                pass  # Skip changelog/documentation/pattern strings
            else:
                # Look ahead for timeout in the same statement
                start_idx = content.find(line)
                if start_idx != -1:
                    # Get the call text (matching parens)
                    context = content[start_idx:start_idx + 500]
                    paren_count = 0
                    call_text = ""
                    for char in context:
                        call_text += char
                        if char == '(':
                            paren_count += 1
                        elif char == ')':
                            paren_count -= 1
                            if paren_count == 0:
                                break

                    # Check for timeout in call or kwargs unpacking nearby
                    has_timeout = 'timeout' in call_text
                    # Check for **kwargs pattern - look back for kwargs dict with timeout
                    if '**' in call_text:
                        kwargs_match = re.search(r'\*\*(\w+)', call_text)
                        if kwargs_match:
                            kwargs_name = kwargs_match.group(1)
                            # Look back in content for this dict definition with timeout
                            lookback = content[max(0, start_idx - 1000):start_idx]
                            if f"'{kwargs_name}'" in lookback or f'"{kwargs_name}"' in lookback:
                                pass  # Skip - complex case
                            elif f'{kwargs_name}' in lookback and 'timeout' in lookback:
                                has_timeout = True

                    if not has_timeout and 'Popen' not in line:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.WARNING, "MF004",
                            "subprocess call without timeout parameter"
                        ))

        # MF006: safe_import for first-party modules
        # First-party modules must use direct imports, not safe_import
        if 'safe_import(' in line and 'safe_import.py' not in filepath:
            first_party_prefixes = (
                "'utils.", "'commands.", "'gateway.", "'core.",
                "'launcher_tui.", "'config.", "'monitoring.", "'plugins.",
                "'cli.", "'agent.", "'amateur.", "'diagnostics.", "'updates.",
            )
            if any(prefix in line for prefix in first_party_prefixes):
                # Skip docstrings/comments/examples
                if not stripped.startswith('#') and not stripped.startswith('"') and not stripped.startswith("'"):
                    issues.append(LintIssue(
                        filepath, lineno, Severity.ERROR, "MF006",
                        "safe_import used for first-party module - use direct import instead"
                    ))

        # MF005: Removed — was GLib.idle_add check for GTK4 thread safety.
        # GTK4 was removed in v0.5.x; TUI (whiptail/dialog) is the only interface.

        # MF007: Direct TCPInterface creation (bypasses connection manager)
        # meshtasticd supports ONE TCP client — direct creation causes thrashing (Issue #17)
        if 'TCPInterface(' in line:
            # Allowlist: files that ARE the connection infrastructure
            conn_infrastructure = (
                'connection_manager.py', 'meshtastic_connection.py', 'connections.py',
            )
            # Files that use the global lock correctly (tracked, not violations)
            lock_aware_files = (
                'node_monitor.py', 'device_controller.py',
                'rns_transport.py', 'mesh_bridge.py',
            )
            basename = os.path.basename(filepath)
            is_infra = any(f in filepath for f in conn_infrastructure)
            is_lock_aware = any(f in filepath for f in lock_aware_files)
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            is_test = '/tests/' in filepath or 'test_' in basename
            if not is_infra and not is_lock_aware and not is_string and not is_comment and not is_test:
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF007",
                    "Direct TCPInterface() creation — use MeshtasticConnection from "
                    "connection_manager.py or acquire MESHTASTIC_CONNECTION_LOCK first (Issue #17)"
                ))

        # MF008: Raw systemctl for service state decisions (bypasses service_check)
        if 'systemctl' in line and 'subprocess' in line:
            basename = os.path.basename(filepath)
            # Only flag state-determining calls, not display-only (status --no-pager)
            is_state_check = (
                "'is-active'" in line or '"is-active"' in line or
                "'restart'" in line or '"restart"' in line or
                "'start'" in line or '"start"' in line or
                "'stop'" in line or '"stop"' in line or
                "'enable'" in line or '"enable"' in line
            )
            is_display_only = '--no-pager' in line or "'status'" in line or '"status"' in line
            is_service_check = 'service_check.py' in filepath
            is_string = stripped.startswith('"') or stripped.startswith("'")
            if is_state_check and not is_display_only and not is_service_check and not is_string:
                issues.append(LintIssue(
                    filepath, lineno, Severity.WARNING, "MF008",
                    "Raw systemctl call — use helpers from utils.service_check instead (Issue #20)"
                ))

        # MF009: RNS.Reticulum() without configdir
        # Without configdir, RNS reads user config with interfaces → EADDRINUSE (Issue #12)
        if 'Reticulum(' in line and 'configdir' not in line:
            basename = os.path.basename(filepath)
            is_test = '/tests/' in filepath or 'test_' in basename
            is_comment = stripped.startswith('#')
            is_string = stripped.startswith('"') or stripped.startswith("'")
            # Only flag actual code calls — pattern: assignment or standalone call
            # e.g. "self._reticulum = RNS.Reticulum(" or "reticulum = RNS.Reticulum("
            is_actual_call = bool(re.search(
                r'=\s*\w*\.?Reticulum\s*\(', line
            ))
            if not is_test and not is_comment and not is_string and is_actual_call:
                # Check if configdir is on the next few lines (multi-line call)
                line_idx = content.find(line)
                if line_idx != -1:
                    following = content[line_idx:line_idx + 300]
                    if 'configdir' not in following.split(')')[0]:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.ERROR, "MF009",
                            "RNS.Reticulum() without configdir= — will cause EADDRINUSE "
                            "when rnsd is running (Issue #12)"
                        ))

        # MF019: RNS.Reticulum() constructed outside the guarded chokepoint.
        # The RNS T2-isolate arc (ported from MeshForge, 2026-05-31) routes ALL
        # in-process RNS init through utils/rns_init.py::open_reticulum so a
        # wedged rnsd degrades (#68 fail-open) instead of hanging the calling
        # thread, and a foreign @rns owner fails loud (#69). Raw construction
        # elsewhere reintroduces the silent-hang class. Mirror of MF007.
        if 'Reticulum(' in line:
            basename = os.path.basename(filepath)
            is_test = '/tests/' in filepath or 'test_' in basename
            is_comment = stripped.startswith('#')
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_actual_call = bool(
                re.search(r'=\s*\w*\.?Reticulum\s*\(', line)
                or re.search(r'\breturn\s+\w*\.?Reticulum\s*\(', line)
            )
            # Allowlisted homes for an actual RNS.Reticulum() construction:
            #   - utils/rns_init.py — THE chokepoint (open_reticulum + the
            #     watchdog-guarded constructor).
            #   - launcher_tui/handlers/rns_interfaces.py — a `python3 -c`
            #     connectivity probe that runs in an ISOLATED subprocess with
            #     its own subprocess timeout, and deliberately tests NomadNet's
            #     OWN venv RNS (not MeshAnchor's), so it cannot route through
            #     the in-process chokepoint and cannot hang the TUI.
            chokepoint_files = (
                'utils/rns_init.py',
                'launcher_tui/handlers/rns_interfaces.py',
            )
            is_allowed = any(f in filepath for f in chokepoint_files)
            if (is_actual_call and not is_test and not is_comment
                    and not is_string and not is_allowed):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF019",
                    "RNS.Reticulum() constructed outside the guarded chokepoint "
                    "— use open_reticulum() from utils.rns_init (degrades on a "
                    "wedged rnsd instead of hanging the thread; #68/#69). If the "
                    "call is genuinely isolated, add it to the chokepoint "
                    "allowlist in lint.py + TestRNSReticulumChokepoint."
                ))

        # MF020: apply_config_and_restart() return value discarded in a TUI handler.
        # The function returns (success, msg) precisely so callers surface a
        # failed daemon restart; a bare-statement call drops it and feeds the
        # #74-#77 "hardcoded success after an unchecked action" defect class.
        # Honest pattern:
        #   ok, msg = apply_config_and_restart('meshtasticd')
        #   self.ctx.report_action(ok, "Applied", ..., "Restart Failed", msg)
        norm_path = filepath.replace(os.sep, '/')
        if 'launcher_tui/handlers/' in norm_path:
            if re.match(r'^_?apply_config_and_restart\s*\(', stripped):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF020",
                    "apply_config_and_restart() return (bool, msg) discarded — "
                    "bind 'ok, msg = ...' and surface restart failure via "
                    "ctx.report_action (honest-signal class, Issues #74-#77)"
                ))

        # MF011: _nomadnet_rns_checks.py must not contain repair/service logic
        if '_nomadnet_rns_checks.py' in filepath:
            repair_patterns = ['start_service(', 'stop_service(', 'enable_service(', 'chmod(']
            # subprocess is only flagged for service management commands
            subprocess_forbidden = ['systemctl', 'pkill', 'rnstatus', 'rnsd']
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            is_import = 'import' in line or 'safe_import' in line
            if not is_string and not is_comment and not is_import:
                for pattern in repair_patterns:
                    if pattern in line:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.ERROR, "MF011",
                            f"Repair logic in _nomadnet_rns_checks.py — move to "
                            f"_rns_repair.py or diagnostics handler"
                        ))
                        break
                if 'subprocess' in line:
                    for cmd in subprocess_forbidden:
                        if f"'{cmd}'" in line or f'"{cmd}"' in line:
                            issues.append(LintIssue(
                                filepath, lineno, Severity.ERROR, "MF011",
                                f"Service management subprocess in _nomadnet_rns_checks.py — "
                                f"move to _rns_repair.py or diagnostics handler"
                            ))
                            break

        # MF013: bare sqlite3.connect() must go through utils.db_helpers.connect_tuned
        # — closes the fleet-host 2026-04-26 wedge class (1.95 GB rollback-journal
        # DB stalled the sister service 16+ minutes in jbd2_log_wait_commit). The
        # helper itself uses sqlite3.connect (allowed); test fixtures may also.
        if 'sqlite3.connect(' in line:
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            basename = os.path.basename(filepath)
            allowlisted_files = {'db_helpers.py'}
            in_tests = '/tests/' in filepath or basename.startswith('test_')
            if (not is_string and not is_comment and basename not in allowlisted_files
                    and not in_tests):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF013",
                    "Bare sqlite3.connect() — use utils.db_helpers.connect_tuned "
                    "(WAL + sync=NORMAL + 64MB journal cap)"
                ))

        # MF014: Direct MeshCore.create_serial / create_tcp / serial.Serial.
        # MeshCore has no daemon — anyone who opens the device wins exclusive
        # ownership. Direct opens outside meshcore_connection.py race against
        # the gateway handler. Use acquire_for_connect() + register_persistent()
        # for long-running owners, or MeshCoreConnection() for short-lived probes.
        basename_mc = os.path.basename(filepath)
        in_tests_mc = '/tests/' in filepath or basename_mc.startswith('test_')
        if not in_tests_mc:
            allowlisted_mc = {
                'meshcore_connection.py',  # IS the connection infrastructure
                'meshcore_handler.py',     # persistent owner — uses acquire_for_connect()
                'meshcore_radio.py',       # supervisor — Session 2 persistent owner
            }
            is_string_mc = stripped.startswith('"') or stripped.startswith("'")
            is_comment_mc = stripped.startswith('#')
            if not is_string_mc and not is_comment_mc and basename_mc not in allowlisted_mc:
                if 'MeshCore.create_serial(' in line or 'MeshCore.create_tcp(' in line:
                    issues.append(LintIssue(
                        filepath, lineno, Severity.ERROR, "MF014",
                        "Direct MeshCore.create_serial/create_tcp() — wrap in "
                        "acquire_for_connect() and call register_persistent() "
                        "from utils.meshcore_connection"
                    ))
                # Raw pyserial Serial() — only legitimate inside the probe helper.
                if re.search(r'\bserial\.Serial\(', line):
                    # The Meshtastic side uses pyserial too; allow files that
                    # don't touch MeshCore-class devices (/dev/ttyMeshCore,
                    # ttyACM/ttyUSB device-paths derived from MeshCore config).
                    is_meshcore_file = (
                        'meshcore' in filepath.lower()
                        or 'ttyMeshCore' in line
                    )
                    if is_meshcore_file:
                        issues.append(LintIssue(
                            filepath, lineno, Severity.ERROR, "MF014",
                            "Raw serial.Serial() on a MeshCore device — use "
                            "MeshCoreConnection() from utils.meshcore_connection"
                        ))

        # MF016: @patch('src.utils.paths.…') silently no-ops because production
        # code imports via `from utils.paths import …` and conftest puts only
        # `src/` on sys.path — `src.utils.paths` and `utils.paths` resolve to
        # different module objects with different ReticulumPaths class objects.
        # See sister-repo project_ci_red_2026_05_03_cascade.md for the full
        # diagnosis. Cure: patch at the consumer's namespace OR use bare
        # 'utils.paths.…'.
        basename_lc = os.path.basename(filepath)
        if (basename_lc.startswith('test_') or '/tests/' in filepath) and '@patch' in line:
            if re.search(r"@patch\(\s*['\"]src\.utils\.paths\.", line):
                issues.append(LintIssue(
                    filepath, lineno, Severity.ERROR, "MF016",
                    "@patch('src.utils.paths.…') silently no-ops — production "
                    "imports via 'from utils.paths import …' (different module "
                    "object). Use 'utils.paths.…' or patch at the consumer's "
                    "namespace (Issue: 2026-05-03 CI cascade)"
                ))

        # MF010: time.sleep() in daemon loops (should use _stop_event.wait())
        if 'time.sleep(' in line:
            is_string = stripped.startswith('"') or stripped.startswith("'")
            is_comment = stripped.startswith('#')
            if not is_string and not is_comment:
                # Check if we're inside a daemon loop method
                func_match = content.rfind('def ', 0, content.find(line))
                if func_match != -1:
                    func_sig = content[func_match:func_match + 200].split('\n')[0]
                    daemon_patterns = ('_loop', '_run', 'run_forever', '_poll', '_monitor')
                    if any(p in func_sig for p in daemon_patterns):
                        issues.append(LintIssue(
                            filepath, lineno, Severity.WARNING, "MF010",
                            "time.sleep() in daemon loop — use _stop_event.wait() for clean shutdown"
                        ))

        return issues

    def lint_files(self, files: List[str]) -> List[LintIssue]:
        """Lint multiple files."""
        all_issues = []
        for f in files:
            if os.path.isfile(f):
                all_issues.extend(self.lint_file(f))
        return all_issues


def get_staged_files() -> List[str]:
    """Get list of staged Python files."""
    try:
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True,
            text=True,
            timeout=10
        )
        files = [f for f in result.stdout.strip().split('\n') if f.endswith('.py')]
        return files
    except Exception:
        return []


def get_all_python_files(directory: str = 'src') -> List[str]:
    """Get all Python files in directory."""
    files = []
    for root, _, filenames in os.walk(directory):
        for f in filenames:
            if f.endswith('.py'):
                files.append(os.path.join(root, f))
    return files


# MF012: Context-loaded docs must stay small so per-conversation overhead is
# bounded. When a doc trips this cap, move the oldest fully-resolved issues to
# the companion archive file and leave a one-row summary in the in-file
# archived-issues table. DO NOT raise the limit to make a tripped check pass.
CONTEXT_DOC_LIMITS = {
    '.claude/foundations/persistent_issues.md': 40_000,
}


# MA017: hardened systemd units (ProtectHome=read-only) must whitelist all
# three canonical MeshAnchor data buckets in ReadWritePaths=. Ported from
# MeshForge MF017 (commit 2026-05-18 reliability sprint). The taxonomy
# matches db_inventory._meshanchor_*_dir() — the bucket-class contract.
#
# Drift between the code's data path (e.g. _meshanchor_data_dir() in
# utils/db_inventory.py) and the unit's ReadWritePaths= is the Issue #58
# class on the MeshForge side: a hardened service stays "active (running)"
# while every write fails in a callback exception. MeshAnchor has the
# same trap surface (meshanchor.service, meshcore-radio.service both
# declare ProtectHome=read-only) but lacked the lint rule until now.
#
# Audit rule: every hardened unit (ProtectHome=read-only or yes) with a
# ReadWritePaths= line must include all three meshanchor buckets. Use
# an inline "# audit-skip: <reason>" comment on the ReadWritePaths line
# to explicitly opt out for a service that genuinely needs less (the
# marker is the signal that the omission is deliberate, not drift).
MA017_REQUIRED_BUCKETS = (".config/meshanchor", ".local/share/meshanchor", ".cache/meshanchor")

# Where MeshAnchor stores systemd unit templates. Unlike MeshForge
# (which uses contrib/systemd/*.service.in), MeshAnchor keeps its
# units under scripts/*.service (no .in suffix; installed by
# install_noc.sh with operator-substituted values at install time).
MA017_UNIT_DIRS = ("scripts",)
MA017_UNIT_EXTENSIONS = (".service",)


def _audit_one_systemd_unit(
    rel_path: str,
    content: str,
    required_buckets: tuple,
) -> List[LintIssue]:
    """Audit one hardened systemd unit for ReadWritePaths bucket coverage.

    Returns issues for this unit only. Caller iterates units. Factored
    so a per-unit ``# audit-skip:`` marker doesn't suppress later units
    in the same run — the MeshForge port of MF017 had a `return` here
    that exited the whole audit function (pattern-audit Finding #4,
    2026-05-19).
    """
    issues: List[LintIssue] = []
    if 'ProtectHome=read-only' not in content and 'ProtectHome=yes' not in content:
        return issues

    whitelisted = []
    rwp_lineno = 0
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.split('#', 1)[0].strip()
        if stripped.startswith('ReadWritePaths='):
            if '# audit-skip:' in line:
                return issues
            if rwp_lineno == 0:
                rwp_lineno = lineno
            rest = stripped[len('ReadWritePaths='):]
            whitelisted.extend(rest.split())

    if not whitelisted:
        return issues

    for bucket in required_buckets:
        if not any(bucket in p for p in whitelisted):
            issues.append(LintIssue(
                rel_path, rwp_lineno, Severity.ERROR, "MA017",
                f"hardened systemd unit (ProtectHome=read-only) missing "
                f"'{bucket}' in ReadWritePaths= — Issue #58 class. Add "
                f"the bucket explicitly, OR mark the omission deliberate "
                f"with an inline '# audit-skip: <reason>' comment.",
            ))
    return issues


def check_systemd_sandbox_paths(repo_root: str = '.') -> List[LintIssue]:
    """MA017: hardened systemd units must whitelist all three meshanchor data buckets."""
    issues: List[LintIssue] = []
    for unit_dir in MA017_UNIT_DIRS:
        full_dir = os.path.join(repo_root, unit_dir)
        if not os.path.isdir(full_dir):
            continue
        for fname in sorted(os.listdir(full_dir)):
            if not any(fname.endswith(ext) for ext in MA017_UNIT_EXTENSIONS):
                continue
            full = os.path.join(full_dir, fname)
            rel_path = os.path.relpath(full, repo_root)
            try:
                with open(full, 'r', encoding='utf-8') as f:
                    content = f.read()
            except OSError:
                continue
            issues.extend(_audit_one_systemd_unit(
                rel_path, content, MA017_REQUIRED_BUCKETS,
            ))
    return issues


# ─────────────────────────────────────────────────────────────────────────
# MA022: pip/apt hygiene in shell installers (install-hardening arc, ported
# from MeshForge MF022). Shell scripts must route package installs through
# scripts/lib/install_common.sh so pip-presence (ensure_pip), PEP 668, and the
# REAL exit code are handled — the fresh-user "had to install pip by hand"
# failure + the `pip … | tail` exit-code mask. `lint_file` is .py-only, so
# (like MA017) MA022 scans shell files via its own full-tree pass.
# ─────────────────────────────────────────────────────────────────────────
MA022_SCAN_EXTENSIONS = {'.sh', '.bash'}
MA022_EXCLUDE_DIRS = {
    '.git', 'venv', '.venv', '__pycache__', 'node_modules', '.pytest_cache',
    '.tox', '.cache', '.mypy_cache', '.ruff_cache', 'dist', 'build', '.eggs',
}

# The lib DEFINES the sanctioned wrappers (it legitimately constructs `pip
# install` / `apt-get install`); lint.py + the rule's own test carry example
# strings. Exempt them.
MA022_ALLOWED_FILES = {
    'scripts/lib/install_common.sh',
    'scripts/lint.py',
    'tests/test_lint_ma022.py',
}

MA022_PIPE_MASK = re.compile(r'\bpip3?\s+install\b.*\|\s*(tail|head)\b')
MA022_BARE_PIP = re.compile(r'\bpip3?\s+install\b')
MA022_APT_SWALLOW = re.compile(r'\bapt(-get)?\s+install\b.*&>\s*/dev/null')


def _ma022_match_in_quotes(line: str, pos: int) -> bool:
    """True when the match at `pos` sits inside a quoted string — i.e. it is a
    fix-hint / echo / dry-run preview, not an actual command. An odd count of
    quotes before the match means we are inside one."""
    prefix = line[:pos]
    return (prefix.count('"') % 2 == 1) or (prefix.count("'") % 2 == 1)


def _check_pip_invocations_in_file(filepath: str, rel_path: str) -> List[LintIssue]:
    issues: List[LintIssue] = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for lineno, line in enumerate(f, 1):
                if line.lstrip().startswith('#'):
                    continue
                m = MA022_PIPE_MASK.search(line)
                if m and not _ma022_match_in_quotes(line, m.start()):
                    issues.append(LintIssue(
                        rel_path, lineno, Severity.ERROR, "MA022",
                        "pip install piped to tail/head masks pip's exit code — route "
                        "through mf_pip_install (scripts/lib/install_common.sh) and check rc",
                    ))
                    continue
                m = MA022_BARE_PIP.search(line)
                if (m and not _ma022_match_in_quotes(line, m.start())
                        and '-m pip' not in line and 'mf_pip_install' not in line):
                    issues.append(LintIssue(
                        rel_path, lineno, Severity.WARNING, "MA022",
                        "bare 'pip install' in a shell script — route through mf_pip_install "
                        "(scripts/lib/install_common.sh) for pip-presence + PEP 668 + checked rc",
                    ))
                    continue
                m = MA022_APT_SWALLOW.search(line)
                if m and not _ma022_match_in_quotes(line, m.start()):
                    issues.append(LintIssue(
                        rel_path, lineno, Severity.WARNING, "MA022",
                        "apt-get install with &>/dev/null hides the failure reason — "
                        "use mf_apt_install (scripts/lib/install_common.sh)",
                    ))
    except (IOError, OSError):
        pass
    return issues


def _ma022_exempt(rel_path: str) -> bool:
    if rel_path in MA022_ALLOWED_FILES:
        return True
    ext = os.path.splitext(rel_path)[1].lower()
    return ext not in MA022_SCAN_EXTENSIONS


def check_pip_invocations_full_tree(repo_root: str = '.') -> List[LintIssue]:
    """MA022: scan the whole repo tree's shell scripts for pip/apt hygiene."""
    issues: List[LintIssue] = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in MA022_EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, repo_root)
        for filename in files:
            rel_path = os.path.normpath(os.path.join(rel_root, filename)) if rel_root != '.' else filename
            if _ma022_exempt(rel_path):
                continue
            issues.extend(_check_pip_invocations_in_file(os.path.join(root, filename), rel_path))
    return issues


def check_context_doc_sizes(repo_root: str = '.') -> List[LintIssue]:
    """MF012: enforce char-size caps on docs routinely loaded into model context."""
    issues: List[LintIssue] = []
    for rel_path, limit in CONTEXT_DOC_LIMITS.items():
        full = os.path.join(repo_root, rel_path)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size > limit:
            issues.append(LintIssue(
                rel_path, 0, Severity.ERROR, "MF012",
                f"File is {size:,} chars (limit {limit:,}). "
                f"Move oldest resolved issues to the archive; do not raise the limit.",
            ))
    return issues


def main():
    parser = argparse.ArgumentParser(description='MeshAnchor Linter')
    parser.add_argument('files', nargs='*', help='Files to lint')
    parser.add_argument('--all', action='store_true', help='Lint all Python files in src/')
    parser.add_argument('--staged', action='store_true', help='Lint staged files only')
    parser.add_argument('--format', choices=['text', 'json', 'github'], default='text',
                       help='Output format')
    parser.add_argument('--severity', choices=['error', 'warning', 'info'], default='info',
                       help='Minimum severity to report')
    args = parser.parse_args()

    # Determine files to lint
    if args.all:
        files = get_all_python_files('src')
    elif args.staged:
        files = get_staged_files()
    elif args.files:
        files = args.files
    else:
        # Default: lint src/
        files = get_all_python_files('src')

    if not files:
        print("No files to lint.")
        return 0

    # Run linter
    linter = MeshAnchorLinter()
    issues = linter.lint_files(files)

    # MF012: doc-size cap (skip in --staged mode — only relevant to whole-repo checks)
    if not args.staged:
        issues.extend(check_context_doc_sizes())

    # MA017: hardened-systemd-unit sandbox path audit (skip in --staged
    # mode — only relevant when whole-repo state is being checked).
    if not args.staged:
        issues.extend(check_systemd_sandbox_paths())

    # MA022: shell-installer pip/apt hygiene (.sh/.bash); full-tree like MA017.
    if not args.staged:
        issues.extend(check_pip_invocations_full_tree())

    # Filter by severity
    severity_order = {'error': 0, 'warning': 1, 'info': 2}
    min_severity = severity_order[args.severity]
    issues = [i for i in issues if severity_order[i.severity.value] <= min_severity]

    # Output results
    if args.format == 'json':
        import json
        print(json.dumps([{
            'file': i.file,
            'line': i.line,
            'severity': i.severity.value,
            'code': i.code,
            'message': i.message
        } for i in issues], indent=2))
    elif args.format == 'github':
        for issue in issues:
            level = 'error' if issue.severity == Severity.ERROR else 'warning'
            print(f"::{level} file={issue.file},line={issue.line}::{issue.code}: {issue.message}")
    else:
        for issue in issues:
            print(issue)

    # Summary
    errors = sum(1 for i in issues if i.severity == Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity == Severity.WARNING)

    if issues:
        print(f"\nFound {len(issues)} issues ({errors} errors, {warnings} warnings)")

    # Exit with error if there are errors
    return 1 if errors > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
