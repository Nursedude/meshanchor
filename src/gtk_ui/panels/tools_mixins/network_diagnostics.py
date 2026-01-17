"""
Network Diagnostics Mixin - Advanced network analysis

Provides /proc/net parsing, port listener enumeration, and service diagnostics.
Extracted from tools.py for maintainability.
"""

import os
import re
import struct
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib

from utils.ports import (
    MESHTASTICD_PORT, MESHTASTICD_WEB_PORT,
    RNS_SHARED_INSTANCE_PORT, RNS_TCP_SERVER_PORT
)


class NetworkDiagnosticsMixin:
    """Mixin providing network diagnostics functionality for ToolsPanel"""

    # TCP state mapping
    TCP_STATES = {
        '01': 'ESTABLISHED',
        '02': 'SYN_SENT',
        '03': 'SYN_RECV',
        '04': 'FIN_WAIT1',
        '05': 'FIN_WAIT2',
        '06': 'TIME_WAIT',
        '07': 'CLOSE',
        '08': 'CLOSE_WAIT',
        '09': 'LAST_ACK',
        '0A': 'LISTEN',
        '0B': 'CLOSING',
    }

    def _parse_proc_net(self, protocol: str) -> list:
        """Parse /proc/net/{tcp,udp} for connection info.

        Args:
            protocol: 'tcp' or 'udp'

        Returns:
            List of connection dicts with port, state, inode info
        """
        connections = []
        proc_file = f'/proc/net/{protocol}'

        if not os.path.exists(proc_file):
            return connections

        try:
            with open(proc_file, 'r') as f:
                lines = f.readlines()[1:]  # Skip header

            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue

                local_addr = parts[1]
                remote_addr = parts[2]
                state = parts[3]
                inode = parts[9] if len(parts) > 9 else '0'

                # Parse hex addresses
                local_ip_hex, local_port_hex = local_addr.split(':')
                local_port = int(local_port_hex, 16)

                remote_ip_hex, remote_port_hex = remote_addr.split(':')
                remote_port = int(remote_port_hex, 16)

                connections.append({
                    'local_port': local_port,
                    'remote_port': remote_port,
                    'state': self.TCP_STATES.get(state, state),
                    'state_hex': state,
                    'inode': inode,
                })

        except Exception:
            pass

        return connections

    def _get_inode_to_process(self) -> dict:
        """Map socket inodes to process info by scanning /proc/*/fd.

        Returns:
            Dict mapping inode strings to (pid, process_name) tuples
        """
        inode_map = {}

        try:
            for pid_dir in Path('/proc').iterdir():
                if not pid_dir.name.isdigit():
                    continue

                pid = pid_dir.name
                fd_dir = pid_dir / 'fd'

                try:
                    # Get process name
                    comm_file = pid_dir / 'comm'
                    if comm_file.exists():
                        proc_name = comm_file.read_text().strip()
                    else:
                        proc_name = 'unknown'

                    # Scan file descriptors
                    for fd in fd_dir.iterdir():
                        try:
                            link = fd.resolve()
                            link_str = str(link)
                            if link_str.startswith('socket:['):
                                inode = link_str[8:-1]  # Extract inode
                                inode_map[inode] = (pid, proc_name)
                        except (PermissionError, FileNotFoundError):
                            pass
                except (PermissionError, FileNotFoundError):
                    pass
        except Exception:
            pass

        return inode_map

    def _on_show_udp_listeners(self, button=None):
        """Show UDP port listeners"""
        threading.Thread(target=self._fetch_udp_listeners, daemon=True).start()

    def _fetch_udp_listeners(self):
        """Fetch UDP listener info"""
        GLib.idle_add(self._log, "\n=== UDP Listeners ===")

        connections = self._parse_proc_net('udp')
        inode_map = self._get_inode_to_process()

        for conn in connections:
            if conn['local_port'] > 0:
                pid, proc = inode_map.get(conn['inode'], ('?', 'unknown'))
                GLib.idle_add(
                    self._log,
                    f"  UDP :{conn['local_port']} - {proc} (PID: {pid})"
                )

        if not connections:
            GLib.idle_add(self._log, "  No UDP listeners found")

    def _on_show_tcp_listeners(self, button=None):
        """Show TCP port listeners"""
        threading.Thread(target=self._fetch_tcp_listeners, daemon=True).start()

    def _fetch_tcp_listeners(self):
        """Fetch TCP listener info"""
        GLib.idle_add(self._log, "\n=== TCP Listeners ===")

        connections = self._parse_proc_net('tcp')
        inode_map = self._get_inode_to_process()

        listeners = [c for c in connections if c['state'] == 'LISTEN']

        for conn in listeners:
            pid, proc = inode_map.get(conn['inode'], ('?', 'unknown'))
            GLib.idle_add(
                self._log,
                f"  TCP :{conn['local_port']} - {proc} (PID: {pid})"
            )

        if not listeners:
            GLib.idle_add(self._log, "  No TCP listeners found")

    def _on_check_rns_ports(self, button=None):
        """Check RNS-related ports"""
        threading.Thread(target=self._check_rns_ports_thread, daemon=True).start()

    def _check_rns_ports_thread(self):
        """Check RNS ports in background"""
        GLib.idle_add(self._log, "\n=== RNS Port Check ===")

        rns_ports = [
            (RNS_SHARED_INSTANCE_PORT, 'UDP', 'RNS Shared Instance'),
            (RNS_TCP_SERVER_PORT, 'TCP', 'RNS TCP Server'),
        ]

        import socket
        for port, proto, name in rns_ports:
            sock = None
            try:
                sock_type = socket.SOCK_DGRAM if proto == 'UDP' else socket.SOCK_STREAM
                sock = socket.socket(socket.AF_INET, sock_type)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))

                status = "OPEN" if result == 0 else "CLOSED"
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): {status}")
            except Exception as e:
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): Error - {e}")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

    def _on_check_mesh_ports(self, button=None):
        """Check Meshtastic-related ports"""
        threading.Thread(target=self._check_mesh_ports_thread, daemon=True).start()

    def _check_mesh_ports_thread(self):
        """Check Meshtastic ports in background"""
        GLib.idle_add(self._log, "\n=== Meshtastic Port Check ===")

        mesh_ports = [
            (MESHTASTICD_PORT, 'TCP', 'meshtasticd API'),
            (MESHTASTICD_WEB_PORT, 'TCP', 'meshtasticd gRPC'),
        ]

        import socket
        for port, proto, name in mesh_ports:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))

                status = "OPEN" if result == 0 else "CLOSED"
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): {status}")
            except Exception as e:
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): Error - {e}")
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

    def _on_full_network_diagnostics(self, button=None):
        """Run comprehensive network diagnostics"""
        threading.Thread(target=self._run_full_diagnostics, daemon=True).start()

    def _run_full_diagnostics(self):
        """Run full network diagnostics in background"""
        GLib.idle_add(self._log, "\n" + "=" * 50)
        GLib.idle_add(self._log, "FULL NETWORK DIAGNOSTICS")
        GLib.idle_add(self._log, "=" * 50)

        # TCP listeners
        self._fetch_tcp_listeners()

        # UDP listeners
        self._fetch_udp_listeners()

        # RNS ports
        self._check_rns_ports_thread()

        # Meshtastic ports
        self._check_mesh_ports_thread()

        GLib.idle_add(self._log, "\n" + "=" * 50)
        GLib.idle_add(self._log, "Diagnostics complete")

    def _on_show_multicast(self, button=None):
        """Show multicast group memberships"""
        self._log("\n=== Multicast Group Memberships ===")
        threading.Thread(target=self._fetch_multicast, daemon=True).start()

    def _fetch_multicast(self):
        """Fetch multicast groups in background"""
        # Method 1: Parse /proc/net/igmp
        GLib.idle_add(self._log, "\n-- IGMP Groups (/proc/net/igmp) --")
        try:
            with open('/proc/net/igmp', 'r') as f:
                lines = f.readlines()

            current_device = None
            for line in lines[1:]:  # Skip header
                if line[0].isdigit():
                    # Device line
                    parts = line.split()
                    if len(parts) >= 2:
                        current_device = parts[1].rstrip(':')
                        GLib.idle_add(self._log, f"\n{current_device}:")
                elif line.strip().startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'A', 'B', 'C', 'D', 'E', 'F')):
                    # Group line - hex address (little-endian)
                    parts = line.split()
                    if parts:
                        hex_group = parts[0]
                        try:
                            # Convert hex to IP (multicast addresses)
                            group_int = int(hex_group, 16)
                            group_bytes = [
                                (group_int >> 0) & 0xFF,
                                (group_int >> 8) & 0xFF,
                                (group_int >> 16) & 0xFF,
                                (group_int >> 24) & 0xFF,
                            ]
                            group_ip = '.'.join(str(b) for b in group_bytes)
                            GLib.idle_add(self._log, f"  {group_ip}")
                        except ValueError:
                            GLib.idle_add(self._log, f"  {hex_group}")

        except FileNotFoundError:
            GLib.idle_add(self._log, "  /proc/net/igmp not found")
        except PermissionError:
            GLib.idle_add(self._log, "  Permission denied")

        # Method 2: Use ip maddr command
        GLib.idle_add(self._log, "\n-- IP Multicast Addresses (ip maddr) --")
        try:
            result = subprocess.run(
                ['ip', 'maddr', 'show'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, line)
            else:
                GLib.idle_add(self._log, "  ip maddr command failed")
        except FileNotFoundError:
            GLib.idle_add(self._log, "  ip command not found")
        except Exception as e:
            GLib.idle_add(self._log, f"  Error: {e}")

    def _on_show_process_ports(self, button=None):
        """Show process to port mapping"""
        self._log("\n=== Process → Port Mapping ===")
        threading.Thread(target=self._fetch_process_ports, daemon=True).start()

    def _fetch_process_ports(self):
        """Fetch process-port mapping using ss"""
        # Try ss first (faster), fall back to netstat
        try:
            result = subprocess.run(
                ['ss', '-tulnp'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "Using: ss -tulnp")
                GLib.idle_add(self._log, "-" * 80)
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, line[:100])
                return
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # Fall back to netstat
        try:
            result = subprocess.run(
                ['netstat', '-tulnp'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                GLib.idle_add(self._log, "Using: netstat -tulnp")
                GLib.idle_add(self._log, "-" * 80)
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, line[:100])
                return
        except FileNotFoundError:
            GLib.idle_add(self._log, "Neither ss nor netstat found. Install: sudo apt install iproute2")
        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_watch_api_connections(self, button=None):
        """Watch connections to meshtasticd API port"""
        self._log("\n=== Watching API Connections (port 4403) ===")
        self._log("Refreshing every 2 seconds... Click again to refresh.\n")
        threading.Thread(target=self._watch_api_connections_thread, daemon=True).start()

    def _watch_api_connections_thread(self):
        """Show current connections to port 4403"""
        try:
            # Use ss to show connections
            result = subprocess.run(
                ['ss', '-tnp', 'sport', '=', ':4403', 'or', 'dport', '=', ':4403'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                GLib.idle_add(self._log, f"Active connections to meshtasticd:")
                for line in lines:
                    GLib.idle_add(self._log, f"  {line[:90]}")
            else:
                # Fallback - check who has the port open
                result = subprocess.run(
                    ['ss', '-tlnp'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if '4403' in line:
                            GLib.idle_add(self._log, line[:90])

            # Also show established connections
            tcp_entries = self._parse_proc_net('tcp')
            inode_map = self._get_inode_to_process()

            connected = []
            for entry in tcp_entries:
                if entry['local_port'] == 4403 and entry['state'] == 'ESTABLISHED':
                    pid, proc = inode_map.get(entry['inode'], ('?', 'unknown'))
                    connected.append(f":{entry['local_port']} - {proc} (PID: {pid})")

            if connected:
                GLib.idle_add(self._log, f"\nEstablished connections:")
                for c in connected:
                    GLib.idle_add(self._log, f"  {c}")
            else:
                GLib.idle_add(self._log, "\nNo active client connections to port 4403")

        except Exception as e:
            GLib.idle_add(self._log, f"Error: {e}")

    def _on_kill_competing_clients(self, button=None):
        """Kill processes that compete for meshtasticd connection"""
        self._log("\n=== Killing Competing Clients ===")
        threading.Thread(target=self._kill_competing_clients_thread, daemon=True).start()

    def _kill_competing_clients_thread(self):
        """Kill nomadnet and python meshtastic clients"""
        killed = []

        # Kill nomadnet
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', 'nomadnet'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append('nomadnet')
        except Exception:
            pass

        # Kill python meshtastic clients (but not meshtasticd itself)
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', 'python.*meshtastic'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append('python meshtastic')
        except Exception:
            pass

        # Kill any lxmf processes
        try:
            result = subprocess.run(
                ['pkill', '-9', '-f', 'lxmf'],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                killed.append('lxmf')
        except Exception:  # Process may not exist - non-critical
            pass

        if killed:
            GLib.idle_add(self._log, f"Killed: {', '.join(killed)}")
        else:
            GLib.idle_add(self._log, "No competing clients found to kill")

        # Verify
        GLib.idle_add(self._log, "\nRemaining processes:")
        try:
            result = subprocess.run(
                ['pgrep', '-a', '-f', 'nomadnet|lxmf|meshtastic'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    # Filter out meshtasticd itself
                    if 'meshtasticd' not in line:
                        GLib.idle_add(self._log, f"  {line}")
                    else:
                        GLib.idle_add(self._log, f"  {line} (daemon - OK)")
            else:
                GLib.idle_add(self._log, "  None (clean)")
        except Exception:
            pass

    def _on_stop_all_rns(self, button=None):
        """Stop all RNS-related processes"""
        self._log("\n=== Stopping All RNS Processes ===")
        threading.Thread(target=self._stop_all_rns_thread, daemon=True).start()

    def _stop_all_rns_thread(self):
        """Kill all RNS processes"""
        import time
        killed = []

        processes = ['rnsd', 'nomadnet', 'lxmf', 'RNS']
        for proc in processes:
            try:
                result = subprocess.run(
                    ['pkill', '-9', '-f', proc],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    killed.append(proc)
            except Exception:
                pass

        if killed:
            GLib.idle_add(self._log, f"Killed: {', '.join(killed)}")
        else:
            GLib.idle_add(self._log, "No RNS processes found")

        # Check what's left
        GLib.idle_add(self._log, "\nVerifying...")
        time.sleep(0.5)

        try:
            result = subprocess.run(
                ['pgrep', '-a', '-f', 'rnsd|nomadnet|lxmf|RNS'],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                GLib.idle_add(self._log, "Still running:")
                for line in result.stdout.strip().split('\n'):
                    GLib.idle_add(self._log, f"  {line}")
            else:
                GLib.idle_add(self._log, "All RNS processes stopped ✓")
        except Exception:
            GLib.idle_add(self._log, "Verification complete")

        # Check port 29716
        GLib.idle_add(self._log, "\nChecking port 29716...")
        try:
            result = subprocess.run(
                ['ss', '-ulnp'],
                capture_output=True, text=True, timeout=5
            )
            found = False
            for line in result.stdout.split('\n'):
                if '29716' in line:
                    found = True
                    GLib.idle_add(self._log, f"  Still bound: {line[:80]}")
            if not found:
                GLib.idle_add(self._log, "  Port 29716 is free ✓")
        except Exception:
            pass
