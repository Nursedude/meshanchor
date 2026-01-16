"""
Coverage Map Generator for MeshForge.

Generates interactive Folium-based maps showing:
- Node locations with status indicators
- Coverage estimation circles
- Network links/paths
- Terrain analysis overlays

Output: Self-contained HTML files viewable in any browser.

Usage:
    from utils.coverage_map import CoverageMapGenerator

    generator = CoverageMapGenerator()
    generator.add_nodes(nodes)
    generator.generate("coverage_map.html")
"""

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# Import centralized path utility for sudo compatibility
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


@dataclass
class MapNode:
    """Node for mapping with required fields."""
    id: str
    name: str
    latitude: float
    longitude: float
    network: str = "meshtastic"  # meshtastic, rns
    is_online: bool = False
    is_gateway: bool = False
    via_mqtt: bool = False
    snr: Optional[float] = None
    rssi: Optional[int] = None
    battery: Optional[int] = None
    altitude: Optional[float] = None
    last_seen: str = ""
    hardware: str = ""
    role: str = ""


class CoverageMapGenerator:
    """
    Interactive coverage map generator using Folium.

    Features:
    - Node markers with popup info
    - Coverage radius estimation
    - Heatmaps for signal density
    - Multiple tile layers (OSM, satellite, terrain)
    - Export to standalone HTML
    """

    # Estimated coverage radius by LoRa preset (meters)
    PRESET_RANGES = {
        "LONG_FAST": 10000,      # ~10km typical
        "LONG_SLOW": 20000,      # ~20km
        "MEDIUM_FAST": 5000,     # ~5km
        "MEDIUM_SLOW": 8000,     # ~8km
        "SHORT_FAST": 2000,      # ~2km
        "SHORT_SLOW": 3000,      # ~3km
        "SHORT_TURBO": 1000,     # ~1km
        "DEFAULT": 5000,         # Default assumption
    }

    def __init__(self, lora_preset: str = "DEFAULT"):
        """
        Initialize the map generator.

        Args:
            lora_preset: LoRa preset for coverage estimation
        """
        self._nodes: List[MapNode] = []
        self._links: List[Tuple[str, str, Dict]] = []  # (from_id, to_id, props)
        self._lora_preset = lora_preset
        self._coverage_radius = self.PRESET_RANGES.get(lora_preset, 5000)

    def add_node(self, node: MapNode) -> None:
        """Add a single node to the map."""
        self._nodes.append(node)

    def add_nodes(self, nodes: List[MapNode]) -> None:
        """Add multiple nodes to the map."""
        self._nodes.extend(nodes)

    def add_nodes_from_geojson(self, geojson: Dict) -> None:
        """Add nodes from GeoJSON FeatureCollection."""
        for feature in geojson.get("features", []):
            props = feature.get("properties", {})
            geom = feature.get("geometry", {})
            coords = geom.get("coordinates", [0, 0])

            node = MapNode(
                id=props.get("id", ""),
                name=props.get("name", "Unknown"),
                longitude=coords[0],
                latitude=coords[1],
                network=props.get("network", "meshtastic"),
                is_online=props.get("is_online", False),
                is_gateway=props.get("is_gateway", False),
                via_mqtt=props.get("via_mqtt", False),
                snr=props.get("snr"),
                rssi=props.get("rssi"),
                battery=props.get("battery"),
                last_seen=props.get("last_seen", ""),
                hardware=props.get("hardware", ""),
                role=props.get("role", ""),
            )
            self._nodes.append(node)

    def add_link(self, from_id: str, to_id: str, **props) -> None:
        """Add a link between two nodes."""
        self._links.append((from_id, to_id, props))

    def set_coverage_radius(self, meters: int) -> None:
        """Set custom coverage radius in meters."""
        self._coverage_radius = meters

    def get_center(self) -> Tuple[float, float]:
        """Calculate map center from nodes."""
        if not self._nodes:
            # Default to center of continental US
            return (39.8283, -98.5795)

        lats = [n.latitude for n in self._nodes if n.latitude]
        lons = [n.longitude for n in self._nodes if n.longitude]

        if not lats or not lons:
            return (39.8283, -98.5795)

        return (sum(lats) / len(lats), sum(lons) / len(lons))

    def get_bounds(self) -> Optional[List[List[float]]]:
        """Get bounding box for all nodes."""
        if not self._nodes:
            return None

        lats = [n.latitude for n in self._nodes if n.latitude]
        lons = [n.longitude for n in self._nodes if n.longitude]

        if not lats or not lons:
            return None

        return [[min(lats), min(lons)], [max(lats), max(lons)]]

    def generate(self, output_path: str = None, show_coverage: bool = True,
                 show_links: bool = True, tile_layer: str = "OpenStreetMap") -> str:
        """
        Generate the coverage map HTML.

        Args:
            output_path: Output file path (default: ~/.cache/meshforge/coverage_map.html)
            show_coverage: Show coverage radius circles
            show_links: Show links between nodes
            tile_layer: Base tile layer

        Returns:
            Path to generated HTML file
        """
        try:
            import folium
            from folium.plugins import MarkerCluster, HeatMap
        except ImportError:
            logger.error("Folium not installed. Run: pip install folium")
            return self._generate_fallback(output_path)

        # Determine output path
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "coverage_map.html")

        # Create map centered on nodes
        center = self.get_center()
        m = folium.Map(
            location=center,
            zoom_start=10,
            tiles=tile_layer
        )

        # Add tile layers
        folium.TileLayer('OpenStreetMap', name='Street').add_to(m)
        folium.TileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Satellite'
        ).add_to(m)
        folium.TileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Terrain'
        ).add_to(m)

        # Create node groups
        online_group = folium.FeatureGroup(name='Online Nodes')
        offline_group = folium.FeatureGroup(name='Offline Nodes')
        gateway_group = folium.FeatureGroup(name='Gateways')
        coverage_group = folium.FeatureGroup(name='Coverage Areas', show=show_coverage)
        links_group = folium.FeatureGroup(name='Links', show=show_links)

        # Node lookup for links
        node_lookup = {n.id: n for n in self._nodes}

        # Add nodes
        for node in self._nodes:
            if not node.latitude or not node.longitude:
                continue

            # Create popup content
            popup_html = self._create_popup(node)

            # Determine marker style
            if node.is_gateway:
                icon = folium.Icon(color='purple', icon='tower-broadcast', prefix='fa')
                group = gateway_group
            elif node.is_online:
                icon = folium.Icon(color='green', icon='signal', prefix='fa')
                group = online_group
            else:
                icon = folium.Icon(color='gray', icon='circle', prefix='fa')
                group = offline_group

            # Special icon for MQTT nodes
            if node.via_mqtt:
                icon = folium.Icon(
                    color='blue' if node.is_online else 'lightgray',
                    icon='cloud',
                    prefix='fa'
                )

            # Add marker
            marker = folium.Marker(
                location=[node.latitude, node.longitude],
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=node.name,
                icon=icon
            )
            marker.add_to(group)

            # Add coverage circle
            if show_coverage and node.is_online:
                folium.Circle(
                    location=[node.latitude, node.longitude],
                    radius=self._coverage_radius,
                    color='green' if not node.is_gateway else 'purple',
                    fill=True,
                    fill_opacity=0.1,
                    weight=1,
                    popup=f"Coverage: ~{self._coverage_radius/1000:.1f}km"
                ).add_to(coverage_group)

        # Add links
        if show_links:
            for from_id, to_id, props in self._links:
                from_node = node_lookup.get(from_id)
                to_node = node_lookup.get(to_id)

                if from_node and to_node:
                    if (from_node.latitude and from_node.longitude and
                        to_node.latitude and to_node.longitude):
                        folium.PolyLine(
                            locations=[
                                [from_node.latitude, from_node.longitude],
                                [to_node.latitude, to_node.longitude]
                            ],
                            color=props.get('color', 'blue'),
                            weight=props.get('weight', 2),
                            opacity=0.7,
                            popup=props.get('label', '')
                        ).add_to(links_group)

        # Add groups to map
        online_group.add_to(m)
        offline_group.add_to(m)
        gateway_group.add_to(m)
        coverage_group.add_to(m)
        links_group.add_to(m)

        # Add layer control
        folium.LayerControl().add_to(m)

        # Add stats box
        stats_html = self._create_stats_html()
        m.get_root().html.add_child(folium.Element(stats_html))

        # Fit bounds if we have nodes
        bounds = self.get_bounds()
        if bounds:
            m.fit_bounds(bounds, padding=[50, 50])

        # Save map
        m.save(output_path)
        logger.info(f"Coverage map saved to: {output_path}")

        return output_path

    def _create_popup(self, node: MapNode) -> str:
        """Create HTML popup content for a node."""
        status = "Online" if node.is_online else "Offline"
        status_color = "green" if node.is_online else "gray"

        html = f"""
        <div style="font-family: sans-serif; min-width: 200px;">
            <h4 style="margin: 0 0 8px 0;">{node.name}</h4>
            <div style="color: {status_color}; font-weight: bold; margin-bottom: 8px;">
                ● {status}
            </div>
            <table style="font-size: 12px; border-collapse: collapse;">
                <tr><td><b>ID:</b></td><td>{node.id}</td></tr>
                <tr><td><b>Network:</b></td><td>{node.network.upper()}</td></tr>
        """

        if node.hardware:
            html += f'<tr><td><b>Hardware:</b></td><td>{node.hardware}</td></tr>'
        if node.role:
            html += f'<tr><td><b>Role:</b></td><td>{node.role}</td></tr>'
        if node.snr is not None:
            html += f'<tr><td><b>SNR:</b></td><td>{node.snr:.1f} dB</td></tr>'
        if node.rssi is not None:
            html += f'<tr><td><b>RSSI:</b></td><td>{node.rssi} dBm</td></tr>'
        if node.battery is not None:
            html += f'<tr><td><b>Battery:</b></td><td>{node.battery}%</td></tr>'
        if node.altitude is not None:
            html += f'<tr><td><b>Altitude:</b></td><td>{node.altitude:.0f}m</td></tr>'
        if node.last_seen:
            html += f'<tr><td><b>Last seen:</b></td><td>{node.last_seen}</td></tr>'
        if node.via_mqtt:
            html += '<tr><td><b>Via:</b></td><td>MQTT</td></tr>'

        html += """
            </table>
            <div style="margin-top: 8px; font-size: 11px; color: #666;">
                Lat: {:.6f}, Lon: {:.6f}
            </div>
        </div>
        """.format(node.latitude, node.longitude)

        return html

    def _create_stats_html(self) -> str:
        """Create HTML for stats overlay."""
        total = len(self._nodes)
        online = len([n for n in self._nodes if n.is_online])
        with_pos = len([n for n in self._nodes if n.latitude and n.longitude])
        gateways = len([n for n in self._nodes if n.is_gateway])
        via_mqtt = len([n for n in self._nodes if n.via_mqtt])

        return f"""
        <div style="
            position: fixed;
            bottom: 30px;
            left: 10px;
            background: white;
            padding: 10px 15px;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
            font-family: sans-serif;
            font-size: 13px;
            z-index: 1000;
        ">
            <div style="font-weight: bold; margin-bottom: 5px;">MeshForge Network</div>
            <div>Total: {total} nodes</div>
            <div style="color: green;">Online: {online}</div>
            <div>Mapped: {with_pos}</div>
            <div style="color: purple;">Gateways: {gateways}</div>
            <div style="color: blue;">Via MQTT: {via_mqtt}</div>
            <div style="font-size: 11px; color: #888; margin-top: 5px;">
                Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
            </div>
        </div>
        """

    def _generate_fallback(self, output_path: str = None) -> str:
        """Generate simple HTML map without Folium."""
        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "coverage_map.html")

        center = self.get_center()
        nodes_json = json.dumps([{
            "id": n.id,
            "name": n.name,
            "lat": n.latitude,
            "lon": n.longitude,
            "online": n.is_online,
            "gateway": n.is_gateway,
            "mqtt": n.via_mqtt,
        } for n in self._nodes if n.latitude and n.longitude])

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>MeshForge Coverage Map</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ width: 100%; height: 100vh; }}
        .stats-box {{
            position: fixed;
            bottom: 30px;
            left: 10px;
            background: white;
            padding: 10px 15px;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.3);
            font-family: sans-serif;
            font-size: 13px;
            z-index: 1000;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="stats-box">
        <div style="font-weight: bold;">MeshForge Network</div>
        <div id="stats"></div>
    </div>
    <script>
        var nodes = {nodes_json};
        var map = L.map('map').setView([{center[0]}, {center[1]}], 10);

        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '© OpenStreetMap'
        }}).addTo(map);

        var online = 0, gateways = 0, mqtt = 0;
        nodes.forEach(function(node) {{
            var color = node.online ? 'green' : 'gray';
            if (node.gateway) color = 'purple';
            if (node.mqtt) color = 'blue';

            if (node.online) online++;
            if (node.gateway) gateways++;
            if (node.mqtt) mqtt++;

            L.circleMarker([node.lat, node.lon], {{
                radius: 8,
                fillColor: color,
                color: '#fff',
                weight: 2,
                opacity: 1,
                fillOpacity: 0.8
            }}).bindPopup('<b>' + node.name + '</b><br>ID: ' + node.id).addTo(map);
        }});

        document.getElementById('stats').innerHTML =
            'Total: ' + nodes.length + '<br>' +
            '<span style="color:green">Online: ' + online + '</span><br>' +
            '<span style="color:purple">Gateways: ' + gateways + '</span><br>' +
            '<span style="color:blue">Via MQTT: ' + mqtt + '</span>';

        if (nodes.length > 0) {{
            var bounds = nodes.map(n => [n.lat, n.lon]);
            map.fitBounds(bounds, {{padding: [50, 50]}});
        }}
    </script>
