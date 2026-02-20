"""
RNS Diagnostics Mixin - RNS health checks, tool execution, and port diagnostics.

Extracted from rns_menu_mixin.py to reduce file size per CLAUDE.md guidelines.
"""

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen
from utils.safe_import import safe_import

check_process_running, check_udp_port, start_service, stop_service, _sudo_cmd, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'check_udp_port', 'start_service', 'stop_service', '_sudo_cmd'
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

        # Collect issues and warnings throughout diagnostics
        issues = []
        warnings = []

        # 1. Service status
        print("[1/6] Checking rnsd service...")
        status = get_status()
        status_data = status.data or {}
        running = status_data.get('rnsd_running', False)
        service_state = status_data.get('service_state', '')
        print(f"  rnsd: {'RUNNING' if running else 'NOT RUNNING'}")
        if status_data.get('rnsd_pid'):
            print(f"  PID: {status_data['rnsd_pid']}")
        if service_state:
            print(f"  State: {service_state}")

        # Check rnsd.service file for misplaced directives
        service_file = Path('/etc/systemd/system/rnsd.service')
        if service_file.exists():
            try:
                svc_content = service_file.read_text()
                svc_section = None
                for svc_line in svc_content.splitlines():
                    svc_stripped = svc_line.strip()
                    if svc_stripped.startswith('[') and svc_stripped.endswith(']'):
                        svc_section = svc_stripped
                    elif svc_section == '[Service]' and (
                        'StartLimitIntervalSec' in svc_stripped
                        or 'StartLimitBurst' in svc_stripped
                    ):
                        print(f"  Service file: has misplaced directives in [Service]")
                        warnings.append(
                            "rnsd.service: StartLimitIntervalSec in [Service] "
                            "(should be [Unit]) — run Repair to fix"
                        )
                        break
                else:
                    print(f"  Service file: OK")
            except (OSError, PermissionError):
                print("  Service file: could not read (check permissions)")

        # Detect NomadNet conflict (common cause of rnsd crash-loops)
        nomadnet_conflict = self._check_nomadnet_conflict()
        if nomadnet_conflict:
            print(f"  NomadNet: RUNNING (port conflict!)")
            # Show port 37428 owner for clarity
            try:
                from utils.service_check import get_udp_port_owner
                owner = get_udp_port_owner(37428)
                if owner:
                    proc_name, pid = owner
                    print(f"  Port 37428 owner: {proc_name} (PID {pid})")
            except ImportError:
                pass
        if service_state == 'failed' or (not running and nomadnet_conflict):
            print("")
            if nomadnet_conflict:
                print("  WARNING: NomadNet is holding the RNS shared "
                      "instance port.")
                print("  rnsd cannot bind port 37428 while NomadNet "
                      "is running.")
                print("  Fix: stop NomadNet first, or disable rnsd "
                      "and let NomadNet")
                print("  serve as the shared instance.")
                # Show NomadNet log tail for context
                nn_logfile = (get_real_user_home()
                              / '.nomadnetwork' / 'logfile')
                if nn_logfile.exists():
                    try:
                        import collections
                        with open(nn_logfile, 'r') as f:
                            last_lines = list(
                                collections.deque(f, maxlen=5)
                            )
                        if last_lines:
                            print("\n  Recent NomadNet log entries:")
                            for line in last_lines:
                                print(f"    {line[:100]}")
                    except (OSError, PermissionError):
                        print("    (cannot read NomadNet logfile)")
            elif service_state == 'failed':
                print("  WARNING: rnsd has crashed. Check logs:")
                print("    sudo journalctl -u rnsd -n 30")

        # 2. Config check
        print("\n[2/6] Checking configuration...")
        config_exists = status_data.get('config_exists', False)
        print(f"  Config: {'found' if config_exists else 'MISSING'}")
        if config_exists:
            iface_count = status_data.get('interface_count', 0)
            print(f"  Interfaces: {iface_count}")

        # 3. Identity check
        print("\n[3/6] Checking identity...")
        identity_exists = status_data.get('identity_exists', False)
        print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
        config_dir = ReticulumPaths.get_config_dir()
        rns_identity = config_dir / 'identity'
        print(f"  RNS identity: {'found' if rns_identity.exists() else 'not created'}")

        # 4. Full connectivity check
        print("\n[4/6] Running connectivity check...")
        conn = check_connectivity()
        conn_data = conn.data or {}
        print(f"  RNS importable: {'yes' if conn_data.get('can_import_rns') else 'NO'}")
        if conn_data.get('rns_version'):
            print(f"  RNS version: {conn_data['rns_version']}")
        print(f"  Config valid: {'yes' if conn_data.get('config_valid') else 'NO'}")
        print(f"  Interfaces enabled: {conn_data.get('interfaces_enabled', 0)}")

        # Merge issues and warnings from connectivity check
        issues.extend(conn_data.get('issues', []))
        warnings.extend(conn_data.get('warnings', []))

        # 5. Interface dependencies
        print("\n[5/6] Checking interface dependencies...")
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
                # Show who owns the port (if anyone)
                try:
                    from utils.service_check import get_udp_port_owner
                    owner = get_udp_port_owner(37428)
                    if owner:
                        proc_name, pid = owner
                        print(f"    Port held by: {proc_name} "
                              f"(PID {pid})")
                except ImportError:
                    pass
                # Check if share_instance is enabled in config
                share_ok = conn_data.get('share_instance', None)
                if share_ok is False:
                    print("    Cause: share_instance is not "
                          "enabled in [reticulum] config")
                    print("    Fix: Add 'share_instance = Yes' "
                          "to [reticulum] section,")
                    print("         then restart: sudo systemctl "
                          "restart rnsd")
                    issues.append(
                        "share_instance not enabled — gateway "
                        "cannot connect to rnsd")
                else:
                    # Surface recent journal errors to explain WHY
                    try:
                        r = subprocess.run(
                            ['journalctl', '-u', 'rnsd', '-n', '10',
                             '--no-pager', '-p', 'warning', '-q',
                             '--no-hostname'],
                            capture_output=True, text=True, timeout=10
                        )
                        if r.stdout and r.stdout.strip():
                            print("    Recent rnsd errors:")
                            for line in r.stdout.strip().splitlines()[-5:]:
                                print(f"      {line.strip()[:100]}")
                    except (subprocess.SubprocessError, OSError):
                        pass
                    warnings.append(
                        "rnsd active but shared instance port "
                        "not bound")
            elif running and port_ok:
                print(f"  Shared instance port 37428: listening")
        except Exception:
            pass

        # 6. Interface TX/RX health
        print("\n[6/6] Checking interface traffic...")
        try:
            iface_health = self._check_rns_interface_health()
            if iface_health:
                rx_only_found = False
                for name, tx, rx, healthy in iface_health:
                    if healthy:
                        print(f"  {name}: ↑{tx} ↓{rx}")
                    else:
                        print(f"  {name}: RX-ONLY (↑{tx} ↓{rx})")
                        issues.append(
                            f"Interface {name} is RX-only (no TX)")
                        rx_only_found = True
                if rx_only_found:
                    print("\n  RX-only interfaces = link "
                          "establishment failing.")
                    print("  Common cause: shared instance port "
                          "37428 not bound.")
            else:
                print("  Could not retrieve interface traffic "
                      "(rnstatus not available or rnsd not "
                      "connected)")
        except Exception as e:
            logger.debug("Interface health check failed: %s", e)
            print(f"  Could not check: {e}")

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

            # Offer to fix now if possible
            if result.can_auto_fix:
                print()
                self._offer_drift_fix(result)
            else:
                print()
                self._wait_for_enter()
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

    def _offer_drift_fix(self, drift_result):
        """Offer to fix config drift by migrating to /etc/reticulum/.

        Orchestrates existing primitives:
        1. Migrate config to /etc/reticulum/config (via _migrate_rns_config_to_etc)
        2. Ensure /etc/reticulum/storage/ dirs exist (via ReticulumPaths)
        3. Clear stale auth tokens from all locations
        4. Validate rnsd.service (ExecStart path, systemd directives)
        5. Verify rnsd Python dependencies (install missing packages)
        6. Restart rnsd
        7. Wait for port 37428 and verify drift is resolved
        """
        etc_config = Path('/etc/reticulum/config')

        # Determine the source config to migrate — prefer rnsd's (actively in use)
        source_candidates = []
        if drift_result.rnsd_config_dir:
            source_candidates.append(drift_result.rnsd_config_dir / 'config')
        if drift_result.gateway_config_dir:
            source_candidates.append(drift_result.gateway_config_dir / 'config')

        source = None
        for candidate in source_candidates:
            if candidate.is_file():
                source = candidate
                break

        if source is None:
            print("  Cannot find a source config file to migrate.")
            self._wait_for_enter()
            return

        # Build the dialog message
        if etc_config.exists():
            dialog_text = (
                f"Config drift: gateway and rnsd use different paths.\n\n"
                f"  Gateway: {drift_result.gateway_config_dir}\n"
                f"  rnsd:    {drift_result.rnsd_config_dir}\n\n"
                f"/etc/reticulum/config already exists.\n\n"
                f"MeshForge will:\n"
                f"  1. Keep existing /etc/reticulum/config\n"
                f"  2. Rename old config(s) to .migrated\n"
                f"  3. Clear stale auth tokens\n"
                f"  4. Restart rnsd\n\n"
                f"Fix now?"
            )
        else:
            dialog_text = (
                f"Config drift: gateway and rnsd use different paths.\n\n"
                f"  Gateway: {drift_result.gateway_config_dir}\n"
                f"  rnsd:    {drift_result.rnsd_config_dir}\n\n"
                f"MeshForge will:\n"
                f"  1. Migrate {source} to /etc/reticulum/config\n"
                f"  2. Rename old config to .migrated\n"
                f"  3. Clear stale auth tokens\n"
                f"  4. Restart rnsd\n\n"
                f"Fix now?"
            )

        if not self.dialog.yesno("Fix Config Drift", dialog_text):
            self._wait_for_enter()
            return

        # === Execute the fix ===
        print("\n--- Fixing Config Drift ---\n")

        # Step 1: Migrate config to /etc/reticulum/config
        print("[1/7] Migrating config to /etc/reticulum/...")
        if etc_config.exists():
            print(f"  /etc/reticulum/config already exists — keeping it")
            # Rename competing configs to .migrated so RNS resolution
            # finds /etc/reticulum/config first
            for candidate in source_candidates:
                if candidate.is_file() and not str(candidate).startswith('/etc/'):
                    try:
                        backup = candidate.with_suffix('.migrated')
                        candidate.rename(backup)
                        print(f"  Renamed: {candidate} -> {backup.name}")
                    except (OSError, PermissionError) as e:
                        print(f"  Warning: Could not rename {candidate}: {e}")
        else:
            if self._migrate_rns_config_to_etc(source):
                print(f"  Migrated: {source} -> /etc/reticulum/config")
            else:
                print("  Migration failed. Aborting.")
                self._wait_for_enter()
                return

        # Step 2: Ensure system directories exist with correct permissions
        print("\n[2/7] Ensuring /etc/reticulum/ directory structure...")
        if ReticulumPaths.ensure_system_dirs():
            print("  Directories OK (storage, ratchets, cache, interfaces)")
        else:
            print("  Warning: Could not create all directories (need sudo?)")

        # Step 3: Clear stale auth tokens from all locations
        print("\n[3/7] Clearing stale auth tokens...")
        user_home = get_real_user_home()
        storage_dirs = [
            Path('/etc/reticulum/storage'),
            Path('/root/.reticulum/storage'),
            user_home / '.reticulum' / 'storage',
            user_home / '.config' / 'reticulum' / 'storage',
        ]
        files_cleared = 0
        for storage_dir in storage_dirs:
            if storage_dir.exists():
                for auth_file in storage_dir.glob('shared_instance_*'):
                    try:
                        auth_file.unlink()
                        files_cleared += 1
                        print(f"  Removed: {auth_file}")
                    except (OSError, PermissionError) as e:
                        print(f"  Warning: Could not remove {auth_file}: {e}")
        if files_cleared == 0:
            print("  No stale auth files found")

        # Step 4: Validate rnsd.service file (ExecStart path, directives)
        print("\n[4/7] Validating rnsd.service...")
        service_fixed = self._validate_rnsd_service_file()
        if not service_fixed:
            print("  Service file: OK")

        # Step 5: Verify rnsd Python dependencies
        print("\n[5/7] Checking rnsd Python dependencies...")
        self._ensure_rnsd_dependencies()

        # Step 6: Restart rnsd
        print("\n[6/7] Restarting rnsd...")
        if _HAS_SERVICE_CHECK:
            success, msg = stop_service('rnsd')
            if not success:
                print(f"  Warning stopping rnsd: {msg}")
            time.sleep(1)

            # Reset failed state in case rnsd was in a crash loop
            try:
                subprocess.run(
                    _sudo_cmd(['systemctl', 'reset-failed', 'rnsd']),
                    capture_output=True, timeout=5
                )
            except (subprocess.SubprocessError, OSError):
                pass

            success, msg = start_service('rnsd')
            if success:
                print("  rnsd started")
            else:
                print(f"  Warning: {msg}")
        else:
            print("  Service management not available — restart manually:")
            print("    sudo systemctl restart rnsd")

        # Step 7: Wait for port and verify
        print("\n[7/7] Verifying fix...")
        print("  Waiting for port 37428...")
        port_ok = self._wait_for_rns_port(max_wait=15)

        if port_ok:
            print("  Port 37428: listening")

            # Re-run drift detection to confirm fix
            verify = detect_rnsd_config_drift()
            if not verify.drifted:
                print(f"\n  \033[0;32mDrift resolved!\033[0m Config aligned at: "
                      f"{verify.gateway_config_dir}")
            else:
                print(f"\n  \033[0;33mDrift may persist.\033[0m")
                print(f"  Gateway: {verify.gateway_config_dir}")
                print(f"  rnsd:    {verify.rnsd_config_dir}")
                print("  You may need to restart MeshForge for path resolution to update.")
        else:
            print("  Port 37428 not listening after 15s.")
            print("  rnsd may be slow to initialize or may have crashed.")
            print("  Check: sudo journalctl -u rnsd -n 20")

        print()
        self._wait_for_enter()

    # Known RNS external interface plugins and their pip package dependencies.
    # Key: plugin filename in /etc/reticulum/interfaces/
    # Value: list of (import_name, pip_package) tuples
    _INTERFACE_DEPS = {
        'Meshtastic_Interface.py': [('meshtastic', 'meshtastic')],
    }

    def _ensure_rnsd_dependencies(self):
        """Check that rnsd's Python can import packages required by enabled interfaces.

        Scans /etc/reticulum/interfaces/ for known plugin files, checks if their
        Python dependencies are importable by rnsd's interpreter, and offers to
        install missing packages system-wide via pip.

        Common failure: meshtastic installed via pipx (isolated venv, CLI only)
        but Meshtastic_Interface.py needs it importable by system Python.
        """
        interfaces_dir = Path('/etc/reticulum/interfaces')
        if not interfaces_dir.is_dir():
            print("  No external interfaces directory")
            return

        # Determine rnsd's Python interpreter from its shebang.
        # Check multiple locations in priority order:
        # 1. ExecStart from the service file (the actual binary systemd uses)
        # 2. Venv rnsd (has all dependencies)
        # 3. System rnsd (PATH or /usr/local/bin)
        rnsd_path = None

        # Try ExecStart from the service file first — most accurate
        service_file = Path('/etc/systemd/system/rnsd.service')
        if service_file.exists():
            try:
                svc_content = service_file.read_text()
                exec_match = re.search(r'ExecStart\s*=\s*(.+)', svc_content)
                if exec_match:
                    candidate = Path(exec_match.group(1).strip())
                    if candidate.exists():
                        rnsd_path = candidate
            except (OSError, PermissionError):
                pass

        # Fallback: venv path
        if rnsd_path is None:
            venv_rnsd = Path('/opt/meshforge/venv/bin/rnsd')
            if venv_rnsd.exists():
                rnsd_path = venv_rnsd

        # Fallback: system path
        if rnsd_path is None:
            sys_rnsd = Path('/usr/local/bin/rnsd')
            if sys_rnsd.exists():
                rnsd_path = sys_rnsd
            else:
                rnsd_which = shutil.which('rnsd')
                if rnsd_which:
                    rnsd_path = Path(rnsd_which)
                else:
                    print("  rnsd not found — skipping dependency check")
                    return

        # Read shebang to find which Python rnsd uses
        try:
            first_line = rnsd_path.read_text().split('\n', 1)[0]
            if first_line.startswith('#!'):
                rnsd_python = first_line[2:].strip().split()[0]
            else:
                rnsd_python = 'python3'
        except (OSError, PermissionError):
            rnsd_python = 'python3'

        # Check each known plugin
        missing = []
        for plugin_file, deps in self._INTERFACE_DEPS.items():
            if not (interfaces_dir / plugin_file).exists():
                continue
            for import_name, pip_name in deps:
                try:
                    result = subprocess.run(
                        [rnsd_python, '-c', f'import {import_name}'],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode != 0:
                        missing.append((plugin_file, import_name, pip_name))
                        print(f"  {plugin_file} needs '{import_name}' — NOT installed")
                    else:
                        print(f"  {plugin_file} needs '{import_name}' — OK")
                except (subprocess.SubprocessError, OSError):
                    missing.append((plugin_file, import_name, pip_name))
                    print(f"  {plugin_file} needs '{import_name}' — check failed")

        if not missing:
            print("  All interface dependencies met")
            return

        # Offer to install missing packages
        pkg_list = ', '.join(pip_name for _, _, pip_name in missing)
        if self.dialog.yesno(
            "Install Missing Packages",
            f"rnsd's Python ({rnsd_python}) is missing packages\n"
            f"required by external interface plugins:\n\n"
            + '\n'.join(
                f"  {plugin}: {imp} (pip: {pip})"
                for plugin, imp, pip in missing
            )
            + f"\n\nInstall system-wide with:\n"
            f"  sudo {rnsd_python} -m pip install {pkg_list}\n\n"
            f"Without these, rnsd will crash on startup.\n\n"
            f"Install now?"
        ):
            for _, _, pip_name in missing:
                print(f"  Installing {pip_name}...")
                try:
                    install_cmd = [rnsd_python, '-m', 'pip', 'install',
                                    '--break-system-packages', pip_name]
                    if _HAS_SERVICE_CHECK:
                        base_cmd = _sudo_cmd(install_cmd)
                    elif os.getuid() != 0:
                        base_cmd = ['sudo'] + install_cmd
                    else:
                        base_cmd = install_cmd
                    result = subprocess.run(
                        base_cmd,
                        capture_output=True, text=True, timeout=120
                    )
                    if result.returncode == 0:
                        print(f"  {pip_name}: installed")
                    else:
                        # Detect Debian-managed package conflict:
                        # pip says "installed by debian/apt" when it refuses
                        # to overwrite an apt-owned package.
                        err_text = (result.stderr or result.stdout or '').lower()
                        if 'installed by' in err_text or 'externally-managed' in err_text:
                            print(f"  {pip_name}: Debian package conflict, retrying with --ignore-installed...")
                            retry_cmd = [rnsd_python, '-m', 'pip', 'install',
                                         '--break-system-packages', '--ignore-installed', pip_name]
                            if _HAS_SERVICE_CHECK:
                                retry_cmd = _sudo_cmd(retry_cmd)
                            elif os.getuid() != 0:
                                retry_cmd = ['sudo'] + retry_cmd
                            retry = subprocess.run(
                                retry_cmd,
                                capture_output=True, text=True, timeout=120
                            )
                            if retry.returncode == 0:
                                print(f"  {pip_name}: installed (bypassed Debian package)")
                            else:
                                err_lines = (retry.stderr or retry.stdout or '').strip().split('\n')
                                print(f"  {pip_name}: FAILED (even with --ignore-installed)")
                                if err_lines:
                                    print(f"    {err_lines[-1]}")
                        else:
                            err_lines = (result.stderr or result.stdout or '').strip().split('\n')
                            print(f"  {pip_name}: FAILED")
                            if err_lines:
                                print(f"    {err_lines[-1]}")
                except subprocess.TimeoutExpired:
                    print(f"  {pip_name}: timed out (network issue?)")
                except (subprocess.SubprocessError, OSError) as e:
                    print(f"  {pip_name}: error — {e}")
        else:
            print(f"  Skipped. Install manually: sudo {rnsd_python} -m pip install {pkg_list}")
            print(f"  Without these packages, rnsd will crash on startup.")

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

            # Check for error patterns BEFORE returncode — rnstatus returns
            # exit code 0 even when the shared instance is unreachable.
            lower_combined = combined.lower()
            has_shared_error = any(p in lower_combined for p in (
                "no shared", "could not connect", "could not get",
                "shared instance", "authenticationerror", "digest",
            ))

            if "address already in use" in lower_combined:
                # Suppress noisy traceback, show actionable diagnostics
                print("\nError: RNS port conflict (Address already in use)")
                print("Another process is bound to the RNS AutoInterface port.\n")
                self._diagnose_rns_port_conflict()
            elif has_shared_error:
                # RNS shared instance issue — DIAGNOSE, don't auto-fix.
                # Auto-fix was the #1 source of regressions (see persistent_issues.md).
                # Policy: show what's wrong, let the user decide what to do.
                # Don't dump raw stdout — the diagnostic path below shows actionable info.
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
                            start_service('rnsd')
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
            elif result.returncode == 0:
                # Clean success — no error patterns detected
                if result.stdout:
                    print(result.stdout, end='')
            else:
                # Other error - DON'T auto-fix, just show output
                # RNS tools may return non-zero for benign reasons (empty table, no paths)
                if result.stdout:
                    print(result.stdout, end='')
                if result.stderr and result.stderr.strip():
                    # Show concise error hint — not raw tracebacks
                    stderr_lower = result.stderr.lower()
                    if "error" in stderr_lower or "traceback" in stderr_lower or "exception" in stderr_lower:
                        print(f"\n{tool_name} reported an error. Check: sudo journalctl -u rnsd -n 10")
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

    def _check_rns_interface_health(self):
        """Run rnstatus and parse per-interface TX/RX counters.

        Returns a list of (interface_name, tx_str, rx_str, is_healthy)
        tuples. An interface is unhealthy if it has RX but zero TX
        (link establishment / SYN/ACK failing).

        Returns empty list if rnstatus is unavailable or fails.
        """
        rnstatus_path = shutil.which('rnstatus')
        if not rnstatus_path:
            user_home = get_real_user_home()
            candidate = user_home / '.local' / 'bin' / 'rnstatus'
            if candidate.exists():
                rnstatus_path = str(candidate)
        if not rnstatus_path:
            return []

        try:
            result = subprocess.run(
                [rnstatus_path],
                capture_output=True, text=True, timeout=15
            )
            combined = (result.stdout or '') + (result.stderr or '')
            if ('no shared' in combined.lower()
                    or 'could not' in combined.lower()):
                return []
        except (subprocess.SubprocessError, FileNotFoundError,
                OSError):
            return []

        interfaces = []
        current_iface = None
        tx_cache = {}

        for line in combined.splitlines():
            # Interface header: InterfaceType[DisplayName]
            iface_match = re.match(r'\s*(\w+)\[(.+?)\]', line)
            if iface_match:
                current_iface = (
                    f"{iface_match.group(1)}"
                    f"[{iface_match.group(2)}]"
                )
                continue

            # TX line: ↑NNN B  NNN bps
            tx_match = re.search(
                r'↑\s*([\d,.]+)\s*(\w+)', line
            )
            if tx_match and current_iface:
                tx_cache[current_iface] = (
                    tx_match.group(1).replace(',', ''),
                    tx_match.group(2),
                )

            # RX line: ↓NNN B  NNN bps
            rx_match = re.search(
                r'↓\s*([\d,.]+)\s*(\w+)', line
            )
            if rx_match and current_iface:
                rx_val = rx_match.group(1).replace(',', '')
                rx_unit = rx_match.group(2)
                tx_info = tx_cache.get(
                    current_iface, ('0', 'B')
                )
                tx_str = f"{tx_info[0]} {tx_info[1]}"
                rx_str = f"{rx_val} {rx_unit}"

                try:
                    tx_bytes = float(tx_info[0])
                    rx_bytes = float(rx_val)
                except ValueError:
                    tx_bytes = rx_bytes = 0

                # Healthy if TX > 0, or both are 0 (just started)
                healthy = not (rx_bytes > 0 and tx_bytes == 0)
                interfaces.append(
                    (current_iface, tx_str, rx_str, healthy)
                )
                current_iface = None

        return interfaces

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
                    start_service('rnsd')
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
