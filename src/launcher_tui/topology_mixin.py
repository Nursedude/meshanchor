"""
Network Topology Mixin for MeshForge Launcher TUI.

Provides network topology visualization and analysis tools:
- View topology statistics
- Trace paths to destinations
- View recent topology events
- Generate browser-based visualization
- ASCII topology display
"""

import logging
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


class TopologyMixin:
    """Mixin providing network topology tools for the TUI launcher."""

    def _topology_menu(self):
        """Network topology analysis menu."""
        choices = [
            ("stats", "Topology Statistics"),
            ("nodes", "View Nodes"),
            ("edges", "View Links/Edges"),
            ("events", "Recent Topology Events"),
            ("trace", "Trace Path to Destination"),
            ("ascii", "ASCII Topology View"),
            ("browser", "Open in Browser (D3.js Graph)"),
            ("export", "Export Topology Data"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Network Topology",
                "Analyze mesh network topology:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "stats":
                self._show_topology_stats()
            elif choice == "nodes":
                self._show_topology_nodes()
            elif choice == "edges":
                self._show_topology_edges()
            elif choice == "events":
                self._show_topology_events()
            elif choice == "trace":
                self._trace_path()
            elif choice == "ascii":
                self._show_ascii_topology()
            elif choice == "browser":
                self._open_topology_browser()
            elif choice == "export":
                self._export_topology()

    def _get_topology(self):
        """Get the network topology instance."""
        try:
            from gateway.network_topology import get_network_topology
            return get_network_topology()
        except ImportError:
            return None

    def _get_node_tracker(self):
        """Get the node tracker instance if available."""
        try:
            from gateway.node_tracker import get_node_tracker
            return get_node_tracker()
        except ImportError:
            return None

    def _show_topology_stats(self):
        """Display topology statistics."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox(
                "Topology Unavailable",
                "Network topology module not loaded.\n\n"
                "The gateway service may need to be running."
            )
            return

        try:
            stats = topology.get_topology_stats()

            # Format stats display
            lines = [
                "NETWORK TOPOLOGY STATISTICS",
                "=" * 40,
                "",
                f"Total Nodes:    {stats.get('node_count', 0)}",
                f"Total Edges:    {stats.get('edge_count', 0)}",
                f"Active Edges:   {stats.get('active_edges', 0)}",
                "",
                f"Average Hops:   {stats.get('avg_hops', 0):.2f}",
                f"Maximum Hops:   {stats.get('max_hops', 0)}",
                "",
            ]

            # Add service stats if available from node tracker
            tracker = self._get_node_tracker()
            if tracker and hasattr(tracker, 'get_service_stats'):
                try:
                    svc_stats = tracker.get_service_stats()
                    if svc_stats:
                        lines.append("SERVICE DISCOVERY")
                        lines.append("-" * 40)
                        for svc_name, count in sorted(svc_stats.items()):
                            lines.append(f"  {svc_name}: {count}")
                        lines.append("")
                except Exception:
                    pass

            self.dialog.msgbox("Topology Statistics", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get topology stats:\n{e}")

    def _show_topology_nodes(self):
        """Show list of nodes in the topology."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        try:
            topo_dict = topology.to_dict()
            nodes = topo_dict.get("nodes", [])

            if not nodes:
                self.dialog.msgbox("No Nodes", "No nodes discovered in the topology yet.")
                return

            # Build node list for menu
            node_choices = []
            for node_id in sorted(nodes)[:50]:  # Limit to 50 for TUI
                # Truncate long IDs
                display_id = node_id[:30] if len(node_id) > 30 else node_id

                # Determine node type
                if node_id == "local":
                    desc = "Local Node"
                elif node_id.startswith("rns_"):
                    desc = "RNS Destination"
                elif node_id.startswith("mesh_") or node_id.startswith("!"):
                    desc = "Meshtastic Node"
                else:
                    desc = "Network Node"

                node_choices.append((node_id, f"{display_id} [{desc}]"))

            node_choices.append(("back", "Back"))

            selected = self.dialog.menu(
                "Network Nodes",
                f"Found {len(nodes)} nodes:",
                node_choices
            )

            if selected and selected != "back":
                self._show_node_details(selected)

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to list nodes:\n{e}")

    def _show_node_details(self, node_id: str):
        """Show details for a specific node."""
        topology = self._get_topology()

        if topology is None:
            return

        lines = [
            f"NODE: {node_id}",
            "=" * 50,
            "",
        ]

        # Get edges involving this node
        try:
            topo_dict = topology.to_dict()
            edges = topo_dict.get("edges", [])

            node_edges = [e for e in edges
                          if e.get("source_id") == node_id or e.get("dest_id") == node_id]

            if node_edges:
                lines.append("CONNECTIONS:")
                lines.append("-" * 50)

                for edge in node_edges:
                    src = edge.get("source_id", "")[:15]
                    dst = edge.get("dest_id", "")[:15]
                    hops = edge.get("hops", 0)
                    active = "Active" if edge.get("is_active", False) else "Inactive"
                    snr = edge.get("snr")
                    snr_str = f"{snr:.1f}dB" if snr else "N/A"

                    lines.append(f"  {src} → {dst}")
                    lines.append(f"    Hops: {hops} | SNR: {snr_str} | {active}")
                    lines.append("")
            else:
                lines.append("No direct connections found.")

        except Exception as e:
            lines.append(f"Error loading details: {e}")

        self.dialog.msgbox("Node Details", "\n".join(lines))

    def _show_topology_edges(self):
        """Show list of edges/links in the topology."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        try:
            topo_dict = topology.to_dict()
            edges = topo_dict.get("edges", [])

            if not edges:
                self.dialog.msgbox("No Edges", "No links discovered in the topology yet.")
                return

            # Sort by announce count (most active first)
            edges_sorted = sorted(edges, key=lambda e: e.get("announce_count", 0), reverse=True)

            lines = [
                "NETWORK LINKS",
                "=" * 70,
                f"Total: {len(edges)} links",
                "",
                "Most Active Links:",
                "-" * 70,
            ]

            for i, edge in enumerate(edges_sorted[:20]):
                src = edge.get("source_id", "")[:12]
                dst = edge.get("dest_id", "")[:12]
                hops = edge.get("hops", 0)
                count = edge.get("announce_count", 0)
                active = "●" if edge.get("is_active", False) else "○"

                snr = edge.get("snr")
                if snr is not None:
                    if snr > 10:
                        quality = "Excellent"
                    elif snr > 5:
                        quality = "Good"
                    elif snr > 0:
                        quality = "Marginal"
                    elif snr > -5:
                        quality = "Poor"
                    else:
                        quality = "Bad"
                    snr_str = f"{snr:.1f}dB ({quality})"
                else:
                    snr_str = "N/A"

                lines.append(f"{active} {src} → {dst}")
                lines.append(f"    Hops: {hops} | Announces: {count} | SNR: {snr_str}")

            if len(edges) > 20:
                lines.append("")
                lines.append(f"... and {len(edges) - 20} more links")

            self.dialog.msgbox("Network Links", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to list edges:\n{e}")

    def _show_topology_events(self):
        """Show recent topology events."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        try:
            events = topology.get_recent_events(30)

            if not events:
                self.dialog.msgbox("No Events", "No topology events recorded yet.")
                return

            lines = [
                "RECENT TOPOLOGY EVENTS",
                "=" * 60,
                "",
            ]

            for event in reversed(events[-20:]):
                event_type = event.get("event_type", "UNKNOWN")
                timestamp = event.get("timestamp", "")[:19]  # Trim microseconds
                node_id = event.get("node_id", "")
                dest_hash = event.get("dest_hash", "")[:16] if event.get("dest_hash") else ""

                # Format based on event type
                if event_type == "PATH_DISCOVERED":
                    lines.append(f"[{timestamp}] + PATH: {dest_hash}")
                    if event.get("new_value") is not None:
                        lines.append(f"    Hops: {event.get('new_value')}")
                elif event_type == "PATH_LOST":
                    lines.append(f"[{timestamp}] - PATH: {dest_hash}")
                elif event_type == "HOP_COUNT_CHANGED":
                    old = event.get("old_value", "?")
                    new = event.get("new_value", "?")
                    lines.append(f"[{timestamp}] ~ HOPS: {dest_hash}")
                    lines.append(f"    {old} → {new} hops")
                elif event_type == "NODE_ADDED":
                    lines.append(f"[{timestamp}] + NODE: {node_id}")
                elif event_type == "NODE_REMOVED":
                    lines.append(f"[{timestamp}] - NODE: {node_id}")
                elif event_type == "EDGE_ADDED":
                    lines.append(f"[{timestamp}] + EDGE: {node_id}")
                elif event_type == "EDGE_UPDATED":
                    lines.append(f"[{timestamp}] ~ EDGE: {node_id}")
                else:
                    lines.append(f"[{timestamp}] {event_type}")

                lines.append("")

            self.dialog.msgbox("Topology Events", "\n".join(lines))

        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to get events:\n{e}")

    def _trace_path(self):
        """Trace path to a destination."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        # Get destination hash from user
        dest_input = self.dialog.inputbox(
            "Trace Path",
            "Enter destination hash (hex) or node ID:",
            ""
        )

        if not dest_input:
            return

        try:
            # Try to interpret as hex hash
            dest_hash = None
            if len(dest_input) >= 16:
                try:
                    # Clean hex string
                    clean_hex = dest_input.replace(" ", "").replace(":", "")
                    dest_hash = bytes.fromhex(clean_hex[:32])
                except ValueError:
                    pass

            if dest_hash:
                result = topology.trace_path(dest_hash)

                lines = [
                    "PATH TRACE RESULT",
                    "=" * 50,
                    "",
                    f"Destination: {result.get('dest_hash', 'N/A')[:32]}",
                    f"Node ID: {result.get('dest_id', 'N/A')}",
                    "",
                ]

                if result.get("found"):
                    lines.append("Status: PATH FOUND")
                    lines.append(f"Total Hops: {result.get('total_hops', 0)}")
                    lines.append("")
                    lines.append("Path:")
                    for i, node in enumerate(result.get("path", [])):
                        lines.append(f"  {i+1}. {node}")

                    details = result.get("details", {})
                    if details:
                        lines.append("")
                        lines.append("Details:")
                        if details.get("interface"):
                            lines.append(f"  Interface: {details['interface']}")
                        if details.get("last_seen"):
                            lines.append(f"  Last Seen: {details['last_seen']}")
                        if details.get("announce_count"):
                            lines.append(f"  Announces: {details['announce_count']}")
                else:
                    lines.append("Status: NO PATH FOUND")
                    lines.append("")
                    lines.append("The destination may be unreachable or")
                    lines.append("not yet discovered in the topology.")

                self.dialog.msgbox("Path Trace", "\n".join(lines))

            else:
                # Try as node ID
                topo_dict = topology.to_dict()
                nodes = topo_dict.get("nodes", [])

                if dest_input in nodes:
                    # Find path from local to this node
                    path = topology.find_path("local", dest_input)

                    if path:
                        lines = [
                            "PATH TRACE RESULT",
                            "=" * 50,
                            "",
                            f"Destination: {dest_input}",
                            "",
                            "Status: PATH FOUND",
                            f"Hops: {len(path) - 1}",
                            "",
                            "Path:",
                        ]
                        for i, node in enumerate(path):
                            lines.append(f"  {i+1}. {node}")

                        self.dialog.msgbox("Path Trace", "\n".join(lines))
                    else:
                        self.dialog.msgbox(
                            "No Path",
                            f"No path found to {dest_input}"
                        )
                else:
                    self.dialog.msgbox(
                        "Not Found",
                        f"Node '{dest_input}' not found in topology.\n\n"
                        "Try entering a valid destination hash (hex) or node ID."
                    )

        except Exception as e:
            self.dialog.msgbox("Error", f"Path trace failed:\n{e}")

    def _show_ascii_topology(self):
        """Show ASCII representation of topology."""
        try:
            from utils.topology_visualizer import TopologyVisualizer

            topology = self._get_topology()
            if topology is None:
                self.dialog.msgbox("Unavailable", "Topology module not loaded.")
                return

            visualizer = TopologyVisualizer.from_topology(topology)
            ascii_output = visualizer.generate_ascii(max_width=70)

            self.dialog.msgbox("Network Topology (ASCII)", ascii_output)

        except ImportError:
            self.dialog.msgbox(
                "Module Not Found",
                "Topology visualizer module not available."
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to generate ASCII view:\n{e}")

    def _open_topology_browser(self):
        """Generate and open topology visualization in browser."""
        self.dialog.infobox("Generating...", "Creating topology visualization...")

        try:
            from utils.topology_visualizer import TopologyVisualizer

            topology = self._get_topology()
            if topology is None:
                self.dialog.msgbox("Unavailable", "Topology module not loaded.")
                return

            # Generate visualization
            visualizer = TopologyVisualizer.from_topology(topology)
            output_path = visualizer.generate()

            # Try to open in browser (in background thread to not block)
            def open_browser():
                try:
                    # Try xdg-open first (Linux)
                    result = subprocess.run(
                        ["xdg-open", output_path],
                        capture_output=True,
                        timeout=10
                    )
                    if result.returncode != 0:
                        # Fallback to webbrowser module
                        webbrowser.open(f"file://{output_path}")
                except Exception:
                    try:
                        webbrowser.open(f"file://{output_path}")
                    except Exception:
                        pass

            threading.Thread(target=open_browser, daemon=True).start()

            self.dialog.msgbox(
                "Topology Visualization",
                f"Visualization generated and opened in browser.\n\n"
                f"File: {output_path}\n\n"
                f"If the browser didn't open automatically,\n"
                f"you can open this file manually."
            )

        except ImportError:
            self.dialog.msgbox(
                "Module Not Found",
                "Topology visualizer module not available."
            )
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to generate visualization:\n{e}")

    def _export_topology(self):
        """Export topology data to file."""
        topology = self._get_topology()

        if topology is None:
            self.dialog.msgbox("Unavailable", "Topology module not loaded.")
            return

        # Ask for export format
        format_choices = [
            ("geojson", "GeoJSON (for mapping tools)"),
            ("d3", "D3.js JSON (for web visualization)"),
            ("graphml", "GraphML (for Gephi, etc.)"),
            ("csv", "CSV (nodes + edges)"),
            ("json", "JSON (full topology data)"),
            ("back", "Back"),
        ]

        export_format = self.dialog.menu(
            "Export Format",
            "Select export format:",
            format_choices
        )

        if not export_format or export_format == "back":
            return

        try:
            from utils.topology_visualizer import TopologyVisualizer

            # Create visualizer from topology
            visualizer = TopologyVisualizer.from_topology(topology)

            if export_format == "geojson":
                output_path, count = visualizer.export_geojson()
                self.dialog.msgbox(
                    "GeoJSON Export",
                    f"Exported {count} features.\n\n"
                    f"File: {output_path}\n\n"
                    "Note: Only nodes with GPS positions are included."
                )

            elif export_format == "d3":
                output_path, count = visualizer.export_d3_json()
                self.dialog.msgbox(
                    "D3.js Export",
                    f"Exported {count} nodes + links.\n\n"
                    f"File: {output_path}\n\n"
                    "Use with D3.js force-directed graph."
                )

            elif export_format == "graphml":
                output_path, count = visualizer.export_graphml()
                self.dialog.msgbox(
                    "GraphML Export",
                    f"Exported {count} edges.\n\n"
                    f"File: {output_path}\n\n"
                    "Open in Gephi, yEd, or similar tools."
                )

            elif export_format == "csv":
                nodes_path, edges_path = visualizer.export_csv()
                self.dialog.msgbox(
                    "CSV Export",
                    f"Exported CSV files:\n\n"
                    f"Nodes: {nodes_path}\n"
                    f"Edges: {edges_path}"
                )

            elif export_format == "json":
                import json
                from utils.paths import get_real_user_home
                export_dir = get_real_user_home() / ".cache" / "meshforge"
                export_dir.mkdir(parents=True, exist_ok=True)

                topo_dict = topology.to_dict()
                output_path = export_dir / "topology_export.json"
                with open(output_path, 'w') as f:
                    json.dump(topo_dict, f, indent=2, default=str)

                self.dialog.msgbox(
                    "JSON Export",
                    f"Exported full topology data.\n\n"
                    f"File: {output_path}"
                )

        except ImportError:
            self.dialog.msgbox("Error", "Topology visualizer module not available.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Export failed:\n{e}")
