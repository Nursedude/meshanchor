"""
RNS Menu Mixin - Reticulum Network Stack menu handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
Sniffer methods further extracted to rns_sniffer_mixin.py.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from rns_sniffer_mixin import RNSSnifferMixin

# Import centralized service checking
try:
    from utils.service_check import check_process_running
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False

# Import centralized path utility - SINGLE SOURCE OF TRUTH for all paths
# See: utils/paths.py (ReticulumPaths, get_real_user_home)
# NO FALLBACK: stale fallback copies caused config divergence bugs (Issue #25+)
from utils.paths import get_real_user_home, ReticulumPaths
from backend import clear_screen


class RNSMenuMixin(RNSSnifferMixin):
    """Mixin providing RNS/Reticulum menu functionality.

    Inherits sniffer methods from RNSSnifferMixin.
    """

    def _rns_menu(self):
        """Reticulum Network Stack tools."""
        while True:
            choices = [
                ("status", "RNS Status (rnstatus)"),
                ("paths", "RNS Path Table (rnpath)"),
                ("sniffer", "RNS Traffic Sniffer (Wireshark-grade)"),
                ("topology", "Network Topology (graph view)"),
                ("quality", "Link Quality Analysis"),
                ("probe", "Probe Destination (rnprobe)"),
                ("identity", "Identity Info (rnid)"),
                ("nodes", "Known Destinations"),
                ("positions", "Set Node Positions (for map)"),
                ("diag", "RNS Diagnostics"),
                ("drift", "Config Drift Check"),
                ("bridge", "Gateway Bridge (start/stop)"),
                ("nomadnet", "NomadNet Client"),
                ("ifaces", "Manage Interfaces"),
                ("config", "View Reticulum Config"),
                ("edit", "Edit Reticulum Config"),
                ("check", "Check RNS Setup"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNS / Reticulum",
                "Reticulum Network Stack tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "sniffer": ("RNS Traffic Sniffer", self._rns_traffic_sniffer),
                "topology": ("Network Topology", self._topology_menu),
                "quality": ("Link Quality Analysis", self._link_quality_menu),
                "probe": ("Probe Destination", self._rns_probe_destination),
                "identity": ("Identity Info", self._rns_identity_info),
                "nodes": ("Known Destinations", self._rns_known_destinations),
                "positions": ("Set Node Positions", self._rns_set_node_positions),
                "diag": ("RNS Diagnostics", self._rns_diagnostics),
                "drift": ("Config Drift Check", self._rns_config_drift_check),
                "bridge": ("Gateway Bridge", self._run_bridge),
                "nomadnet": ("NomadNet Client", self._nomadnet_menu),
                "ifaces": ("Manage Interfaces", self._rns_interfaces_menu),
                "config": ("View RNS Config", self._view_rns_config),
                "edit": ("Edit RNS Config", self._edit_rns_config),
                "check": ("Check RNS Setup", self._check_rns_setup),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)
                continue

            # Inline RNS tool commands
            try:
                if choice == "status":
                    clear_screen()
                    print("=== RNS Status ===\n")
                    self._run_rns_tool(['rnstatus'], 'rnstatus')
                    self._wait_for_enter()
                elif choice == "paths":
                    clear_screen()
                    print("=== RNS Path Table ===\n")
                    self._run_rns_tool(['rnpath', '-t'], 'rnpath')
                    self._wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.dialog.msgbox(
                    "RNS Error",
                    f"Operation failed:\n{type(e).__name__}: {e}\n\n"
                    f"Check that rnsd is running:\n"
                    f"  sudo systemctl status rnsd"
                )

    def _rns_probe_destination(self):
        """Probe an RNS destination to test reachability."""
        clear_screen()
        print("=== Probe RNS Destination ===\n")
        print("Probe tests reachability of a destination on the RNS network.")
        print("Enter the full destination hash (32 hex chars), or a partial hash.\n")

        try:
            dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not dest_hash or dest_hash.lower() == 'q':
            return

        # Validate hex format to prevent flag injection
        if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
            print("Error: Hash must contain only hex characters (0-9, a-f).")
            self._wait_for_enter()
            return

        print(f"\nProbing {dest_hash}...\n")
        self._run_rns_tool(['rnprobe', dest_hash], 'rnprobe')
        self._wait_for_enter()

    def _rns_identity_info(self):
        """Show RNS identity information."""
        clear_screen()
        print("=== RNS Identity Info ===\n")

        while True:
            # Check identity status for menu hints
            config_dir = ReticulumPaths.get_config_dir()
            rnsd_exists = (config_dir / 'identity').exists()
            try:
                from commands.rns import get_identity_path
                gw_exists = get_identity_path().exists()
            except ImportError:
                gw_exists = False

            choices = [
                ("show", "Show local identity"),
                ("create", "Create identities" + (
                    "" if not rnsd_exists or not gw_exists else " (all exist)")),
                ("path", "Show identity file paths"),
                ("recall", "Recall identity by hash"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "RNS Identity",
                "Identity management:",
                choices
            )

            if choice is None or choice == "back":
                break

            try:
                if choice == "create":
                    self._create_rns_identities()

                elif choice == "show":
                    clear_screen()
                    print("=== Local RNS Identity ===\n")

                    # Find the rnsd identity file
                    # RNS stores it at <configdir>/identity
                    rnsd_identity = config_dir / 'identity'
                    if rnsd_identity.exists():
                        print(f"rnsd identity: {rnsd_identity}")
                        self._run_rns_tool(
                            ['rnid', '-i', str(rnsd_identity), '-p'],
                            'rnid'
                        )
                    else:
                        print(f"rnsd identity: {rnsd_identity}")
                        print("  Not found — use 'Create identities' to generate.\n")

                    try:
                        gw_id = get_identity_path()
                        print(f"\nMeshForge gateway identity: {gw_id}")
                        if gw_id.exists():
                            self._run_rns_tool(
                                ['rnid', '-i', str(gw_id), '-p'],
                                'rnid'
                            )
                        else:
                            print("  Not created — use 'Create identities' to generate.")
                    except ImportError:
                        pass
                    self._wait_for_enter()

                elif choice == "path":
                    clear_screen()
                    print("=== RNS Identity Paths ===\n")
                    config_dir = ReticulumPaths.get_config_dir()
                    identity_path = config_dir / 'identity'
                    print(f"RNS config dir:    {config_dir}")
                    print(f"RNS identity file: {identity_path}")
                    if identity_path.exists():
                        stat = identity_path.stat()
                        print(f"  Size: {stat.st_size} bytes")
                        from datetime import datetime
                        mtime = datetime.fromtimestamp(stat.st_mtime)
                        print(f"  Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
                    else:
                        print("  Not found (created on first rnsd start)")

                    try:
                        from commands.rns import get_identity_path
                        gw_id = get_identity_path()
                        print(f"\nMeshForge gateway:  {gw_id}")
                        if gw_id.exists():
                            stat = gw_id.stat()
                            print(f"  Size: {stat.st_size} bytes")
                        else:
                            print("  Not created yet")
                    except ImportError:
                        pass
                    self._wait_for_enter()

                elif choice == "recall":
                    clear_screen()
                    print("=== Recall RNS Identity ===\n")
                    print("Look up a known identity by its destination hash.\n")
                    try:
                        dest_hash = input("Destination hash (or 'q' to cancel): ").strip()
                    except (KeyboardInterrupt, EOFError):
                        print()
                        continue
                    if dest_hash and dest_hash.lower() != 'q':
                        if not re.match(r'^[0-9a-fA-F]+$', dest_hash):
                            print("Error: Hash must contain only hex characters (0-9, a-f).")
                        else:
                            self._run_rns_tool(['rnid', '--recall', dest_hash], 'rnid')
                    self._wait_for_enter()
            except KeyboardInterrupt:
                pass
            except Exception as e:
                self.dialog.msgbox(
                    "Identity Error",
                    f"Operation failed:\n{type(e).__name__}: {e}"
                )

    def _create_rns_identities(self):
        """Create RNS and gateway identities from the TUI.

        Calls commands.rns.create_identities() which generates keypairs
        for both the rnsd identity and the MeshForge gateway identity.
        No manual commands needed.
        """
        clear_screen()
        print("=== Create RNS Identities ===\n")

        try:
            from commands.rns import create_identities, get_identity_path
            config_dir = ReticulumPaths.get_config_dir()

            # Show current state
            rns_id = config_dir / 'identity'
            gw_id = get_identity_path()
            print(f"RNS identity:     {rns_id}")
            print(f"  Status: {'EXISTS' if rns_id.exists() else 'MISSING'}")
            print(f"Gateway identity: {gw_id}")
            print(f"  Status: {'EXISTS' if gw_id.exists() else 'MISSING'}\n")

            if rns_id.exists() and gw_id.exists():
                print("Both identities already exist. Nothing to create.")
                self._wait_for_enter()
                return

            result = create_identities()
            if result.success:
                print(f"OK: {result.message}")
                created = result.data.get('created', [])
                if 'rns' in created:
                    print(f"  Created: {result.data['rns_identity']}")
                if 'gateway' in created:
                    print(f"  Created: {result.data['gateway_identity']}")
                if not created:
                    print("  All identities already existed.")
            else:
                print(f"ERROR: {result.message}")
        except ImportError:
            print("ERROR: RNS module not installed.")
            print("  Install: pip install rns")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        self._wait_for_enter()

    def _rns_known_destinations(self):
        """Show known RNS destinations from the running rnsd instance."""
        clear_screen()
        print("=== Known RNS Destinations ===\n")

        try:
            from commands.rns import list_known_destinations
            result = list_known_destinations()

            if result.success:
                nodes = result.data.get('nodes', [])
                count = result.data.get('count', 0)

                if count == 0:
                    print("No known destinations yet.")
                    print("\nNodes appear when they announce or when you request paths.")
                    print("Make sure rnsd is running: sudo systemctl start rnsd")
                else:
                    print(f"Found {count} destination(s):\n")
                    print(f"{'Hash':>10}  {'Hops':>5}  {'Source':<20}  {'Name'}")
                    print("-" * 60)
                    for node in nodes:
                        short = node.get('short_hash', '?')
                        hops = node.get('hops', -1)
                        hops_str = str(hops) if hops >= 0 else '?'
                        source = node.get('source', 'unknown')
                        name = node.get('name', '')
                        print(f"{short:>10}  {hops_str:>5}  {source:<20}  {name}")
            else:
                print(f"Error: {result.message}")
                fix_hint = (result.data or {}).get('fix_hint', '')
                if fix_hint:
                    print(f"Fix: {fix_hint}")
        except ImportError:
            # Fallback: use rnstatus which also shows some destination info
            print("Commands module not available, falling back to rnstatus...\n")
            self._run_rns_tool(['rnstatus', '-a'], 'rnstatus')

        self._wait_for_enter()

    def _rns_set_node_positions(self):
        """Set GPS positions for RNS nodes so they appear on the map.

        NomadNet nodes don't broadcast location, so positions must be set manually.
        Sideband nodes with GPS sharing will be auto-populated.
        """
        while True:
            clear_screen()
            print("=== Set RNS Node Positions ===\n")
            print("NomadNet nodes don't broadcast GPS. Set positions manually")
            print("so your RNS nodes appear on the live network map.\n")

            # Load node tracker and cache
            try:
                from gateway.node_tracker import UnifiedNodeTracker
                tracker = UnifiedNodeTracker()
                rns_nodes = tracker.get_rns_nodes()
            except Exception as e:
                print(f"Error loading node tracker: {e}")
                self._wait_for_enter()
                return

            if not rns_nodes:
                print("No RNS nodes discovered yet.")
                print("\nMake sure rnsd is running and you've exchanged announces")
                print("with other nodes (via NomadNet or Sideband).")
                self._wait_for_enter()
                return

            # Build menu of nodes
            choices = []
            print(f"{'#':<3} {'Name':<20} {'Hash':<12} {'Position'}")
            print("-" * 60)
            for i, node in enumerate(rns_nodes):
                if node.position.is_valid():
                    pos_str = f"({node.position.latitude:.4f}, {node.position.longitude:.4f})"
                else:
                    pos_str = "NOT SET"
                name = node.name[:18] if node.name else node.id[:18]
                hash_short = node.id.replace('rns_', '')[:10]
                print(f"{i+1:<3} {name:<20} {hash_short:<12} {pos_str}")
                choices.append((str(i), f"{name} - {pos_str}"))

            choices.append(("back", "Back to RNS Menu"))
            print()

            choice = self.dialog.menu(
                "Select Node",
                "Choose a node to set its position:",
                choices
            )

            if choice is None or choice == "back":
                break

            try:
                idx = int(choice)
                if 0 <= idx < len(rns_nodes):
                    self._set_single_node_position(rns_nodes[idx])
            except ValueError:
                pass

    def _set_single_node_position(self, node):
        """Set position for a single RNS node."""
        clear_screen()
        print(f"=== Set Position for {node.name} ===\n")
        print(f"Node ID: {node.id}")
        if node.position.is_valid():
            print(f"Current: ({node.position.latitude:.6f}, {node.position.longitude:.6f})")
        else:
            print("Current: NOT SET")
        print()
        print("Enter coordinates in decimal degrees (e.g., 21.3069 for latitude)")
        print("Tip: Get coords from Google Maps by right-clicking a location\n")

        try:
            lat_str = input("Latitude (e.g., 21.3069): ").strip()
            if not lat_str:
                print("Cancelled.")
                self._wait_for_enter()
                return

            lon_str = input("Longitude (e.g., -157.8583): ").strip()
            if not lon_str:
                print("Cancelled.")
                self._wait_for_enter()
                return

            lat = float(lat_str)
            lon = float(lon_str)

            # Validate
            if not (-90 <= lat <= 90):
                print(f"Invalid latitude: {lat} (must be -90 to 90)")
                self._wait_for_enter()
                return
            if not (-180 <= lon <= 180):
                print(f"Invalid longitude: {lon} (must be -180 to 180)")
                self._wait_for_enter()
                return

            # Optional: name
            name_input = input(f"Name [{node.name}]: ").strip()
            new_name = name_input if name_input else node.name

            # Save to cache
            self._save_rns_node_position(node.id, new_name, lat, lon)
            print(f"\nSaved: {new_name} at ({lat:.6f}, {lon:.6f})")
            print("Refresh the map to see the updated position.")

        except ValueError as e:
            print(f"Invalid input: {e}")
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")

        self._wait_for_enter()

    def _save_rns_node_position(self, node_id: str, name: str, lat: float, lon: float):
        """Save an RNS node position to the node cache."""
        import json

        cache_path = get_real_user_home() / '.config' / 'meshforge' / 'node_cache.json'
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing cache
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                data = {'version': 1, 'nodes': []}
        else:
            data = {'version': 1, 'nodes': []}

        if 'nodes' not in data:
            data['nodes'] = []

        # Find and update or add node
        found = False
        for node in data['nodes']:
            if node.get('id') == node_id:
                node['name'] = name
                node['position'] = {'latitude': lat, 'longitude': lon, 'altitude': 0}
                node['network'] = 'rns'
                found = True
                break

        if not found:
            data['nodes'].append({
                'id': node_id,
                'name': name,
                'network': 'rns',
                'position': {'latitude': lat, 'longitude': lon, 'altitude': 0},
                'is_online': True,
            })

        # Save
        with open(cache_path, 'w') as f:
            json.dump(data, f, indent=2)

    def _rns_diagnostics(self):
        """Run comprehensive RNS diagnostics."""
        clear_screen()
        print("=== RNS Diagnostics ===\n")

        try:
            from commands.rns import check_connectivity, get_status
        except ImportError:
            print("RNS commands module not available.")
            print("Run from MeshForge root: sudo python3 src/launcher_tui/main.py")
            self._wait_for_enter()
            return

        # 1. Service status
        print("[1/4] Checking rnsd service...")
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
        print("\n[2/4] Checking configuration...")
        config_exists = status_data.get('config_exists', False)
        print(f"  Config: {'found' if config_exists else 'MISSING'}")
        if config_exists:
            iface_count = status_data.get('interface_count', 0)
            print(f"  Interfaces: {iface_count}")

        # 3. Identity check
        print("\n[3/4] Checking identity...")
        identity_exists = status_data.get('identity_exists', False)
        print(f"  Gateway identity: {'found' if identity_exists else 'not created'}")
        config_dir = ReticulumPaths.get_config_dir()
        rns_identity = config_dir / 'identity'
        print(f"  RNS identity: {'found' if rns_identity.exists() else 'not created'}")

        # 4. Full connectivity check
        print("\n[4/4] Running connectivity check...")
        conn = check_connectivity()
        conn_data = conn.data or {}
        print(f"  RNS importable: {'yes' if conn_data.get('can_import_rns') else 'NO'}")
        if conn_data.get('rns_version'):
            print(f"  RNS version: {conn_data['rns_version']}")
        print(f"  Config valid: {'yes' if conn_data.get('config_valid') else 'NO'}")
        print(f"  Interfaces enabled: {conn_data.get('interfaces_enabled', 0)}")

        # Summary
        issues = conn_data.get('issues', [])
        warnings = conn_data.get('warnings', [])
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
                    from commands.rns import create_identities
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

        try:
            from utils.config_drift import detect_rnsd_config_drift
        except ImportError:
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

    @staticmethod
    def _is_root_owned_rns_config(config_path: Path) -> bool:
        """Check if the RNS config is in a root-only location (/root/)."""
        try:
            return str(config_path.resolve()).startswith('/root/')
        except OSError:
            return str(config_path).startswith('/root/')

    def _migrate_rns_config_to_etc(self, source: Path) -> bool:
        """Migrate RNS config from root-owned location to /etc/reticulum/config.

        Copies the config to /etc/reticulum/config (system-wide, preferred location),
        sets world-readable permissions, and renames the old file to avoid confusion.

        Returns True if migration succeeded.
        """
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.dialog.msgbox(
                "Cannot Migrate",
                f"Config already exists at:\n  {target}\n\n"
                f"Remove it first if you want to migrate from:\n  {source}"
            )
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            target.chmod(0o644)
            # Rename old config so rnsd picks up the /etc/ one
            backup = source.with_suffix('.migrated')
            source.rename(backup)
            return True
        except (OSError, PermissionError) as e:
            self.dialog.msgbox("Error", f"Failed to migrate config:\n{e}")
            return False

    def _deploy_rns_template(self) -> Optional[Path]:
        """Deploy RNS template to /etc/reticulum/config (system-wide).

        Returns the path where the config was deployed, or None on failure.
        """
        template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
        if not template.exists():
            return None

        # Always deploy to /etc/reticulum/ (system-wide, first in search order)
        target = Path('/etc/reticulum/config')
        if target.exists():
            self.dialog.msgbox(
                "Config Exists",
                f"Config already exists at:\n  {target}\n\n"
                f"Use 'Edit Reticulum Config' to modify it."
            )
            return None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(template), str(target))
            target.chmod(0o644)  # World-readable so all users and rnsd can read it
            return target
        except (OSError, PermissionError) as e:
            self.dialog.msgbox("Error", f"Failed to deploy config:\n{e}")
            return None

    def _auto_fix_rns_shared_instance(self) -> bool:
        """Automatically fix RNS shared instance issues.

        Called when 'no shared instance' error is detected. This method:
        1. Ensures /etc/reticulum/ directories exist with correct permissions
        2. Deploys template ONLY if no config exists anywhere (never overwrites)
        3. Clears stale auth tokens and restarts rnsd
        4. Verifies shared instance is now available

        Returns True if fix was successful.
        """
        import time

        print("\n" + "=" * 50)
        print("AUTO-FIX: RNS Shared Instance")
        print("=" * 50)

        # Step 1: Fix directories and deploy config ONLY if none exists
        target_dir = Path('/etc/reticulum')
        target = target_dir / 'config'

        print(f"\n[1/3] Checking RNS config and directories...")

        try:
            # Create /etc/reticulum/ directory structure
            target_dir.mkdir(parents=True, exist_ok=True)

            # Create required subdirectories that rnsd needs to write to
            storage_dir = target_dir / 'storage'
            interfaces_dir = target_dir / 'interfaces'

            # Use 0o777 for storage dirs — rnsd may run as a different user
            # than MeshForge, and NomadNet launches as the real user (not root).
            # Must match ensure_system_dirs() in paths.py.
            old_umask = os.umask(0)
            try:
                storage_dir.mkdir(mode=0o777, exist_ok=True)
                interfaces_dir.mkdir(mode=0o755, exist_ok=True)
            finally:
                os.umask(old_umask)

            # Fix existing permissions (may have been set to 0o755 by older code)
            target_dir.chmod(0o755)
            storage_dir.chmod(0o777)
            interfaces_dir.chmod(0o755)

            print(f"  Ensured: {storage_dir}")
            print(f"  Ensured: {interfaces_dir}")

            # Only deploy template if NO config exists at ANY standard location.
            # Never overwrite an existing config — that destroys user interfaces.
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
            print("  (Run MeshForge with sudo)")
            return False

        # Step 2: Stop rnsd, clear stale auth tokens, start rnsd
        print(f"\n[2/3] Restarting rnsd service...")

        # Stop rnsd first (must stop before clearing auth files)
        print("  Stopping rnsd...")
        try:
            subprocess.run(
                ['systemctl', 'stop', 'rnsd'],
                capture_output=True, text=True, timeout=10
            )
            time.sleep(1)  # Give it time to fully stop
        except Exception as e:
            print(f"  Warning stopping rnsd: {e}")

        # Clear stale shared_instance_* files that cause AuthenticationError
        # These files contain auth tokens that become invalid after config changes
        print("  Clearing stale shared instance authentication files...")
        storage_dirs = [
            Path('/etc/reticulum/storage'),
            Path('/root/.reticulum/storage'),
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

        # Start rnsd with fresh state
        print("  Starting rnsd...")
        try:
            result = subprocess.run(
                ['systemctl', 'start', 'rnsd'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                print("  rnsd started successfully")
            else:
                print(f"  Warning: systemctl returned {result.returncode}")
                if result.stderr:
                    print(f"  {result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            print("  Warning: start timed out")
        except Exception as e:
            print(f"  Warning: {e}")

        # Give rnsd time to start and bind the port
        print("  Waiting for rnsd to initialize...")
        time.sleep(2)

        # Step 3: Verify shared instance is now available
        print(f"\n[3/3] Verifying shared instance...")
        try:
            # Check if rnsd is listening on port 37428
            result = subprocess.run(
                ['ss', '-tlnp'],
                capture_output=True, text=True, timeout=5
            )
            if '37428' in result.stdout:
                print("  SUCCESS: rnsd is now listening on port 37428")
                print("\n" + "=" * 50)
                print("RNS shared instance is now available!")
                print("=" * 50 + "\n")
                return True
            else:
                print("  WARNING: rnsd not yet listening on port 37428")
                print("  Service may need more time to start.")
                print("  Check logs: sudo journalctl -u rnsd -n 20")
                return False
        except Exception as e:
            print(f"  Cannot verify: {e}")
            return False

    def _check_meshtastic_plugin(self) -> bool:
        """Check if Meshtastic_Interface.py plugin is installed.

        The plugin bridges RNS over Meshtastic LoRa and must be in
        the RNS interfaces directory (e.g., ~/.reticulum/interfaces/ or
        /etc/reticulum/interfaces/).

        Returns True if plugin is installed.
        """
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        return plugin_path.exists()

    def _install_meshtastic_interface_plugin(self):
        """Download and install Meshtastic_Interface.py plugin from GitHub.

        Clones the RNS_Over_Meshtastic_Gateway repository and copies the
        Meshtastic_Interface.py file to the RNS interfaces directory.
        """
        interfaces_dir = ReticulumPaths.get_interfaces_dir()
        plugin_path = interfaces_dir / 'Meshtastic_Interface.py'

        if plugin_path.exists():
            self.dialog.msgbox(
                "Already Installed",
                f"Meshtastic_Interface.py is already installed at:\n"
                f"  {plugin_path}\n\n"
                f"Size: {plugin_path.stat().st_size} bytes"
            )
            return

        if not self.dialog.yesno(
            "Install Meshtastic Interface Plugin",
            "The Meshtastic_Interface.py plugin is required for\n"
            "bridging RNS over Meshtastic LoRa mesh networks.\n\n"
            "Source: github.com/landandair/RNS_Over_Meshtastic\n\n"
            f"Install to:\n  {plugin_path}\n\n"
            "Requires: git and internet connection.\n\n"
            "Install now?"
        ):
            return

        # Clone repo to temp dir and copy plugin
        import tempfile
        tmp_dir = tempfile.mkdtemp(prefix='meshforge_rns_plugin_')
        clone_url = "https://github.com/landandair/RNS_Over_Meshtastic.git"

        try:
            # Clone the repository
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', clone_url, tmp_dir],
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode != 0:
                self.dialog.msgbox(
                    "Clone Failed",
                    f"Failed to clone repository:\n{result.stderr}\n\n"
                    f"Manual install:\n"
                    f"  git clone {clone_url}\n"
                    f"  cp RNS_Over_Meshtastic/Interface/Meshtastic_Interface.py \\\n"
                    f"    {interfaces_dir}/"
                )
                return

            # Find the plugin file (in Interface/ subfolder per upstream repo)
            source_file = Path(tmp_dir) / 'Interface' / 'Meshtastic_Interface.py'
            if not source_file.exists():
                # Fallback: check repo root in case structure changes
                source_file = Path(tmp_dir) / 'Meshtastic_Interface.py'
            if not source_file.exists():
                self.dialog.msgbox(
                    "Plugin Not Found",
                    f"Meshtastic_Interface.py not found in repository.\n\n"
                    f"Expected at: Interface/Meshtastic_Interface.py\n"
                    f"Check: {clone_url}"
                )
                return

            # Create interfaces directory and copy plugin
            interfaces_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source_file), str(plugin_path))
            plugin_path.chmod(0o644)

            self.dialog.msgbox(
                "Plugin Installed",
                f"Meshtastic_Interface.py installed to:\n"
                f"  {plugin_path}\n\n"
                f"Restart rnsd to load the new interface:\n"
                f"  sudo systemctl restart rnsd"
            )

        except FileNotFoundError:
            self.dialog.msgbox(
                "Git Not Found",
                "git is required to download the plugin.\n\n"
                "Install git: sudo apt install git\n\n"
                "Or manually download from:\n"
                f"  {clone_url}"
            )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox(
                "Timeout",
                "Download timed out. Check your internet connection."
            )
        except (OSError, PermissionError) as e:
            self.dialog.msgbox(
                "Install Failed",
                f"Failed to install plugin:\n{e}\n\n"
                f"Try running with sudo, or manually copy:\n"
                f"  sudo cp Meshtastic_Interface.py {interfaces_dir}/"
            )
        finally:
            # Clean up temp dir
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _view_rns_config(self):
        """View current Reticulum config."""
        clear_screen()
        print("=== Reticulum Configuration ===\n")

        config_path = ReticulumPaths.get_config_file()

        if config_path.exists():
            # Warn if config is in root-only location
            if self._is_root_owned_rns_config(config_path):
                print(f"Config: {config_path}")
                print(f"  ** This config is in /root/ - not editable without sudo **")
                print(f"  ** Use 'Edit Reticulum Config' to migrate to /etc/reticulum/ **\n")
            else:
                print(f"Config: {config_path}\n")
            try:
                content = config_path.read_text()
                print(content)

                # Show validation warnings inline
                issues = self._validate_rns_config_content(content)
                if issues:
                    print("\n--- Config Issues ---")
                    for issue in issues:
                        print(f"  ! {issue}")
            except PermissionError:
                print(f"Permission denied reading {config_path}")
                print(f"Try: sudo cat {config_path}")
        else:
            print(f"No Reticulum config found at: {config_path}")
            user_home = get_real_user_home()
            print(f"\nMeshForge checks (in order):")
            print(f"  1. /etc/reticulum/config  (system-wide, preferred)")
            print(f"  2. {user_home}/.config/reticulum/config")
            print(f"  3. {user_home}/.reticulum/config")
            if os.geteuid() == 0 and os.environ.get('SUDO_USER'):
                print(f"\nNote: rnsd (running as root) uses /root/.reticulum/config")
                print(f"  For shared use, deploy to /etc/reticulum/config")
            print(f"\nTo create: use 'Edit Reticulum Config' to deploy template")
            print(f"Template:  templates/reticulum.conf")

        # Show Meshtastic_Interface plugin status
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        print(f"\n--- Meshtastic Interface Plugin ---")
        if plugin_path.exists():
            print(f"  Installed: {plugin_path}")
            print(f"  Size: {plugin_path.stat().st_size} bytes")
        else:
            print(f"  NOT INSTALLED")
            print(f"  Expected at: {plugin_path}")
            print(f"  Source: https://github.com/landandair/RNS_Over_Meshtastic")
            print(f"  Use 'Install Meshtastic Interface' from the RNS menu to install.")

        self._wait_for_enter()

    def _edit_rns_config(self):
        """Edit Reticulum config with available editor. Deploys template if no config exists."""
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            # Offer to deploy from template to /etc/reticulum/config (system-wide)
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'

            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.dialog.yesno(
                    "Deploy Reticulum Config",
                    f"No Reticulum config found.\n\n"
                    f"Deploy template to:\n  {target}\n\n"
                    f"This sets up RNS with:\n"
                    f"  - share_instance = Yes (required for rnstatus)\n"
                    f"  - AutoInterface (local network discovery)\n"
                    f"  - Meshtastic_Interface on port 4403\n\n"
                    f"You can edit it after deployment."
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        config_path = deployed
                    else:
                        return
                else:  # User said No
                    return
            else:
                self.dialog.msgbox(
                    "No Config",
                    "No Reticulum config found and template missing.\n\n"
                    "Install RNS first: pipx install rns\n"
                    "Then run rnsd once to generate default config."
                )
                return

        # If config is in /root/, offer to migrate to /etc/reticulum/
        if self._is_root_owned_rns_config(config_path):
            if self.dialog.yesno(
                "Migrate Config",
                f"Config is at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by rnsd and all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )
                # If migration failed, continue with original path

        # Find editor
        editor = None
        for cmd in ['nano', 'vim', 'vi']:
            if shutil.which(cmd):
                editor = cmd
                break

        if not editor:
            self.dialog.msgbox("Error", "No text editor found (nano, vim, vi)")
            return

        subprocess.run([editor, str(config_path)], timeout=None)

        # After editing, check for config divergence between user and root
        self._check_rns_config_divergence(config_path)

    def _check_rns_config_divergence(self, edited_path: Path):
        """Check if edited config differs from root/system config that rnsd actually uses.

        When running with sudo, user edits /home/user/.reticulum/config but
        rnsd (running as root) reads /root/.reticulum/config. The configs
        silently diverge, causing the user's changes to have no effect.

        This check warns the user and offers to sync.
        """
        import os

        # Only relevant when running as root/sudo
        if os.geteuid() != 0:
            return

        # Find where rnsd actually reads its config
        # rnsd systemd service runs as root, so it reads from one of:
        root_configs = [
            Path('/etc/reticulum/config'),
            Path('/root/.config/reticulum/config'),
            Path('/root/.reticulum/config'),
        ]

        # Skip if edited path is already a root/system path
        edited_str = str(edited_path)
        if edited_str.startswith('/root/') or edited_str.startswith('/etc/'):
            return

        for root_config in root_configs:
            if root_config.exists() and root_config != edited_path:
                # Compare contents
                try:
                    user_content = edited_path.read_text()
                    root_content = root_config.read_text()

                    if user_content != root_content:
                        if self.dialog.yesno(
                            "Config Divergence Detected",
                            f"WARNING: Your edited config:\n"
                            f"  {edited_path}\n\n"
                            f"differs from the config rnsd uses:\n"
                            f"  {root_config}\n\n"
                            f"rnsd runs as root and reads {root_config}.\n"
                            f"Your changes won't take effect until synced.\n\n"
                            f"Copy your config to {root_config}?"
                        ):
                            try:
                                import shutil
                                # Backup root config first
                                backup = root_config.with_suffix('.config.bak')
                                if root_config.exists():
                                    shutil.copy2(str(root_config), str(backup))
                                shutil.copy2(str(edited_path), str(root_config))
                                self.dialog.msgbox(
                                    "Config Synced",
                                    f"Copied to: {root_config}\n"
                                    f"Backup at: {backup}\n\n"
                                    f"Restart rnsd to apply:\n"
                                    f"  sudo systemctl restart rnsd"
                                )
                            except Exception as e:
                                self.dialog.msgbox(
                                    "Sync Failed",
                                    f"Could not copy config: {e}\n\n"
                                    f"Manual fix:\n"
                                    f"  sudo cp {edited_path} {root_config}\n"
                                    f"  sudo systemctl restart rnsd"
                                )
                except (OSError, subprocess.SubprocessError) as e:
                    logger.debug("RNS config apply failed: %s", e)
                return  # Only check the first existing root config

    def _validate_rns_config_content(self, content: str) -> list:
        """Validate RNS config content and return list of issues found.

        Checks for common misconfigurations that cause rnstatus/rnpath failures:
        - Missing [reticulum] section
        - Missing share_instance = Yes (required for client apps to connect)
        - No interfaces configured
        - No Meshtastic_Interface (needed for mesh bridging)
        - Meshtastic_Interface.py plugin not installed
        """
        issues = []
        content_lower = content.lower()

        # Check [reticulum] section exists
        if '[reticulum]' not in content_lower:
            issues.append("Missing [reticulum] section")

        # Check share_instance (required for rnstatus/rnpath to connect to rnsd)
        has_share = False
        for line in content.split('\n'):
            stripped = line.strip().lower()
            if stripped.startswith('#'):
                continue
            if 'share_instance' in stripped:
                if 'yes' in stripped or 'true' in stripped:
                    has_share = True
                break
        if not has_share:
            issues.append("share_instance not set to Yes (rnstatus/client apps won't connect)")

        # Check for at least one active interface
        has_interface = False
        has_meshtastic = False
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if stripped.startswith('[[') and stripped.endswith(']]'):
                has_interface = True
            if 'meshtastic_interface' in stripped.lower() and 'type' in stripped.lower():
                has_meshtastic = True

        if not has_interface:
            issues.append("No interfaces configured")

        # Check Meshtastic_Interface status: config reference + plugin file
        plugin_path = ReticulumPaths.get_interfaces_dir() / 'Meshtastic_Interface.py'
        plugin_installed = plugin_path.exists()

        if not has_meshtastic and not plugin_installed:
            issues.append("No Meshtastic_Interface configured (needed for mesh bridging)")
        elif has_meshtastic and not plugin_installed:
            issues.append(
                f"Meshtastic_Interface.py plugin not installed at "
                f"{ReticulumPaths.get_interfaces_dir()}/\n"
                f"    Install from: https://github.com/landandair/RNS_Over_Meshtastic"
            )

        return issues

    def _check_rns_setup(self) -> bool:
        """Check RNS setup and offer to fix common issues.

        Available via 'Check RNS Setup' menu item. Returns True if setup
        looks OK or user chose to continue anyway, False if user wants
        to go back.
        """
        config_path = ReticulumPaths.get_config_file()

        if not config_path.exists():
            template = Path(__file__).parent.parent.parent / 'templates' / 'reticulum.conf'
            if template.exists():
                target = Path('/etc/reticulum/config')
                if self.dialog.yesno(
                    "RNS Not Configured",
                    f"No Reticulum config found.\n\n"
                    f"RNS tools (rnstatus, rnpath) and the gateway bridge\n"
                    f"require a config file to function.\n\n"
                    f"Deploy MeshForge template to:\n"
                    f"  {target}\n\n"
                    f"(Sets up shared instance + Meshtastic bridge)"
                ):
                    deployed = self._deploy_rns_template()
                    if deployed:
                        self.dialog.msgbox(
                            "Config Deployed",
                            f"Deployed to: {deployed}\n\n"
                            f"Restart rnsd to apply:\n"
                            f"  sudo systemctl restart rnsd"
                        )
                        config_path = deployed
            return True  # Continue to menu either way

        # Config exists - check if it's in a root-only location
        if self._is_root_owned_rns_config(config_path):
            if self.dialog.yesno(
                "Config in /root/",
                f"RNS config found at:\n  {config_path}\n\n"
                f"This location requires root access to edit.\n\n"
                f"Migrate to /etc/reticulum/config?\n"
                f"(System-wide location, accessible by all users)"
            ):
                if self._migrate_rns_config_to_etc(config_path):
                    config_path = Path('/etc/reticulum/config')
                    self.dialog.msgbox(
                        "Migrated",
                        f"Config moved to: {config_path}\n\n"
                        f"Restart rnsd to apply:\n"
                        f"  sudo systemctl restart rnsd"
                    )

        # Config exists - validate it
        try:
            content = config_path.read_text()
            issues = self._validate_rns_config_content(content)
            if issues:
                msg = f"Config: {config_path}\n\nIssues found:\n"
                for issue in issues:
                    msg += f"  - {issue}\n"
                msg += f"\nUse 'Edit Reticulum Config' to fix these issues."
                self.dialog.msgbox("RNS Config Issues", msg)
        except PermissionError:
            self.dialog.msgbox(
                "Permission Denied",
                f"Cannot read config at:\n  {config_path}\n\n"
                f"Run MeshForge with sudo to access this file,\n"
                f"or use 'Edit Reticulum Config' to migrate it."
            )

        # Check for Meshtastic_Interface.py plugin (separate from config validation)
        if not self._check_meshtastic_plugin():
            if self.dialog.yesno(
                "Meshtastic Interface Plugin Missing",
                "The Meshtastic_Interface.py plugin is not installed.\n\n"
                "This plugin is required for bridging RNS over\n"
                "Meshtastic LoRa mesh networks.\n\n"
                f"Expected at:\n"
                f"  {ReticulumPaths.get_interfaces_dir()}/Meshtastic_Interface.py\n\n"
                "Download and install it now?"
            ):
                self._install_meshtastic_interface_plugin()

        return True

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
                # RNS shared instance issue — diagnose before acting.
                # The auto-fix (restart rnsd, deploy template) is destructive
                # and only helps if rnsd is genuinely not running. If rnsd IS
                # running, the problem is something else (entropy, auth, config
                # mismatch) and blind restarts make things worse.
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
                    # rnsd is NOT running — auto-fix can help (start it)
                    print("rnsd is not running. Attempting to start...\n")
                    if self._auto_fix_rns_shared_instance():
                        print(f"\nRetrying {tool_name}...\n")
                        retry_result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=15
                        )
                        if retry_result.returncode == 0 and retry_result.stdout:
                            print(retry_result.stdout, end='')
                        elif retry_result.stdout:
                            print(retry_result.stdout, end='')
                    else:
                        print("\nCould not start rnsd.")
                        print("Check logs: sudo journalctl -u rnsd -n 30")
                else:
                    # rnsd IS running but tools can't connect — show diagnostics
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

    def _diagnose_rns_connectivity(self, error_output: str):
        """Show targeted diagnostics when rnsd is running but tools can't connect.

        Instead of blindly restarting rnsd (which loses entropy and makes the
        problem worse), diagnose the actual issue.
        """
        lower = error_output.lower()
        print("rnsd is running but RNS tools cannot connect.\n")

        # Check 1: Authentication error (stale tokens after config change)
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

        # Check 2: Entropy starvation (rnsd hangs at crypto init)
        try:
            with open('/proc/sys/kernel/random/entropy_avail', 'r') as f:
                entropy = int(f.read().strip())
            if entropy < 256:
                print(f"Cause: Low system entropy ({entropy} bits available)")
                print("       rnsd is likely hanging on cryptographic initialization.\n")
                print("Fix:   Install entropy daemon:\n")
                print("  sudo apt install rng-tools    # preferred on Pi")
                print("  sudo systemctl enable --now rngd")
                print("\n  Then restart rnsd:")
                print("  sudo systemctl restart rnsd")
                return
        except (OSError, ValueError):
            pass

        # Check 3: rnsd started but not yet listening (slow init)
        try:
            r = subprocess.run(
                ['ss', '-tlnp', 'sport', '=', ':37428'],
                capture_output=True, text=True, timeout=5
            )
            if ':37428' not in r.stdout:
                print("Cause: rnsd is active but not listening on port 37428 yet.")
                print("       This can happen when rnsd is still initializing")
                print("       (crypto key generation, slow storage, low entropy).\n")
                print("Check: sudo journalctl -u rnsd -n 20 --no-pager")
                print("Wait:  Give it 30-60 seconds, then retry.")
                return
        except (subprocess.SubprocessError, OSError):
            pass

        # Check 4: Config drift (rnsd using different config than expected)
        print("Possible causes:")
        print("  - Config drift: rnsd may be using a different config path")
        print("  - Storage permissions: /etc/reticulum/storage/ may not be writable")
        print("  - Stale state: shared instance tokens may be invalid\n")
        print("Diagnostics:")
        print("  sudo journalctl -u rnsd -n 30 --no-pager")
        print("  sudo systemctl restart rnsd")
        print("  ls -la /etc/reticulum/storage/")

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
        import time
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

    # Sniffer methods (_rns_traffic_sniffer, _rns_sniffer_*) are inherited
    # from RNSSnifferMixin - see rns_sniffer_mixin.py
