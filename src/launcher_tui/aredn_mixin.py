"""
AREDN Menu Mixin - AREDN mesh network menu handlers.

Extracted from main.py to reduce file size per CLAUDE.md guidelines.
"""

import subprocess


class AREDNMixin:
    """Mixin providing AREDN mesh network menu functionality."""

    def _aredn_menu(self):
        """AREDN mesh network tools."""
        while True:
            choices = [
                ("status", "Node Status"),
                ("neighbors", "Neighbors & Links"),
                ("services", "Advertised Services"),
                ("map", "Show on Map"),
                ("web", "Open AREDN Web UI"),
                ("scan", "Scan Network"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "AREDN Mesh",
                "AREDN mesh network tools:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._aredn_node_status()
            elif choice == "neighbors":
                self._aredn_neighbors()
            elif choice == "services":
                self._aredn_services()
            elif choice == "map":
                self._aredn_map()
            elif choice == "web":
                self._aredn_web()
            elif choice == "scan":
                self._aredn_scan()

    def _aredn_get_node_ip(self) -> str:
        """Get AREDN node IP - try common defaults."""
        import socket
        # Try common AREDN addresses
        for host in ['localnode.local.mesh', '10.0.0.1', 'localnode']:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    result = sock.connect_ex((host, 8080))  # AREDN serves on 8080
                    if result == 0:
                        return host
                finally:
                    sock.close()
            except Exception:
                continue
        return ""

    def _aredn_node_status(self):
        """Show local AREDN node status."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Node Status ===\n")

        try:
            from utils.aredn import get_aredn_node

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found on local network.")
                print("\nTried: localnode.local.mesh, 10.0.0.1")
                print("\nIs your AREDN node connected?")
                self._wait_for_enter()
                return

            print(f"Connecting to {node_ip}...\n")
            node = get_aredn_node(node_ip)

            if node:
                print(f"  Hostname:  {node.hostname}")
                print(f"  IP:        {node.ip}")
                print(f"  Model:     {node.model}")
                print(f"  Firmware:  {node.firmware_version}")
                print(f"  SSID:      {node.ssid}")
                print(f"  Channel:   {node.channel} ({node.frequency})")
                print(f"  Width:     {node.channel_width}")
                print(f"  Status:    {node.mesh_status}")
                print(f"  Uptime:    {node.uptime}")
                print(f"  Tunnels:   {node.tunnel_count}")
                if node.loads:
                    print(f"  Load:      {', '.join(str(l) for l in node.loads)}")
            else:
                print(f"Connected to {node_ip} but couldn't parse node info.")
                print(f"Check: http://{node_ip}:8080/cgi-bin/sysinfo.json")

        except ImportError:
            print("AREDN utilities not available.")
            print("Check: src/utils/aredn.py")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_neighbors(self):
        """Show AREDN neighbor links."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Neighbors ===\n")

        try:
            from utils.aredn import AREDNClient

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found. Is it connected?")
                self._wait_for_enter()
                return

            client = AREDNClient(node_ip)
            neighbors = client.get_neighbors()

            if neighbors:
                print(f"Found {len(neighbors)} neighbor(s):\n")
                for link in neighbors:
                    snr_str = f"SNR:{link.snr}dB" if link.snr else ""
                    print(f"  {link.link_type.value:4s} {link.hostname:<30s} {snr_str}")
                    if link.signal:
                        print(f"       Signal:{link.signal} Noise:{link.noise} Rate:{link.tx_rate}Mbps")
            else:
                print("No neighbors found.")
                print("Check that your AREDN node has active RF links.")

        except ImportError:
            print("AREDN utilities not available.")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_services(self):
        """Show AREDN advertised services."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Services ===\n")

        try:
            from utils.aredn import AREDNClient

            node_ip = self._aredn_get_node_ip()
            if not node_ip:
                print("No AREDN node found.")
                self._wait_for_enter()
                return

            client = AREDNClient(node_ip)
            sysinfo = client.get_sysinfo(services=True)

            if sysinfo and 'services' in sysinfo:
                services = sysinfo['services']
                if services:
                    print(f"Found {len(services)} service(s):\n")
                    for svc in services:
                        name = svc.get('name', 'Unknown')
                        protocol = svc.get('protocol', '')
                        url = svc.get('url', '')
                        print(f"  {name} ({protocol})")
                        if url:
                            print(f"    {url}")
                else:
                    print("No services advertised.")
            else:
                print("Could not retrieve services.")

        except ImportError:
            print("AREDN utilities not available.")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_web(self):
        """Show AREDN web UI URL."""
        node_ip = self._aredn_get_node_ip()
        if node_ip:
            msg = (
                f"AREDN Node Web UI\n\n"
                f"  URL: http://{node_ip}:8080\n\n"
                f"Open in any browser on your network.\n\n"
                f"Provides: configuration, neighbor map,\n"
                f"  services, firmware updates"
            )
        else:
            msg = (
                "No AREDN node found on local network.\n\n"
                "Tried: localnode.local.mesh, 10.0.0.1\n\n"
                "Make sure your AREDN node is connected\n"
                "and accessible from this machine."
            )
        self.dialog.msgbox("AREDN Web UI", msg)

    def _aredn_scan(self):
        """Scan for AREDN nodes on network."""
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Network Scan ===\n")
        print("Scanning 10.0.0.0/24 for AREDN nodes...\n")

        try:
            from utils.aredn import AREDNScanner

            scanner = AREDNScanner()
            nodes = scanner.scan_subnet("10.0.0.0/24")

            if nodes:
                print(f"Found {len(nodes)} node(s):\n")
                for node in nodes:
                    print(f"  {node.hostname:<30s} {node.ip:<15s} {node.model}")
            else:
                print("No AREDN nodes found on 10.0.0.0/24")
                print("\nYour network may use a different subnet.")
                print("Check your AREDN node's IP configuration.")

        except ImportError:
            print("AREDN utilities not available.")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()

    def _aredn_map(self):
        """Show AREDN nodes on the unified network map.

        AREDN nodes are displayed alongside Meshtastic and RNS nodes
        on the MeshForge map. Nodes must have location configured.
        """
        subprocess.run(['clear'], check=False, timeout=5)
        print("=== AREDN Network Map ===\n")

        # Check for AREDN node
        node_ip = self._aredn_get_node_ip()
        if not node_ip:
            print("No AREDN node found on local network.\n")
            print("The map requires an AREDN node to be connected.")
            print("Tried: localnode.local.mesh, 10.0.0.1")
            self._wait_for_enter()
            return

        print(f"Connecting to AREDN node at {node_ip}...\n")

        try:
            from utils.aredn import get_aredn_node

            node = get_aredn_node(node_ip)
            if not node:
                print("Could not retrieve node information.")
                self._wait_for_enter()
                return

            # Show node info
            print(f"  Local Node: {node.hostname}")
            print(f"  Model:      {node.model}")

            # Check if node has location
            if node.has_location():
                print(f"  Location:   {node.latitude:.6f}, {node.longitude:.6f}")
                if node.grid_square:
                    print(f"  Grid:       {node.grid_square}")
            else:
                print(f"  Location:   Not configured")
                print("\n  Note: Configure location on your AREDN node")
                print("  to see it on the map (Setup > Basic Setup > Location)")

            # Show neighbor count
            print(f"\n  Neighbors:  {len(node.links)}")

            # Count neighbors with location
            neighbors_with_loc = 0
            print("\n  Checking neighbor locations...")
            for link in node.links[:5]:  # Check first 5 to avoid long waits
                if link.ip:
                    try:
                        from utils.aredn import get_aredn_node as get_neighbor
                        neighbor = get_neighbor(link.ip)
                        if neighbor and neighbor.has_location():
                            neighbors_with_loc += 1
                            print(f"    ✓ {neighbor.hostname} has location")
                    except Exception:
                        pass

            if len(node.links) > 5:
                print(f"    ... and {len(node.links) - 5} more neighbors")

            print(f"\n  AREDN nodes on map: {1 if node.has_location() else 0} + {neighbors_with_loc} neighbors")

            # Show map server info
            print("\n" + "=" * 50)
            print("\nAREDN nodes are included in the unified MeshForge map.")
            print("The map shows Meshtastic, RNS, and AREDN nodes together.")
            print("\nTo view the map:")
            print("  1. Main Menu > Maps & Viz > Coverage Map")
            print("  2. Or start the map server and open in browser")

            # Check if map server is running
            import socket
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                if sock.connect_ex(('localhost', 5000)) == 0:
                    print("\n  Map server is running: http://localhost:5000")
                sock.close()
            except Exception:
                pass

        except ImportError as e:
            print(f"AREDN utilities not available: {e}")
        except Exception as e:
            print(f"Error: {e}")

        self._wait_for_enter()
