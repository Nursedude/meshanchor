"""
Service Discovery Mixin - P3 Network Scanner / Auto-Discovery

Provides unified discovery of all mesh network services:
- Meshtasticd (TCP 4403)
- RNS/rnsd (UDP 37428)
- AREDN nodes (HTTP *.local.mesh)
- USB devices (serial ports)
"""

import socket
import subprocess
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# Import service check for consistent status detection
try:
    from utils.service_check import check_service, check_port
except ImportError:
    check_service = None
    check_port = None

# Import device scanner
try:
    from utils.device_scanner import DeviceScanner
except ImportError:
    DeviceScanner = None


@dataclass
class DiscoveredService:
    """A discovered network service"""
    name: str
    status: str  # "running", "stopped", "unknown"
    address: str  # Host:port or device path
    service_type: str  # "meshtastic", "rns", "aredn", "usb"
    details: str = ""


class ServiceDiscoveryMixin:
    """Mixin for unified service discovery in launcher TUI"""

    def _service_discovery_menu(self):
        """P3: Network scanner / service discovery menu"""
        while True:
            choices = [
                ("scan", "Quick Scan (Local Services)"),
                ("full", "Full Network Scan"),
                ("usb", "USB Device Scan"),
                ("status", "Service Status Overview"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Service Discovery",
                "Auto-discover mesh network services:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "scan":
                self._quick_scan()
            elif choice == "full":
                self._full_network_scan()
            elif choice == "usb":
                self._usb_device_scan()
            elif choice == "status":
                self._service_status_overview()

    def _quick_scan(self):
        """Quick scan of local services"""
        self.dialog.infobox("Scanning", "Discovering local services...")

        services = []

        # Check meshtasticd
        mesh_status = check_service('meshtasticd')
        services.append(DiscoveredService(
            name="meshtasticd",
            status="running" if mesh_status.running else "stopped",
            address="localhost:4403",
            service_type="meshtastic",
            details=mesh_status.message or ""
        ))

        # Check rnsd (UDP port)
        rns_status = check_service('rnsd')
        services.append(DiscoveredService(
            name="rnsd",
            status="running" if rns_status.running else "stopped",
            address="UDP 37428",
            service_type="rns",
            details=rns_status.message or ""
        ))

        # Check HamClock
        hc_status = check_service('hamclock')
        services.append(DiscoveredService(
            name="HamClock",
            status="running" if hc_status.running else "stopped",
            address="localhost:8080",
            service_type="hamclock",
            details=hc_status.message or ""
        ))

        # Check for AREDN (10.x.x.x network)
        aredn_found = self._check_aredn_network()
        services.append(DiscoveredService(
            name="AREDN Network",
            status="detected" if aredn_found else "not found",
            address="10.0.0.0/8",
            service_type="aredn",
            details="AREDN mesh network interface" if aredn_found else ""
        ))

        # Format results
        lines = ["Service Discovery Results\n"]
        lines.append("=" * 40)

        for svc in services:
            status_icon = "✓" if svc.status == "running" or svc.status == "detected" else "✗"
            lines.append(f"\n{status_icon} {svc.name}")
            lines.append(f"  Status: {svc.status}")
            lines.append(f"  Address: {svc.address}")
            if svc.details:
                lines.append(f"  Info: {svc.details}")

        self.dialog.msgbox("Discovery Results", "\n".join(lines))

    def _check_aredn_network(self) -> bool:
        """Check if we have an AREDN network interface"""
        try:
            result = subprocess.run(
                ['ip', 'addr'],
                capture_output=True, text=True, timeout=5
            )
            # AREDN uses 10.x.x.x addresses
            return '10.' in result.stdout and 'inet 10.' in result.stdout
        except Exception:
            return False

    def _full_network_scan(self):
        """Full network scan for Meshtastic devices"""
        # Get network range
        network = self._detect_network_range()

        network = self.dialog.inputbox(
            "Network Scan",
            "Enter network range to scan:",
            network
        )

        if not network:
            return

        self.dialog.infobox("Scanning", f"Scanning {network} for Meshtastic devices...\nThis may take a minute...")

        # Check for nmap
        nmap_available = subprocess.run(
            ['which', 'nmap'],
            capture_output=True, timeout=5
        ).returncode == 0

        found_devices = []

        if nmap_available:
            try:
                result = subprocess.run(
                    ['nmap', '-p', '4403', '--open', '-oG', '-', network],
                    capture_output=True, text=True, timeout=120
                )
                # Parse nmap output
                for line in result.stdout.split('\n'):
                    if '4403/open' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            found_devices.append(parts[1])
            except Exception:
                pass
        else:
            # Manual scan
            base = '.'.join(network.split('.')[:3])
            for i in range(1, 255):
                ip = f"{base}.{i}"
                if check_port(ip, 4403, timeout=0.3):
                    found_devices.append(ip)

        # Show results
        if found_devices:
            lines = [f"Found {len(found_devices)} Meshtastic device(s):\n"]
            for ip in found_devices:
                lines.append(f"  • {ip}:4403")
            self.dialog.msgbox("Network Scan Results", "\n".join(lines))
        else:
            self.dialog.msgbox("Network Scan", "No Meshtastic devices found on port 4403")

    def _detect_network_range(self) -> str:
        """Detect local network range"""
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=5
            )
            parts = result.stdout.split()
            if 'via' in parts:
                gateway_idx = parts.index('via') + 1
                gateway = parts[gateway_idx]
                return '.'.join(gateway.split('.')[:3]) + '.0/24'
        except Exception:
            pass
        return "192.168.1.0/24"

    def _usb_device_scan(self):
        """Scan for USB devices"""
        if DeviceScanner is None:
            self.dialog.msgbox("Error", "Device scanner not available")
            return

        self.dialog.infobox("Scanning", "Scanning USB devices...")

        scanner = DeviceScanner()
        results = scanner.scan_all()

        # Format results
        lines = ["USB Device Scan Results\n"]
        lines.append("=" * 40)

        if results['meshtastic_candidates']:
            lines.append(f"\nMeshtastic-compatible devices ({len(results['meshtastic_candidates'])}):")
            for dev in results['meshtastic_candidates']:
                lines.append(f"  • {dev.description}")
                if dev.notes:
                    lines.append(f"    {dev.notes}")

        if results['serial_ports']:
            lines.append(f"\nSerial Ports ({len(results['serial_ports'])}):")
            for port in results['serial_ports']:
                compat = "✓" if port.meshtastic_compatible else " "
                lines.append(f"  [{compat}] {port.device}")
                if port.description:
                    lines.append(f"      {port.description}")

        if results['recommended_port']:
            lines.append(f"\nRecommended port: {results['recommended_port']}")

        if not results['serial_ports'] and not results['meshtastic_candidates']:
            lines.append("\nNo USB serial devices found")
            lines.append("Connect a Meshtastic device and try again")

        self.dialog.msgbox("USB Scan Results", "\n".join(lines))

    def _service_status_overview(self):
        """Show status of all mesh services"""
        self.dialog.infobox("Checking", "Checking service status...")

        # Check all known services
        services = [
            ('meshtasticd', 'Meshtastic Daemon'),
            ('rnsd', 'Reticulum Network Stack'),
            ('hamclock', 'HamClock Space Weather'),
        ]

        lines = ["MeshForge Service Status\n"]
        lines.append("=" * 40)

        for svc_id, svc_name in services:
            status = check_service(svc_id)
            icon = "✓" if status.running else "✗"
            state = "Running" if status.running else "Stopped"
            lines.append(f"\n{icon} {svc_name}")
            lines.append(f"  Status: {state}")
            if status.message:
                lines.append(f"  Info: {status.message}")

        # Add quick actions hint
        lines.append("\n" + "-" * 40)
        lines.append("Use Service Manager to start/stop services")

        self.dialog.msgbox("Service Status", "\n".join(lines))
