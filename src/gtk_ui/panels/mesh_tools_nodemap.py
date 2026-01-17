"""
Node Map Tab Mixin - Extracted from mesh_tools.py

Handles node mapping including:
- Node discovery via meshtasticd TCP
- Node database reading from mesh_bot
- GeoJSON export
- Map display in browser
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import threading
import subprocess
import os
import json
from pathlib import Path
from datetime import datetime

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import path utilities
from utils.paths import get_real_user_home

# Import meshtastic connection utilities
try:
    from utils.meshtastic_connection import (
        MESHTASTIC_CONNECTION_LOCK,
        wait_for_cooldown,
        safe_close_interface
    )
    HAS_MESHTASTIC_LOCK = True
except ImportError:
    HAS_MESHTASTIC_LOCK = False
    MESHTASTIC_CONNECTION_LOCK = None


class NodeMapTabMixin:
    """
    Mixin providing Node Map tab functionality.

    Requires parent class to provide:
    - self._notebook: Gtk.Notebook to add tab to
    - self._path_entry: Entry widget with meshbot path
    - self._log_message(str): Method to log messages
    - self._open_url(str): Method to open URL
    """

    def _add_map_tab(self):
        """Add Node Map tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Map controls
        controls_frame = Gtk.Frame()
        controls_frame.set_label("Node Map Controls")
        controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        controls_box.set_margin_start(15)
        controls_box.set_margin_end(15)
        controls_box.set_margin_top(10)
        controls_box.set_margin_bottom(10)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        refresh_map_btn = Gtk.Button(label="Refresh Nodes")
        refresh_map_btn.add_css_class("suggested-action")
        refresh_map_btn.connect("clicked", self._on_refresh_map)
        btn_row.append(refresh_map_btn)

        open_map_btn = Gtk.Button(label="Open Full Map")
        open_map_btn.connect("clicked", self._on_open_full_map)
        open_map_btn.set_tooltip_text("Open node map in browser")
        btn_row.append(open_map_btn)

        export_btn = Gtk.Button(label="Export GeoJSON")
        export_btn.connect("clicked", self._on_export_geojson)
        btn_row.append(export_btn)

        controls_box.append(btn_row)
        controls_frame.set_child(controls_box)
        box.append(controls_frame)

        # Node list
        nodes_frame = Gtk.Frame()
        nodes_frame.set_label("Discovered Nodes")
        nodes_frame.set_vexpand(True)
        nodes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        nodes_box.set_margin_start(15)
        nodes_box.set_margin_end(15)
        nodes_box.set_margin_top(10)
        nodes_box.set_margin_bottom(10)

        # Node list store
        self._node_store = Gtk.ListStore(str, str, str, str)  # ID, Name, Type, Last Seen

        self._node_tree = Gtk.TreeView(model=self._node_store)
        self._node_tree.set_headers_visible(True)

        for i, title in enumerate(["Node ID", "Name", "Type", "Last Seen"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=i)
            column.set_resizable(True)
            column.set_min_width(80)
            self._node_tree.append_column(column)

        node_scroll = Gtk.ScrolledWindow()
        node_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        node_scroll.set_min_content_height(150)
        node_scroll.set_vexpand(True)
        node_scroll.set_child(self._node_tree)

        nodes_box.append(node_scroll)

        # Stats row
        self._node_stats_label = Gtk.Label(label="Nodes: 0 | Last update: Never")
        self._node_stats_label.set_xalign(0)
        self._node_stats_label.add_css_class("dim-label")
        nodes_box.append(self._node_stats_label)

        nodes_frame.set_child(nodes_box)
        box.append(nodes_frame)

        scrolled.set_child(box)

        # Tab label
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("mark-location-symbolic"))
        tab_box.append(Gtk.Label(label="Node Map"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # Node Map Handlers
    # =========================================================================

    def _on_refresh_map(self, button):
        """Refresh node list from meshtasticd or mesh_bot data"""
        self._log_message("Refreshing node list...")

        def fetch_nodes():
            import socket
            nodes = []

            # Method 1: Check if meshtasticd TCP port is available first
            tcp_available = False
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('localhost', 4403))
                tcp_available = (result == 0)
            except Exception:
                pass
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            if tcp_available:
                # Acquire global lock - meshtasticd only supports one TCP connection
                lock_acquired = False
                if HAS_MESHTASTIC_LOCK and MESHTASTIC_CONNECTION_LOCK:
                    lock_acquired = MESHTASTIC_CONNECTION_LOCK.acquire(timeout=5.0)
                    if not lock_acquired:
                        GLib.idle_add(self._log_message, "Could not acquire connection lock (another operation in progress)")
                    else:
                        wait_for_cooldown()
                else:
                    lock_acquired = True

                if lock_acquired:
                    interface = None
                    try:
                        import meshtastic.tcp_interface
                        import time
                        GLib.idle_add(self._log_message, "Connecting to meshtasticd via TCP...")

                        interface = meshtastic.tcp_interface.TCPInterface('localhost', 4403)
                        time.sleep(2)

                        if hasattr(interface, 'nodes') and interface.nodes:
                            for node_id, node in interface.nodes.items():
                                user = node.get('user', {})
                                name = user.get('longName', user.get('shortName', 'Unknown'))
                                hw_model = user.get('hwModel', 'Unknown')

                                last_heard = node.get('lastHeard', 0)
                                if last_heard:
                                    last_seen = datetime.fromtimestamp(last_heard).strftime('%H:%M:%S')
                                else:
                                    last_seen = 'Never'

                                nodes.append((str(node_id), name, hw_model, last_seen))

                        if nodes:
                            GLib.idle_add(self._log_message, f"Found {len(nodes)} nodes via meshtastic TCP")

                    except ImportError:
                        GLib.idle_add(self._log_message, "Meshtastic Python library not installed")
                        GLib.idle_add(self._log_message, "Install with: pip install meshtastic")
                    except (SystemExit, KeyboardInterrupt, GeneratorExit):
                        raise
                    except BaseException as e:
                        GLib.idle_add(self._log_message, f"Meshtastic error: {e}")
                    finally:
                        if interface:
                            if HAS_MESHTASTIC_LOCK:
                                safe_close_interface(interface)
                            else:
                                try:
                                    interface.close()
                                except Exception:
                                    pass
                        if HAS_MESHTASTIC_LOCK and MESHTASTIC_CONNECTION_LOCK:
                            try:
                                MESHTASTIC_CONNECTION_LOCK.release()
                            except RuntimeError:
                                pass
            else:
                GLib.idle_add(self._log_message, "meshtasticd TCP port 4403 not available")

            # Method 2: Try reading mesh_bot data files
            if not nodes:
                meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
                data_files = [
                    Path(meshbot_path) / "prior_node_db.json",
                    Path(meshbot_path) / "node_db.json",
                    Path(meshbot_path) / "data" / "nodes.json",
                ]

                found_file = False
                for data_file in data_files:
                    if data_file.exists():
                        found_file = True
                        try:
                            with open(data_file) as f:
                                data = json.load(f)

                            if isinstance(data, dict):
                                for node_id, node in data.items():
                                    if isinstance(node, dict):
                                        name = node.get('longName', node.get('shortName', node.get('name', 'Unknown')))
                                        hw = node.get('hwModel', node.get('type', 'Unknown'))
                                        last = node.get('lastHeard', node.get('last_seen', ''))
                                        if isinstance(last, (int, float)) and last > 0:
                                            last = datetime.fromtimestamp(last).strftime('%H:%M:%S')
                                        nodes.append((str(node_id), name, hw, str(last) if last else 'Unknown'))

                            if nodes:
                                GLib.idle_add(self._log_message, f"Loaded {len(nodes)} nodes from {data_file.name}")
                            break
                        except Exception as e:
                            GLib.idle_add(self._log_message, f"Error reading {data_file.name}: {e}")

                if not found_file:
                    GLib.idle_add(self._log_message, "No mesh_bot node database files found")

            # Show help if no nodes found
            if not nodes:
                GLib.idle_add(self._log_message, "")
                GLib.idle_add(self._log_message, "=== No nodes found ===")
                GLib.idle_add(self._log_message, "To get node data:")
                GLib.idle_add(self._log_message, "  1. Start meshtasticd (systemctl start meshtasticd)")
                GLib.idle_add(self._log_message, "  2. Install meshtastic: pip install meshtastic")
                GLib.idle_add(self._log_message, "  3. Or run MeshBot to create node database")

            # Update UI
            GLib.idle_add(self._update_node_list, nodes)

        threading.Thread(target=fetch_nodes, daemon=True).start()

    def _update_node_list(self, nodes: list):
        """Update the node list store with new data"""
        self._node_store.clear()

        for node_id, name, hw_model, last_seen in nodes:
            self._node_store.append([node_id, name, hw_model, last_seen])

        count = len(nodes)
        self._node_stats_label.set_label(f"Nodes: {count} | Last update: {datetime.now().strftime('%H:%M:%S')}")

    def _on_open_full_map(self, button):
        """Open map view - check which ports are available first"""
        import socket

        map_options = [
            ("https://localhost:9443", 9443, "Meshtastic Web"),
            ("http://localhost:8080", 8080, "Meshtastic Alt"),
            ("http://localhost:5000", 5000, "Flask/MeshBot"),
            ("http://localhost:8000", 8000, "MeshMap"),
        ]

        self._log_message("Checking available map servers...")
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

        for url, port, name in map_options:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))

                if result == 0:
                    self._log_message(f"Found {name} on port {port}")
                    subprocess.Popen(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    self._log_message(f"Opening {url}")
                    return
            except Exception:
                continue
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

        self._log_message("No map server found on common ports (9443, 8080, 5000, 8000)")
        self._log_message("Make sure meshtasticd or meshbot web is running")

    def _on_export_geojson(self, button):
        """Export nodes as GeoJSON"""
        self._log_message("Exporting GeoJSON...")

        def do_export():
            try:
                # Collect nodes from store
                nodes = []
                model = self._node_store
                iter = model.get_iter_first()
                while iter:
                    node_id = model.get_value(iter, 0)
                    name = model.get_value(iter, 1)
                    hw_model = model.get_value(iter, 2)
                    last_seen = model.get_value(iter, 3)
                    nodes.append({
                        'id': node_id,
                        'name': name,
                        'hw_model': hw_model,
                        'last_seen': last_seen
                    })
                    iter = model.iter_next(iter)

                if not nodes:
                    GLib.idle_add(self._log_message, "No nodes to export. Refresh node list first.")
                    return

                # Try to get coordinates from meshtasticd or mesh_bot data
                meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
                node_coords = {}

                data_files = [
                    Path(meshbot_path) / "prior_node_db.json",
                    Path(meshbot_path) / "node_db.json",
                ]
                for data_file in data_files:
                    if data_file.exists():
                        try:
                            with open(data_file) as f:
                                data = json.load(f)
                            for nid, node in data.items():
                                if isinstance(node, dict):
                                    pos = node.get('position', {})
                                    lat = pos.get('latitude') or pos.get('lat')
                                    lon = pos.get('longitude') or pos.get('lon')
                                    if lat and lon:
                                        node_coords[str(nid)] = (lon, lat)
                            break
                        except Exception:
                            pass

                # Build GeoJSON
                features = []
                for node in nodes:
                    coords = node_coords.get(node['id'])
                    if coords:
                        feature = {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": list(coords)
                            },
                            "properties": {
                                "id": node['id'],
                                "name": node['name'],
                                "hw_model": node['hw_model'],
                                "last_seen": node['last_seen']
                            }
                        }
                    else:
                        feature = {
                            "type": "Feature",
                            "geometry": None,
                            "properties": {
                                "id": node['id'],
                                "name": node['name'],
                                "hw_model": node['hw_model'],
                                "last_seen": node['last_seen'],
                                "note": "No coordinates available"
                            }
                        }
                    features.append(feature)

                geojson = {
                    "type": "FeatureCollection",
                    "features": features,
                    "properties": {
                        "exported": datetime.now().isoformat(),
                        "source": "MeshForge"
                    }
                }

                # Save to file
                export_dir = get_real_user_home() / ".local" / "share" / "meshforge" / "exports"
                export_dir.mkdir(parents=True, exist_ok=True)
                filename = f"nodes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.geojson"
                export_path = export_dir / filename

                with open(export_path, 'w') as f:
                    json.dump(geojson, f, indent=2)

                nodes_with_coords = sum(1 for f in features if f['geometry'])
                GLib.idle_add(self._log_message, f"Exported {len(nodes)} nodes ({nodes_with_coords} with coordinates)")
                GLib.idle_add(self._log_message, f"Saved to: {export_path}")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Export error: {e}")

        threading.Thread(target=do_export, daemon=True).start()