</body>
</html>"""

        with open(output_path, 'w') as f:
            f.write(html)

        logger.info(f"Fallback coverage map saved to: {output_path}")
        return output_path

    def generate_heatmap(self, output_path: str = None, radius: int = 25) -> str:
        """
        Generate a heatmap showing node density.

        Args:
            output_path: Output file path
            radius: Heatmap point radius

        Returns:
            Path to generated HTML file
        """
        try:
            import folium
            from folium.plugins import HeatMap
        except ImportError:
            logger.error("Folium not installed for heatmap")
            return ""

        if output_path is None:
            cache_dir = get_real_user_home() / ".cache" / "meshforge"
            cache_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(cache_dir / "coverage_heatmap.html")

        center = self.get_center()
        m = folium.Map(location=center, zoom_start=10)

        # Prepare heatmap data
        heat_data = [
            [n.latitude, n.longitude, 1.0 if n.is_online else 0.3]
            for n in self._nodes
            if n.latitude and n.longitude
        ]

        if heat_data:
            HeatMap(
                heat_data,
                radius=radius,
                blur=15,
                gradient={0.4: 'blue', 0.65: 'lime', 1: 'red'}
            ).add_to(m)

        folium.LayerControl().add_to(m)
        m.save(output_path)

        logger.info(f"Heatmap saved to: {output_path}")
        return output_path
