"""RNS Repair Wizard — repair shared instance, validate service file.

Extracted from rns_diagnostics.py for file size compliance (CLAUDE.md #6).
Functions take handler or ctx for TUI interaction.
"""

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

from utils.paths import get_real_user_home, ReticulumPaths
from utils.service_check import (
    check_rns_shared_instance, get_rns_shared_instance_info,
    get_udp_port_owner, start_service, stop_service,
    _sudo_write, daemon_reload, enable_service,
)
from utils.config_drift import detect_rnsd_config_drift
from utils.rnsd_restart_order import (
    ClientHold, hold_rns_clients, instance_name, listener_owners,
    ordered_restart_rnsd, release_rns_clients, terminate_pid,
)
from ._rns_interface_mgr import find_blocking_interfaces, disable_interfaces_in_config

logger = logging.getLogger(__name__)


def restart_rnsd() -> bool:
    """Restart rnsd in the #69 repair order (clients down → rnsd → rnsd
    OWNS @rns → clients up). Use :func:`restart_rnsd_reported` to show why.

    Returns True only if rnsd started, owns the shared instance, and every
    client it stopped came back.
    """
    return restart_rnsd_reported()[0]


def restart_rnsd_reported():
    """:func:`restart_rnsd` plus a one-paragraph summary for a dialog."""
    started, release, _hold = ordered_restart_rnsd()
    if not started:
        return False, "rnsd did not start. " + release.summary()
    return release.ok, release.summary()


def _report_held(hold: ClientHold) -> None:
    """Every failure exit of the repair names the clients it left stopped —
    starting them without an rnsd-owned listener is what makes the squat."""
    if hold.stopped:
        print("\n  RNS clients LEFT STOPPED (rnsd does not own the shared")
        print("  instance; starting them now would let one host it):")
        for label in hold.names():
            print(f"    {label}")
        print("  Start them from Service Control once rnsd is healthy.")


def _release_and_report(hold: ClientHold, name: str) -> bool:
    """Start the held clients only once rnsd owns ``@rns/<name>``."""
    release = release_rns_clients(hold, name)
    print(f"  {release.summary()}")
    return release.ok


def _offer_evict_squatters(ctx, name: str) -> None:
    """rnsd is stopped and its clients are held: ANY process still holding
    ``@rns/<name>`` is a squatter rnsd would lose the bind to. Name it by
    PID and offer to stop THAT pid — never a pattern kill (``pkill -f
    nomadnet`` also hit healthy clients and anything with the word in its
    arguments)."""
    owners = listener_owners(name)
    if owners is None:
        print("  Shared-instance owner: UNKNOWN (ss unavailable)")
        return
    if not owners:
        print(f"  @rns/{name}: no holder while rnsd is stopped (OK)")
        return
    for pid, cmdline in owners:
        print(f"\n  WARNING: @rns/{name} is held by PID {pid} while rnsd is stopped:")
        print(f"    {cmdline[:100]}")
        if ctx.dialog.yesno(
            "Stop shared-instance squatter?",
            f"PID {pid} holds the RNS shared instance @rns/{name}\n"
            f"while rnsd is stopped:\n\n  {cmdline[:60]}\n\n"
            f"rnsd cannot become the shared instance while it does.\n"
            f"Stop PID {pid}? (it can rejoin as a client afterwards)",
        ):
            if terminate_pid(pid):
                print(f"  PID {pid} stopped")
            else:
                print(f"  PID {pid} did NOT exit — rnsd may fail to bind")
        else:
            print("  Proceeding with the squatter in place (rnsd may fail)...")


