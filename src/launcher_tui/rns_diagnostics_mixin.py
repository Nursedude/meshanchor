"""
RNS Diagnostics Mixin - RNS health checks, tool execution, and port diagnostics.

Extracted from rns_menu_mixin.py to reduce file size per CLAUDE.md guidelines.
"""

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen
from utils.safe_import import safe_import

check_process_running, check_udp_port, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'check_udp_port'
)

get_identity_path, create_identities, list_known_destinations, \
    check_connectivity, get_status, _HAS_RNS_COMMANDS = safe_import(
    'commands.rns',
    'get_identity_path', 'create_identities', 'list_known_destinations',
    'check_connectivity', 'get_status',
)

detect_rnsd_config_drift, _HAS_CONFIG_DRIFT = safe_import(
    'utils.config_drift', 'detect_rnsd_config_drift'
)


class RNSDiagnosticsMixin:
    """Mixin providing RNS diagnostics and tool execution functionality."""

    def _rns_diagnostics(self):
        """Run comprehensive RNS diagnostics."""
        clear_screen()
        print("=== RNS Diagnostics ===\n")

        if not _HAS_RNS_COMMANDS:
            print("RNS commands module not available.")
            print("Run from MeshForge root: sudo python3 src/launcher_tui/main.py")
            self._wait_for_enter()
            return

        # 1. Service status
        print("[1/5] Checking rnsd service...")
        status = get_status()
        status_data = status.data or {}
        running = status_data.get('rnsd_running', False)
        service_state = status_data.get('service_state', '')
        print(f"  rnsd: {'RUNNING' if running else 'NOT RUNNING'}")
        if status_data.get('rnsd_pid'):
            print(f"  PID: {status_data['rnsd_pid']}")
        if service_state:
            print(f"  State: {service_state}")

        # Detect NomadNet conflict (common cause of rnsd crash-loops)
        nomadnet_conflict = self._check_nomadnet_conflict()
        if nomadnet_conflict:
            print(f"  NomadNet: RUNNING (port conflict!)")
        if service_state == 'failed' or (not running and nomadnet_conflict):
            print("")
            if nomadnet_conflict:
                print("  WARNING: NomadNet is holding the RNS shared instance port.")
                print("  rnsd cannot bind port 37428 while NomadNet is running.")
                print("  Fix: stop NomadNet first, or disable rnsd and let NomadNet")
                print("  serve as the shared instance.")
            elif service_state == 'failed':
                print("  WARNING: rnsd has crashed. Check logs:")
                print("    sudo journalctl -u rnsd -n 30")

        # 2. Config check
        print("\n[2/5] Checking configuration...")
        config_exists = status_data.get('config_exists', False)
        print(f"  Config: {'found' if config_exists else 'MISSING'}")
        if config_exists:
            iface_count = status_data.get('interface_count', 0)
            print(f"  Interfaces: {iface_count}")

        # 3. Identity check
        print("\n[3/5] Checking identity...")
        identity_exists = status_data.get('identity_exists', False)
        print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
        config_dir = ReticulumPaths.get_config_dir()
        rns_identity = config_dir / 'identity'
        print(f"  RNS identity: {'found' if rns_identity.exists() else 'not created'}")

        # 4. Full connectivity check
        print("\n[4/5] Running connectivity check...")
        conn = check_connectivity()
        conn_data = conn.data or {}
        print(f"  RNS importable: {'yes' if conn_data.get('can_import_rns') else 'NO'}")
        if conn_data.get('rns_version'):
            print(f"  RNS version: {conn_data['rns_version']}")
        print(f"  Config valid: {'yes' if conn_data.get('config_valid') else 'NO'}")
        print(f"  Interfaces enabled: {conn_data.get('interfaces_enabled', 0)}")

        # Collect issues and warnings from connectivity check
        issues = list(conn_data.get('issues', []))
        warnings = list(conn_data.get('warnings', []))

        # 5. Interface dependencies
        print("\n[5/5] Checking interface dependencies...")
        try:
            blocking = self._find_blocking_interfaces()
            if blocking:
                for iface_name, reason, fix in blocking:
                    print(f"  ! [{iface_name}] {reason}")
                    print(f"    Fix: {fix}")
                    issues.append(f"Blocking interface: {iface_name}")
            else:
                print("  All enabled interfaces have their dependencies met")
        except Exception as e:
            logger.debug("Interface dependency check failed: %s", e)
            print(f"  Could not check: {e}")

        # Check if shared instance port is actually listening
        # RNS uses UDP port 37428, NOT TCP — must use UDP bind test
        try:
            if _HAS_SERVICE_CHECK:
                port_ok = check_udp_port(37428)
            else:
                # Fallback: try UDP bind test inline
                import socket
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(1)
                    sock.bind(('127.0.0.1', 37428))
                    sock.close()
                    port_ok = False  # Bind succeeded = port NOT in use
                except OSError as e:
                    port_ok = e.errno in (98, 48, 10048)  # EADDRINUSE = port in use
                finally:
                    try:
                        sock.close()
                    except Exception:
                        pass
            if running and not port_ok:
                print("  ! rnsd running but port 37428 NOT listening")
                warnings.append("rnsd active but shared instance port not bound")
            elif running and port_ok:
                print(f"  Shared instance port 37428: listening")
        except Exception:
            pass

        # Summary
        if issues:
            print(f"\n--- Issues Found ({len(issues)}) ---")
            for issue in issues:
                print(f"  ! {issue}")
        if warnings:
            print(f"\n--- Warnings ({len(warnings)}) ---")
            for warning in warnings:
                print(f"  ~ {warning}")

        if not issues and not warnings:
            print("\n--- All checks passed ---")
        elif not issues:
            print("\n--- Connectivity OK (with warnings) ---")

        # Offer to create missing identities
        if not identity_exists or not rns_identity.exists():
            print("\n--- Identity Setup ---")
            if self.dialog.yesno(
                "Create Identities",
                "One or more RNS identities are missing.\n\n"
                "Create them now?\n\n"
                "  • RNS identity: used by rnsd for network presence\n"
                "  • Gateway identity: used by MeshForge bridge"
            ):
                try:
                    result = create_identities()
                    if result.success:
                        print(f"  ✓ {result.message}")
                        created = (result.data or {}).get('created', [])
                        if 'rns' in created:
                            print(f"    RNS identity: {result.data['rns_identity']}")
                        if 'gateway' in created:
                            print(f"    Gateway identity: {result.data['gateway_identity']}")
                    else:
                        print(f"  ✗ {result.message}")
                except Exception as e:
                    print(f"  ✗ Identity creation failed: {e}")

        # RNS tool availability
        print("\n--- RNS Tool Availability ---")
        for tool in ['rnsd', 'rnstatus', 'rnpath', 'rnprobe', 'rnid', 'rncp', 'rnx']:
            path = shutil.which(tool)
            if path:
                print(f"  {tool}: {path}")
            else:
                print(f"  {tool}: not found")

        self._wait_for_enter()

    def _rns_config_drift_check(self):
        """Check for config drift between gateway and rnsd."""
        clear_screen()
        print("=== RNS Config Drift Check ===\n")
        print("Comparing gateway config path vs rnsd actual path...\n")

        if not _HAS_CONFIG_DRIFT:
            print("  Config drift module not available.")
            print("  File: src/utils/config_drift.py")
            self._wait_for_enter()
            return

        result = detect_rnsd_config_drift()

        # Display result
        severity_colors = {
            'info': '\033[0;34m',     # blue
            'warning': '\033[0;33m',  # yellow
            'error': '\033[0;31m',    # red
        }
        color = severity_colors.get(result.severity, '')
        reset = '\033[0m'

        if result.drifted:
            print(f"  {color}CONFIG DRIFT DETECTED{reset}\n")
            print(f"  Gateway resolves to: {result.gateway_config_dir}")
            print(f"  rnsd actually uses:   {result.rnsd_config_dir}")
            print(f"  Detection method:     {result.detection_method}")
            if result.rnsd_pid:
                print(f"  rnsd PID:             {result.rnsd_pid}")
            print(f"\n  {color}Fix:{reset} {result.fix_hint}")
        else:
            print(f"  \033[0;32mNo drift detected\033[0m\n")
            print(f"  {result.message}")
            if result.gateway_config_dir:
                print(f"  Config directory: {result.gateway_config_dir}")
            if result.rnsd_pid:
                print(f"  rnsd PID: {result.rnsd_pid}")
            print(f"  Detection method: {result.detection_method}")

        print()
        self._wait_for_enter()

    def _run_rns_tool(self, cmd: list, tool_name: str):
        """Run an RNS CLI tool with address-in-use error detection.

        Captures both stdout and stderr to detect specific error patterns.
        RNS logs errors to stdout in some configurations, so both streams
        must be checked for the 'Address already in use' pattern.

        Args:
            cmd: Command and arguments to run
            tool_name: Display name for error messages (e.g., "rnpath")
        """
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            # RNS tools may log errors to stdout or stderr depending on config
            combined = (result.stdout or "") + (result.stderr or "")

            if result.returncode == 0:
                # Success - show normal output
                if result.stdout:
                    print(result.stdout, end='')
            elif "address already in use" in combined.lower():
                # Suppress noisy traceback, show actionable diagnostics
                print("\nError: RNS port conflict (Address already in use)")
                print("Another process is bound to the RNS AutoInterface port.\n")
                self._diagnose_rns_port_conflict()
            elif "no shared" in combined.lower() or "could not connect" in combined.lower() or "could not get" in combined.lower() or "shared instance" in combined.lower() or "authenticationerror" in combined.lower() or "digest" in combined.lower():
                # RNS shared instance issue — DIAGNOSE, don't auto-fix.
                # Auto-fix was the #1 source of regressions (see persistent_issues.md).
                # Policy: show what's wrong, let the user decide what to do.
                print(f"\nRNS connectivity issue detected.")

                # Check if rnsd is actually running
                rnsd_running = False
                try:
                    r = subprocess.run(
                        ['systemctl', 'is-active', 'rnsd'],
                        capture_output=True, text=True, timeout=5
                    )
                    rnsd_running = r.stdout.strip() == 'active'
                except (subprocess.SubprocessError, OSError):
                    pass

                if not rnsd_running:
                    # rnsd is NOT running — offer to start it (with user consent)
                    print("rnsd is not running.\n")
                    if self.dialog.yesno(
                        "rnsd Not Running",
                        f"{tool_name} failed because rnsd is not running.\n\n"
                        "Start rnsd now?\n\n"
                        "If rnsd won't start, use RNS > Diagnostics to investigate.",
                    ):
                        try:
                            subprocess.run(
                                ['systemctl', 'start', 'rnsd'],
                                capture_output=True, text=True, timeout=30
                            )
                            print("Starting rnsd...")
                            if self._wait_for_rns_port(max_wait=10):
                                print(f"rnsd started. Retrying {tool_name}...\n")
                                retry_result = subprocess.run(
                                    cmd, capture_output=True, text=True, timeout=15
                                )
                                if retry_result.returncode == 0 and retry_result.stdout:
                                    print(retry_result.stdout, end='')
                                else:
                                    self._diagnose_rns_connectivity(
                                        (retry_result.stdout or "") + (retry_result.stderr or "")
                                    )
                            else:
                                print("rnsd started but port 37428 not listening.")
                                print("Check: sudo journalctl -u rnsd -n 20")
                                print("Or run: RNS > Diagnostics from the menu.")
                        except (subprocess.SubprocessError, OSError) as e:
                            print(f"Failed to start rnsd: {e}")
                    else:
                        print("To start rnsd manually: sudo systemctl start rnsd")
                else:
                    # rnsd IS running but tools can't connect.
                    # Most common cause: rnsd still initializing (crypto, interfaces).
                    # Wait for port 37428 before showing diagnostics.
                    print("rnsd is running — waiting for port 37428...")
                    port_ready = self._wait_for_rns_port()
                    if port_ready:
                        # Port came up — retry the tool
                        print(f"Port ready. Retrying {tool_name}...\n")
                        retry_result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=15
                        )
                        if retry_result.returncode == 0 and retry_result.stdout:
                            print(retry_result.stdout, end='')
                        else:
                            # Port is up but tool still fails — auth or config issue
                            self._diagnose_rns_connectivity(
                                (retry_result.stdout or "") + (retry_result.stderr or "")
                            )
                    else:
                        # Port never came up — show diagnostics
                        self._diagnose_rns_connectivity(combined)
            else:
                # Other error - DON'T auto-fix, just show output
                # RNS tools may return non-zero for benign reasons (empty table, no paths)
                if result.stdout:
                    print(result.stdout, end='')
                if result.stderr and result.stderr.strip():
                    # Only show stderr if it contains actual error info
                    stderr_lower = result.stderr.lower()
                    if "error" in stderr_lower or "failed" in stderr_lower or "exception" in stderr_lower:
                        print(f"\nNote: {tool_name} reported an issue:")
                        for line in result.stderr.strip().split('\n')[-3:]:
                            print(f"  {line}")
        except FileNotFoundError:
            print(f"\n{tool_name} not found. Is RNS installed?")
            print("Install: pipx install rns")
        except subprocess.TimeoutExpired:
            print(f"\n{tool_name} timed out. RNS may be unresponsive.")
            print("Try restarting rnsd: sudo systemctl restart rnsd")

    def _wait_for_rns_port(self, max_wait: int = 10) -> bool:
        """Wait for rnsd to start listening on UDP port 37428.

        Polls the port with 1-second intervals using UDP bind test.
        Returns True if port becomes available, False if timeout expires.
        """
        for i in range(max_wait):
            if _HAS_SERVICE_CHECK:
                if check_udp_port(37428):
                    return True
            else:
                # Fallback: inline UDP bind test
                import socket
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.settimeout(1)
                    sock.bind(('127.0.0.1', 37428))
                    sock.close()
                    # Bind succeeded = port NOT in use, keep waiting
                except OSError as e:
                    if e.errno in (98, 48, 10048):  # EADDRINUSE
                        return True
                finally:
                    try:
                        sock.close()
                    except Exception:
                        pass
            time.sleep(1)
        return False

    def _diagnose_rns_connectivity(self, error_output: str):
        """Show targeted diagnostics when rnsd is running but tools can't connect.

        Instead of guessing, check for auth errors (actionable) then fall
        through to showing the actual rnsd journal log.
        """
        lower = error_output.lower()
        print("rnsd is running but RNS tools cannot connect.\n")

        # Auth errors are actionable — detect and show specific fix
        if "authenticationerror" in lower or "digest" in lower:
            print("Cause: RPC authentication mismatch (stale auth tokens)")
            print("Fix:   Clear auth tokens and restart rnsd:\n")
            print("  sudo systemctl stop rnsd")
            print("  sudo rm -f /etc/reticulum/storage/shared_instance_*")
            print("  sudo rm -f /root/.reticulum/storage/shared_instance_*")
            user_home = get_real_user_home()
            print(f"  rm -f {user_home}/.reticulum/storage/shared_instance_*")
            print("  sudo systemctl start rnsd")
            return

        # Check for blocking interfaces (most common root cause)
        blocking = self._find_blocking_interfaces()
        if blocking:
            print("Cause: rnsd is stuck initializing a blocking interface.\n")
            for iface_name, reason, fix in blocking:
                print(f"  [{iface_name}] {reason}")
                print(f"  Fix: {fix}\n")
            print("Options:")
            print("  1. Start the missing dependency (see Fix above)")
            print("  2. Disable the interface in /etc/reticulum/config")
            print("     (change 'enabled = yes' to 'enabled = no')")
            print("  3. sudo systemctl restart rnsd (after fixing)")
            return

        # No specific cause detected — show actual rnsd log
        print("Showing recent rnsd log:\n")
        try:
            r = subprocess.run(
                ['journalctl', '-u', 'rnsd', '-n', '15', '--no-pager'],
                capture_output=True, text=True, timeout=10
            )
            if r.stdout and r.stdout.strip():
                for line in r.stdout.strip().split('\n'):
                    print(f"  {line}")
            else:
                print("  (no log output)")
        except (subprocess.SubprocessError, OSError):
            print("  (could not read journal)")
        print("\nTo restart: sudo systemctl restart rnsd")

    def _check_nomadnet_conflict(self) -> bool:
        """Check if NomadNet is running and holding the shared instance port.

        NomadNet creates its own Reticulum() instance and becomes the shared
        instance on port 37428. If rnsd is also configured with
        share_instance = Yes, they fight over the port causing crash loops.

        Returns True if NomadNet conflict detected.
        """
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def _diagnose_rns_port_conflict(self):
        """Diagnose and offer to fix RNS port conflicts from the TUI."""
        try:
            # Check NomadNet first — most common cause of port conflicts
            if self._check_nomadnet_conflict():
                print("CAUSE: NomadNet is running and owns port 37428.")
                print("rnsd can't start because NomadNet has the port.\n")

                if self.dialog.yesno(
                    "Fix Port Conflict",
                    "NomadNet is holding port 37428.\n\n"
                    "MeshForge can fix this:\n"
                    "  1. Stop NomadNet\n"
                    "  2. Start rnsd (becomes shared instance)\n"
                    "  3. Restart NomadNet (connects as client)\n\n"
                    "Fix now?"
                ):
                    print("Stopping NomadNet...")
                    subprocess.run(
                        ['pkill', '-f', 'nomadnet'],
                        capture_output=True, timeout=5
                    )
                    time.sleep(1)

                    print("Starting rnsd...")
                    subprocess.run(
                        ['systemctl', 'start', 'rnsd'],
                        capture_output=True, text=True, timeout=15
                    )
                    time.sleep(2)

                    print("Restarting NomadNet as client...")
                    subprocess.run(
                        ['systemctl', '--user', 'start', 'nomadnet'],
                        capture_output=True, text=True, timeout=10
                    )
                    print("Done. Startup order: rnsd -> NomadNet -> MeshForge\n")
                return

            # Use centralized service check when available
            if _HAS_SERVICE_CHECK:
                rnsd_running = check_process_running('rnsd')
            else:
                # Fallback to direct pgrep
                result = subprocess.run(
                    ['pgrep', '-f', 'rnsd'],
                    capture_output=True, text=True, timeout=5
                )
                rnsd_running = result.returncode == 0

            if rnsd_running:
                # Get PID for diagnostic message
                try:
                    pid_result = subprocess.run(
                        ['pgrep', '-f', 'rnsd'],
                        capture_output=True, text=True, timeout=5
                    )
                    pid = pid_result.stdout.strip().split('\n')[0] if pid_result.stdout else 'unknown'
                except (subprocess.SubprocessError, OSError) as e:
                    logger.debug("rnsd PID lookup failed: %s", e)
                    pid = 'unknown'
                print(f"rnsd is running (PID: {pid}) but may need a restart:")
                print("  sudo systemctl restart rnsd")
            else:
                print("No rnsd found. A stale process may be holding the port.")
                print("  Find it:    sudo lsof -i UDP:29716")
                print("  Kill stale: pkill -f rnsd")
                print("  Or wait ~30s for the socket to timeout")
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("RNS port conflict diagnosis failed: %s", e)
            print("  Try: sudo systemctl restart rnsd")