def validate_rnsd_service_file() -> bool:
    """Validate and fix the rnsd systemd service file.

    Detects and fixes:
    - StartLimitIntervalSec in [Service] instead of [Unit]
    - ExecStart pointing to system rnsd instead of venv rnsd
      (venv has all dependencies like meshtastic)
    - Missing After=meshtasticd.service when MeshtasticInterface is
      configured (rnsd crashes if meshtasticd isn't ready yet)

    Returns True if the service file was fixed (daemon-reload needed).
    """
    service_path = Path('/etc/systemd/system/rnsd.service')
    if not service_path.exists():
        return False

    try:
        content = service_path.read_text()
    except (OSError, PermissionError):
        return False

    # Check for StartLimitIntervalSec in [Service] section (should be in [Unit])
    misplaced_directives = False
    current_section = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            current_section = stripped
        elif current_section == '[Service]' and (
            'StartLimitIntervalSec' in stripped
            or 'StartLimitBurst' in stripped
        ):
            misplaced_directives = True
            break

    # Check ExecStart — two problems to detect:
    # 1. ExecStart points to a binary that doesn't exist on disk (critical)
    # 2. ExecStart uses system rnsd when venv rnsd is available (venv has deps)
    wrong_rnsd_path = False
    current_rnsd_binary = None
    exec_match = re.search(r'ExecStart\s*=\s*(.+)', content)
    if exec_match:
        current_rnsd = exec_match.group(1).strip()
        current_rnsd_binary = current_rnsd.split()[0]

    venv_rnsd = Path('/opt/meshanchor/venv/bin/rnsd')

    if current_rnsd_binary:
        if not Path(current_rnsd_binary).exists():
            wrong_rnsd_path = True
        elif venv_rnsd.exists() and current_rnsd_binary != str(venv_rnsd):
            wrong_rnsd_path = True

    # Check for missing meshtasticd ordering dependency
    missing_ordering = False
    if 'meshtasticd.service' not in content:
        try:
            rns_config = ReticulumPaths.get_config_file()
            if rns_config.exists():
                rns_content = rns_config.read_text()
                if re.search(r'^\s*\[\[.*Meshtastic', rns_content, re.MULTILINE):
                    missing_ordering = True
        except Exception as e:
            print(f"  Warning: Could not check RNS config: {e}")

    if not misplaced_directives and not wrong_rnsd_path and not missing_ordering:
        return False

    # Report what we're fixing
    if misplaced_directives:
        print("  Found: StartLimitIntervalSec in [Service] (should be [Unit])")
    if wrong_rnsd_path and current_rnsd_binary:
        if not Path(current_rnsd_binary).exists():
            print(f"  Found: ExecStart binary missing: {current_rnsd_binary}")
        elif venv_rnsd.exists():
            print(f"  Found: ExecStart uses {current_rnsd_binary}")
            print(f"         Should use venv: {venv_rnsd}")
    if missing_ordering:
        print("  Found: Missing After=meshtasticd.service")
        print("         rnsd can crash if meshtasticd isn't ready")
    print("  Regenerating rnsd.service...")

    # Prefer venv rnsd — it has all dependencies
    rnsd_path = str(venv_rnsd) if venv_rnsd.exists() else (
        shutil.which('rnsd') or '/usr/local/bin/rnsd'
    )

    if not Path(rnsd_path).exists():
        print(f"  ERROR: No rnsd binary found on this system.")
        print(f"  Checked: /opt/meshanchor/venv/bin/rnsd, PATH, /usr/local/bin/rnsd")
        print(f"  Install RNS: pip install rns")
        return False

    service_content = f'''[Unit]
Description=Reticulum Network Stack Daemon
After=network-online.target meshtasticd.service
Wants=network-online.target

# Stop crash-looping after 5 failures in 60 seconds
# (e.g., NomadNet holding port 37428)
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={rnsd_path} --service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''
    write_ok, write_msg = _sudo_write(str(service_path), service_content)
    if write_ok:
        print("  Fixed: rnsd.service regenerated")
        ok, msg = daemon_reload()
        if ok:
            print("  Reloaded: systemd daemon-reload complete")
        else:
            print(f"  Warning: daemon-reload failed: {msg}")
        ok, msg = enable_service('rnsd')
        if ok:
            print("  Enabled: rnsd will start on boot")
        else:
            print(f"  Warning: could not enable rnsd: {msg}")
        return True
    else:
        print(f"  Warning: Could not write service file: {write_msg}")
        return False


def _clear_stale_auth_files() -> int:
    """Remove stale shared_instance_* auth files (rnsd must be stopped)."""
    # Clear stale shared_instance_* files
    print("  Clearing stale shared instance authentication files...")
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
                    print(f"    Removed: {auth_file}")
                except (OSError, PermissionError) as e:
                    print(f"    Warning: Could not remove {auth_file}: {e}")
    if files_cleared == 0:
        print("    No stale auth files found")
    return files_cleared


def repair_rns_shared_instance(handler) -> bool:
    """Repair RNS shared instance — explicit user action only.

    This is a repair wizard method, NOT an error handler auto-fix.
    Must only be called from explicit user actions (RNS Diagnostics,
    Repair menu, etc.) — never from error handlers in _run_rns_tool().

    Args:
        handler: RNSDiagnosticsHandler instance (for ctx, dependencies, conflict checks)

    Steps:
    1. Ensures /etc/reticulum/ directories exist with correct permissions,
       deploys template ONLY if no config exists anywhere (never overwrites)
    2. Validates rnsd.service file (fixes ExecStart path & misplaced directives)
    3. Checks rnsd Python dependencies for enabled interface plugins
    4. Clears stale auth tokens, checks blocking interfaces, restarts rnsd
    5. Verifies shared instance is now available (UDP port 37428)

    Returns True if fix was successful.
    """
    ctx = handler.ctx

    print("\n" + "=" * 50)
    print("RNS REPAIR: Shared Instance")
    print("=" * 50)

    # Step 1: Fix directories and deploy config ONLY if none exists
    target_dir = Path('/etc/reticulum')
    target = target_dir / 'config'

    print(f"\n[1/5] Checking RNS config and directories...")

    try:
        if ReticulumPaths.ensure_system_dirs():
            print(f"  Ensured: {ReticulumPaths.ETC_STORAGE}")
            print(f"  Ensured: {ReticulumPaths.ETC_INTERFACES}")
        else:
            print("  ERROR: Could not create /etc/reticulum/ directories")
            print("  (Run MeshAnchor with sudo)")
            return False

        existing_config = ReticulumPaths.get_config_file()
        if existing_config.exists():
            print(f"  Existing config preserved: {existing_config}")
        else:
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
            if template.exists():
                shutil.copy2(str(template), str(target))
                target.chmod(0o644)
                print(f"  No config found — deployed template to: {target}")
            else:
                print("  WARNING: No config found and template missing")
                print("  Run: rnsd --exampleconfig > /etc/reticulum/config")
    except (OSError, PermissionError) as e:
        print(f"  ERROR: {e}")
        print("  (Run MeshAnchor with sudo)")
        return False

    # Step 2: Validate rnsd.service file
    print(f"\n[2/5] Validating rnsd systemd service file...")
    service_path = Path('/etc/systemd/system/rnsd.service')
    if service_path.exists():
        service_fixed = validate_rnsd_service_file()
        if not service_fixed:
            print("  Service file: OK")
    else:
        print("  Service file: not found (rnsd may not be installed as service)")

    # Step 3: Check rnsd Python dependencies
    print(f"\n[3/5] Checking rnsd Python dependencies...")
    handler._ensure_rnsd_dependencies()

    # Step 4: clients down, stop rnsd, clear stale auth tokens, start rnsd.
    # #69 repair order: a client left running while rnsd is down can host
    # @rns/<instance> itself — so clients stop FIRST and start only after
    # rnsd is proven to own the listener (MeshForge 71705049).
    print(f"\n[4/5] Restarting rnsd service (RNS clients first)...")
    name = instance_name()
    hold = hold_rns_clients()
    for label in hold.names():
        print(f"  Stopped RNS client: {label}")
    for unit, _user, msg in hold.stop_failed:
        print(f"  Warning: could not stop RNS client {unit}: {msg}")
    for unit, user in hold.unobservable:
        # Both scopes land here since S2 (re-review R4): name the manager that
        # did not answer, not "user manager" for a SYSTEM unit's timeout.
        scope = "user manager" if user else "system manager"
        print(f"  Warning: state of RNS client {unit} UNKNOWN — {scope} did not "
              f"answer (timeout or bus unreachable); not stopped")

    print("  Stopping rnsd...")
    success, msg = stop_service('rnsd')
    if not success:
        print(f"  Warning stopping rnsd: {msg}")
    time.sleep(1)

    _clear_stale_auth_files()

    # Pre-flight 4a: Validate share_instance = Yes
    _preflight_share_instance(ctx)

    # Pre-flight 4b: Config drift
    try:
        drift = detect_rnsd_config_drift()
        if drift.drifted:
            print(f"\n  WARNING: Config drift detected!")
            print(f"    Gateway reads: {drift.gateway_config_dir}")
            print(f"    rnsd reads:    {drift.rnsd_config_dir}")
            print(f"    Fix: {drift.fix_hint}")
            print("    The checks above may have validated the wrong config.")
    except Exception as e:
        logger.debug("Pre-flight config drift check failed: %s", e)

    # Pre-flight 4c: anything still holding the shared-instance socket
    try:
        _offer_evict_squatters(ctx, name)
    except Exception as e:
        logger.debug("Pre-flight squatter check failed: %s", e)
        print(f"  Shared-instance owner check skipped: {e}")

    # Pre-flight: check blocking interfaces
    user_declined_disable = False
    blocking = find_blocking_interfaces()
    if blocking:
        print("\n  WARNING: Enabled interfaces have missing dependencies:")
        for iface_name, reason, fix in blocking:
            print(f"    [{iface_name}] {reason}")
            print(f"    Fix: {fix}")
        print()
        print("  rnsd will hang if these interfaces can't connect.")

        iface_names = [b[0] for b in blocking]
        names_str = ", ".join(iface_names)
        if ctx.dialog.yesno(
            "Disable Blocking Interfaces?",
            f"These interfaces will prevent rnsd from starting:\n"
            f"  {names_str}\n\n"
            f"Temporarily disable them in the RNS config?\n"
            f"(You can re-enable them later from the RNS menu)\n\n"
            f"If you choose No, rnsd may hang on startup.",
        ):
            disabled = disable_interfaces_in_config(iface_names)
            if disabled:
                print(f"  Disabled {len(disabled)} blocking interface(s):")
                for name in disabled:
                    print(f"    [{name}] set enabled = no")
            else:
                print("  Could not disable interfaces — rnsd may hang")
        else:
            user_declined_disable = True
            print("  Proceeding without disabling (rnsd may hang)...\n")

    # Clear systemd start limit
    try:
        subprocess.run(
            ['systemctl', 'reset-failed', 'rnsd'],
            capture_output=True, timeout=5
        )
    except (subprocess.SubprocessError, OSError):
        pass

    # Start rnsd
    print("  Starting rnsd...")
    try:
        success, msg = start_service('rnsd')
        if success:
            print("  rnsd started successfully")
        else:
            print(f"  Warning: {msg}")
    except Exception as e:
        print(f"  Warning: {e}")

    # Fix permissions on files rnsd just created (auth tokens, caches).
    # This must run AFTER rnsd start so newly-created shared_instance_*
    # files and identity are covered.  Without this, NomadNet (running as
    # a non-root user) cannot read the auth tokens → auth mismatch.
    try:
        time.sleep(1)  # brief delay for rnsd to create files
        ReticulumPaths._fix_storage_file_permissions()
        print("  Fixed file permissions for shared access")
    except Exception as e:
        logger.debug("Post-restart permission fix: %s", e)

    # Step 5: Verify shared instance
    print(f"\n[5/5] Verifying shared instance...")
    print("  Waiting for rnsd shared instance...")

    instance_ok = False
    rnsd_crashed = False
    for i in range(30):
        instance_ok = check_rns_shared_instance()
        if instance_ok:
            break
        try:
            # Intentional raw is-active (not check_service): this loop must
            # distinguish 'activating' (mid-start — keep waiting) from
            # 'failed'/'inactive' (crashed — stop). check_service collapses
            # 'activating' into NOT_RUNNING, which would falsely declare a crash
            # mid-start. Allowlisted in TestServiceCheckContract.
            r = subprocess.run(
                ['systemctl', 'is-active', 'rnsd'],
                capture_output=True, text=True, timeout=5
            )
            state = r.stdout.strip()
            if state in ('failed', 'inactive'):
                rnsd_crashed = True
                break
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(1)

    if instance_ok:
        info = get_rns_shared_instance_info()
        print(f"  Shared instance answers: {info['detail']}")
        # "Answers" is not "rnsd owns it" — a squatter answers too.
        if not _release_and_report(hold, name):
            print("\n  Repair INCOMPLETE — see above.")
            return False
        print("\n" + "=" * 50)
        print("RNS shared instance is now available!")
        print("=" * 50 + "\n")
        return True

    if rnsd_crashed:
        ok = _handle_rnsd_crash(ctx, hold, name)
    else:
        # Shared instance not available after 30s but rnsd didn't crash
        ok = _diagnose_timeout(handler, user_declined_disable, hold, name)
    if not ok:
        _report_held(hold)
    return ok


def _preflight_share_instance(ctx):
    """Validate share_instance = Yes before restart."""
    try:
        from commands.rns import _parse_share_instance
        config_path = ReticulumPaths.get_config_file()
        if config_path.exists():
            config_content = config_path.read_text()
            share_ok = _parse_share_instance(config_content)
            if share_ok:
                print("  share_instance: Yes (OK)")
            else:
                print("  share_instance: DISABLED")
                print("  Without share_instance = Yes, rnsd won't accept")
                print("  connections from gateway, rnstatus, or other tools.")
                if ctx.dialog.yesno(
                    "Fix share_instance",
                    "share_instance is disabled in the RNS config.\n\n"
                    "Without it, rnsd won't expose the shared instance\n"
                    "and no client apps (gateway, rnstatus) can connect.\n\n"
                    f"Config: {config_path}\n\n"
                    "Set share_instance = Yes?"
                ):
                    if re.search(r'^\s*share_instance\s*=',
                                 config_content, re.MULTILINE):
                        fixed = re.sub(
                            r'^(\s*share_instance\s*=\s*).*$',
                            r'\1Yes',
                            config_content,
                            count=1,
                            flags=re.MULTILINE
                        )
                    elif '[reticulum]' in config_content.lower():
                        fixed = config_content.replace(
                            '[reticulum]',
                            '[reticulum]\n  share_instance = Yes',
                            1
                        )
                    else:
                        fixed = ('[reticulum]\n  share_instance = Yes\n\n'
                                 + config_content)
                    ok, msg = _sudo_write(str(config_path), fixed)
                    if ok:
                        verify = config_path.read_text()
                        if _parse_share_instance(verify):
                            print("  Fixed: share_instance = Yes")
                        else:
                            print("  WARNING: Config write did not take effect")
                    else:
                        print(f"  Could not write config: {msg}")
        else:
            print(f"  Config not found at {config_path}")
    except Exception as e:
        logger.debug("Pre-flight share_instance check failed: %s", e)


def _handle_rnsd_crash(ctx, hold: ClientHold = None, name: str = None) -> bool:
    """Handle rnsd crash during repair — diagnose and offer fixes."""
    print("  FAILED: rnsd crashed on startup")
    print()

    venv_rnsd = Path('/opt/meshanchor/venv/bin/rnsd')
    rnsd_path = str(venv_rnsd) if venv_rnsd.exists() else (
        shutil.which('rnsd') or '/usr/local/bin/rnsd'
    )
    print("  Running rnsd directly to capture error...")
    output = ""
    try:
        r = subprocess.run(
            [rnsd_path],
            capture_output=True, text=True, timeout=10
        )
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        if output:
            lower_output = output.lower()
            if 'address already in use' in lower_output:
                print("  Cause: Port conflict (another process holds the port)")
            elif 'permission' in lower_output and 'denied' in lower_output:
                print("  Cause: Permission denied")
            elif 'connection refused' in lower_output:
                print("  Cause: meshtasticd not running (Connection refused)")
                print("  Fix: sudo systemctl start meshtasticd")
            else:
                for line in output.splitlines()[-5:]:
                    print(f"  {line}")
            print(f"  Full log: sudo journalctl -u rnsd -n 20")
        else:
            print("  (no output captured)")
    except subprocess.TimeoutExpired:
        print("  rnsd hung (no crash within 10s — likely a blocking interface)")
    except (OSError, FileNotFoundError) as e:
        print(f"  Could not run rnsd: {e}")

    # Detect missing meshtastic module and offer install
    if 'meshtastic' in output.lower():
        print()
        print("  Cause: Meshtastic_Interface.py plugin needs the meshtastic module")
        venv_pip = Path('/opt/meshanchor/venv/bin/pip')
        if venv_pip.exists() and ctx.dialog.yesno(
            "Install meshtastic Module",
            "The Meshtastic_Interface.py plugin requires the\n"
            "meshtastic Python module, which is not installed.\n\n"
            "Install it now?\n\n"
            "  pip install meshtastic (into MeshAnchor venv)",
        ):
            print("  Installing meshtastic module...")
            # Route through the hardened helper into the venv's interpreter
            # (ensure_pip + return-code check + conflict-retry are all internal).
            from utils.pip_install import pip_install
            venv_python = venv_pip.parent / 'python'
            pip_r = pip_install(['meshtastic'], python=str(venv_python), timeout=120)
            if pip_r.ok:
                print("  meshtastic installed. Restarting rnsd...")
                subprocess.run(
                    ['systemctl', 'reset-failed', 'rnsd'],
                    capture_output=True, timeout=5
                )
                start_service('rnsd')
                time.sleep(3)
                if check_rns_shared_instance() and _release_and_report(
                        hold or ClientHold(), name or instance_name()):
                    print("  SUCCESS: RNS shared instance is available")
                    print("\n" + "=" * 50)
                    print("RNS shared instance is now available!")
                    print("=" * 50 + "\n")
                    return True
                print("  rnsd restarted — check with RNS > Diagnostics")
            else:
                print(f"  pip install failed: {pip_r.detail[:200]}")

    return False


def _diagnose_timeout(handler, user_declined_disable: bool,
                      hold: ClientHold = None, name: str = None) -> bool:
    """Diagnose why shared instance isn't available after 30s."""
    ctx = handler.ctx
    print("  WARNING: Shared instance not available after 30s")
    print()
    print("  --- Diagnosing root cause ---")

    info = get_rns_shared_instance_info()
    print(f"  Shared instance: {info['detail']}")

    try:
        owner = get_udp_port_owner(37428)
        if owner:
            print(f"  Port 37428 owner: {owner[0]} (PID {owner[1]})")
            if owner[0] in ('nomadnet', 'python', 'python3'):
                print("  Likely cause: NomadNet is holding the port")
    except Exception:
        pass

    try:
        from commands.rns import _parse_share_instance
        config_path = ReticulumPaths.get_config_file()
        if config_path.exists():
            config_content = config_path.read_text()
            if not _parse_share_instance(config_content):
                print("  Cause: share_instance not enabled in config")
                print("  (pre-flight fix may not have applied due to config drift)")
    except Exception:
        pass

    try:
        drift = detect_rnsd_config_drift()
        if drift.drifted:
            print(f"  Config drift: gateway reads {drift.gateway_config_dir}")
            print(f"                rnsd reads    {drift.rnsd_config_dir}")
            print(f"  Fix: {drift.fix_hint}")
    except Exception:
        pass

    try:
        owners = listener_owners(name)
        if owners is None:
            print("  Shared-instance owner: UNKNOWN (ss unavailable)")
        for pid, cmdline in owners or []:
            print(f"  @rns/{name or instance_name()} held by PID {pid}: {cmdline[:80]}")
    except Exception as e:
        logger.debug("diagnosis step skipped (listener owner): %s", e)

    # Blocking interfaces — offer second chance
    try:
        post_blocking = find_blocking_interfaces()
        if post_blocking:
            print("\n  Blocking interfaces detected:")
            for iface_name, reason, fix in post_blocking:
                print(f"    [{iface_name}] {reason}")
            if user_declined_disable:
                print("\n  These are likely why rnsd is stuck.")
                iface_names = [b[0] for b in post_blocking]
                names_str = ", ".join(iface_names)
                if ctx.dialog.yesno(
                    "Disable Blocking Interfaces?",
                    f"Blocking interfaces are preventing rnsd\n"
                    f"from initializing:\n"
                    f"  {names_str}\n\n"
                    f"Disable them and restart rnsd?"
                ):
                    disabled = disable_interfaces_in_config(iface_names)
                    if disabled:
                        print(f"  Disabled {len(disabled)} interface(s)")
                        stop_service('rnsd')
                        time.sleep(1)
                        start_service('rnsd')
                        print("  Waiting for shared instance...")
                        answered = False
                        for _ in range(15):
                            time.sleep(1)
                            if check_rns_shared_instance():
                                answered = True
                                break
                        if answered and _release_and_report(
                                hold or ClientHold(), name or instance_name()):
                            si = get_rns_shared_instance_info()
                            print(f"  SUCCESS: {si['detail']}")
                            print("\n" + "=" * 50)
                            print("RNS shared instance is now available!")
                            print("=" * 50 + "\n")
                            return True
                        print("  Still not available after disabling interfaces")
    except Exception:
        pass

    # Journal output
    print()
    try:
        r = subprocess.run(
            ['journalctl', '-u', 'rnsd', '-n', '15', '--no-pager',
             '-q', '--no-hostname'],
            capture_output=True, text=True, timeout=10
        )
        if r.stdout and r.stdout.strip():
            print("  Recent rnsd log:")
            for line in r.stdout.strip().splitlines()[-10:]:
                print(f"    {line.strip()[:100]}")
        else:
            print("  No journal entries for rnsd")
    except (subprocess.SubprocessError, OSError):
        print("  Check logs: sudo journalctl -u rnsd -n 20")

    print("\n  Run RNS > Diagnostics for a full health check.")
    return False


def fix_rns_storage_permissions(ctx) -> bool:
    """Fix /etc/reticulum/storage permissions for non-root users.

    When rnsd runs as root, it creates /etc/reticulum/storage owned by root.
    NomadNet (running as user) needs write access. This sets 0o777 on the
    storage directory and fixes file permissions within.

    Moved from _nomadnet_rns_checks.py Tier 1 chmod logic.

    Args:
        ctx: TUIContext with dialog backend.

    Returns:
        True if permissions fixed or already OK, False on failure.
    """
    import os
    import stat

    etc_rns = Path('/etc/reticulum')
    if not etc_rns.exists():
        return True

    storage_dir = etc_rns / 'storage'
    sudo_user = os.environ.get('SUDO_USER')

    # Check if storage is writable by the real user
    can_write = False
    try:
        if storage_dir.exists():
            if sudo_user and sudo_user != 'root':
                mode = storage_dir.stat().st_mode
                can_write = bool(mode & stat.S_IWOTH)
            else:
                test_file = storage_dir / '.write_test'
                try:
                    test_file.touch()
                    test_file.unlink()
                    can_write = True
                except (OSError, PermissionError):
                    pass
        else:
            try:
                storage_dir.mkdir(parents=True, exist_ok=True)
                can_write = True
            except (OSError, PermissionError):
                pass
    except (OSError, ValueError) as e:
        logger.debug("RNS storage dir check failed: %s", e)

    if can_write:
        return True

    # Fix permissions
    target_user = sudo_user if sudo_user and sudo_user != 'root' else 'current user'
    logger.info(
        "/etc/reticulum/storage not writable by %s, fixing permissions to 0o777",
        target_user,
    )
    try:
        old_umask = os.umask(0)
        try:
            storage_dir.chmod(0o777)
            ReticulumPaths._fix_storage_file_permissions()
        finally:
            os.umask(old_umask)
        ctx.dialog.msgbox(
            "Storage Permissions Fixed",
            "/etc/reticulum/storage/ permissions have been fixed.\n\n"
            "NomadNet will use the system config (same as rnsd).",
        )
        return True
    except (OSError, PermissionError) as e:
        ctx.dialog.msgbox(
            "Permission Fix Failed",
            f"Could not fix /etc/reticulum/storage permissions:\n"
            f"  {e}\n\n"
            f"Try manually:\n"
            f"  sudo chmod 777 /etc/reticulum/storage"
        )
        return False
