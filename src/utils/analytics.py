"""
Coverage Analytics and Link Budget History

Provides network analysis over time, tracking:
- Coverage area calculations
- Link budget history and trends
- Network health metrics
- Node connectivity patterns
"""

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Import path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    import os
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


@dataclass
class LinkBudgetSample:
    """Single link budget measurement."""
    timestamp: str
    source_node: str
    dest_node: str
    rssi_dbm: float
    snr_db: float
    distance_km: Optional[float]
    packet_loss_pct: float
    link_quality: str  # excellent, good, fair, bad


@dataclass
class CoverageStats:
    """Coverage area statistics."""
    total_nodes: int
    nodes_with_position: int
    bounding_box: Dict[str, float]  # min_lat, max_lat, min_lon, max_lon
    center_point: Tuple[float, float]
    estimated_area_km2: float
    average_node_spacing_km: float
    coverage_radius_km: float  # Estimated effective radius


@dataclass
class NetworkHealthMetrics:
    """Aggregate network health metrics."""
    timestamp: str
    online_nodes: int
    offline_nodes: int
    avg_rssi_dbm: float
    avg_snr_db: float
    avg_link_quality_pct: float
    packet_success_rate: float
    uptime_hours: float


class AnalyticsStore:
    """SQLite-based storage for analytics data."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = get_real_user_home() / ".config" / "meshforge" / "analytics.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()

                # Link budget history
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS link_budget_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        source_node TEXT NOT NULL,
                        dest_node TEXT NOT NULL,
                        rssi_dbm REAL,
                        snr_db REAL,
                        distance_km REAL,
                        packet_loss_pct REAL,
                        link_quality TEXT
                    )
                """)

                # Network health snapshots
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS network_health (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        online_nodes INTEGER,
                        offline_nodes INTEGER,
                        avg_rssi_dbm REAL,
                        avg_snr_db REAL,
                        avg_link_quality_pct REAL,
                        packet_success_rate REAL
                    )
                """)

                # Coverage snapshots
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS coverage_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        total_nodes INTEGER,
                        nodes_with_position INTEGER,
                        min_lat REAL,
                        max_lat REAL,
                        min_lon REAL,
                        max_lon REAL,
                        center_lat REAL,
                        center_lon REAL,
                        area_km2 REAL,
                        avg_spacing_km REAL,
                        coverage_radius_km REAL
                    )
                """)

                # Create indexes for efficient queries
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_link_budget_timestamp
                    ON link_budget_history(timestamp)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_link_budget_nodes
                    ON link_budget_history(source_node, dest_node)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_network_health_timestamp
                    ON network_health(timestamp)
                """)

                conn.commit()
            finally:
                conn.close()

    def record_link_budget(self, sample: LinkBudgetSample):
        """Record a link budget measurement."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO link_budget_history
                    (timestamp, source_node, dest_node, rssi_dbm, snr_db,
                     distance_km, packet_loss_pct, link_quality)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sample.timestamp, sample.source_node, sample.dest_node,
                    sample.rssi_dbm, sample.snr_db, sample.distance_km,
                    sample.packet_loss_pct, sample.link_quality
                ))
                conn.commit()
            finally:
                conn.close()

    def record_network_health(self, metrics: NetworkHealthMetrics):
        """Record network health snapshot."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO network_health
                    (timestamp, online_nodes, offline_nodes, avg_rssi_dbm,
                     avg_snr_db, avg_link_quality_pct, packet_success_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    metrics.timestamp, metrics.online_nodes, metrics.offline_nodes,
                    metrics.avg_rssi_dbm, metrics.avg_snr_db,
                    metrics.avg_link_quality_pct, metrics.packet_success_rate
                ))
                conn.commit()
            finally:
                conn.close()

    def record_coverage(self, stats: CoverageStats):
        """Record coverage snapshot."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO coverage_snapshots
                    (timestamp, total_nodes, nodes_with_position, min_lat, max_lat,
                     min_lon, max_lon, center_lat, center_lon, area_km2,
                     avg_spacing_km, coverage_radius_km)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    stats.total_nodes, stats.nodes_with_position,
                    stats.bounding_box.get('min_lat'),
                    stats.bounding_box.get('max_lat'),
                    stats.bounding_box.get('min_lon'),
                    stats.bounding_box.get('max_lon'),
                    stats.center_point[0], stats.center_point[1],
                    stats.estimated_area_km2, stats.average_node_spacing_km,
                    stats.coverage_radius_km
                ))
                conn.commit()
            finally:
                conn.close()

    def get_link_budget_history(
        self,
        source_node: Optional[str] = None,
        dest_node: Optional[str] = None,
        hours: int = 24
    ) -> List[LinkBudgetSample]:
        """Get link budget history for time period."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                query = "SELECT * FROM link_budget_history WHERE timestamp > ?"
                params = [cutoff]

                if source_node:
                    query += " AND source_node = ?"
                    params.append(source_node)
                if dest_node:
                    query += " AND dest_node = ?"
                    params.append(dest_node)

                query += " ORDER BY timestamp DESC"
                cursor.execute(query, params)
                rows = cursor.fetchall()

                samples = []
                for row in rows:
                    samples.append(LinkBudgetSample(
                        timestamp=row[1],
                        source_node=row[2],
                        dest_node=row[3],
                        rssi_dbm=row[4],
                        snr_db=row[5],
                        distance_km=row[6],
                        packet_loss_pct=row[7],
                        link_quality=row[8]
                    ))
                return samples
            finally:
                conn.close()

    def get_network_health_history(self, hours: int = 24) -> List[NetworkHealthMetrics]:
        """
        Get network health history for time period.

        Args:
            hours: Number of hours to look back (default 24)

        Returns:
            List of NetworkHealthMetrics objects

        API Contract:
            - ALWAYS returns a list (never None)
            - Empty list if no data in time period
            - Results ordered by timestamp descending (newest first)
            - Thread-safe (uses internal lock)
            - Tests: tests/test_analytics.py::TestAnalyticsStore
        """
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM network_health
                    WHERE timestamp > ? ORDER BY timestamp DESC
                """, (cutoff,))
                rows = cursor.fetchall()

                metrics = []
                for row in rows:
                    metrics.append(NetworkHealthMetrics(
                        timestamp=row[1],
                        online_nodes=row[2],
                        offline_nodes=row[3],
                        avg_rssi_dbm=row[4],
                        avg_snr_db=row[5],
                        avg_link_quality_pct=row[6],
                        packet_success_rate=row[7],
                        uptime_hours=0  # Calculated separately
                    ))
                return metrics
            finally:
                conn.close()

    def get_link_budget_trends(
        self,
        source_node: str,
        dest_node: str,
        hours: int = 168  # 1 week default
    ) -> Dict[str, Any]:
        """Analyze link budget trends over time."""
        history = self.get_link_budget_history(source_node, dest_node, hours)

        if not history:
            return {
                'has_data': False,
                'sample_count': 0,
            }

        rssi_values = [s.rssi_dbm for s in history if s.rssi_dbm]
        snr_values = [s.snr_db for s in history if s.snr_db]

        def safe_avg(values):
            return sum(values) / len(values) if values else 0

        def safe_min(values):
            return min(values) if values else 0

        def safe_max(values):
            return max(values) if values else 0

        return {
            'has_data': True,
            'sample_count': len(history),
            'period_hours': hours,
            'rssi': {
                'avg': safe_avg(rssi_values),
                'min': safe_min(rssi_values),
                'max': safe_max(rssi_values),
                'trend': self._calculate_trend(rssi_values),
            },
            'snr': {
                'avg': safe_avg(snr_values),
                'min': safe_min(snr_values),
                'max': safe_max(snr_values),
                'trend': self._calculate_trend(snr_values),
            },
            'quality_distribution': self._quality_distribution(history),
        }

    def _calculate_trend(self, values: List[float]) -> str:
        """Calculate trend direction (improving, stable, degrading)."""
        if len(values) < 2:
            return 'insufficient_data'

        # Compare first and last thirds
        third = len(values) // 3
        if third < 1:
            return 'stable'

        recent_avg = sum(values[:third]) / third
        old_avg = sum(values[-third:]) / third

        diff = recent_avg - old_avg
        threshold = 2.0  # dB

        if diff > threshold:
            return 'improving'
        elif diff < -threshold:
            return 'degrading'
        return 'stable'

    def _quality_distribution(self, samples: List[LinkBudgetSample]) -> Dict[str, int]:
        """Calculate quality distribution."""
        dist = {'excellent': 0, 'good': 0, 'fair': 0, 'bad': 0}
        for s in samples:
            if s.link_quality in dist:
                dist[s.link_quality] += 1
        return dist

    def cleanup_old_data(self, days: int = 30):
        """Remove data older than specified days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM link_budget_history WHERE timestamp < ?",
                    (cutoff,)
                )
                cursor.execute(
                    "DELETE FROM network_health WHERE timestamp < ?",
                    (cutoff,)
                )
                cursor.execute(
                    "DELETE FROM coverage_snapshots WHERE timestamp < ?",
                    (cutoff,)
                )
                conn.commit()
                logger.info(f"Cleaned up analytics data older than {days} days")
            finally:
                conn.close()


class CoverageAnalyzer:
    """Analyze network coverage from node positions."""

    def __init__(self, store: Optional[AnalyticsStore] = None):
        self.store = store or AnalyticsStore()

    def analyze_coverage(self, nodes: List[Dict]) -> CoverageStats:
        """
        Analyze coverage from list of nodes with position data.

        Args:
            nodes: List of node dicts with 'lat', 'lon' keys

        Returns:
            CoverageStats with calculated metrics
        """
        # Filter nodes with valid positions
        positioned = [
            n for n in nodes
            if n.get('lat') and n.get('lon') and
            n['lat'] != 0 and n['lon'] != 0
        ]

        if not positioned:
            return CoverageStats(
                total_nodes=len(nodes),
                nodes_with_position=0,
                bounding_box={'min_lat': 0, 'max_lat': 0, 'min_lon': 0, 'max_lon': 0},
                center_point=(0, 0),
                estimated_area_km2=0,
                average_node_spacing_km=0,
                coverage_radius_km=0,
            )

        # Calculate bounding box
        lats = [n['lat'] for n in positioned]
        lons = [n['lon'] for n in positioned]

        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)

        # Center point
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2

        # Estimate area using bounding box
        # 1 degree lat ~= 111 km, 1 degree lon ~= 111 * cos(lat) km
        import math
        lat_span_km = (max_lat - min_lat) * 111
        lon_span_km = (max_lon - min_lon) * 111 * math.cos(math.radians(center_lat))
        area_km2 = lat_span_km * lon_span_km

        # Average node spacing (simple approximation)
        if len(positioned) > 1:
            avg_spacing = math.sqrt(area_km2 / len(positioned))
        else:
            avg_spacing = 0

        # Coverage radius (diagonal / 2)
        coverage_radius = math.sqrt(lat_span_km**2 + lon_span_km**2) / 2

        stats = CoverageStats(
            total_nodes=len(nodes),
            nodes_with_position=len(positioned),
            bounding_box={
                'min_lat': min_lat, 'max_lat': max_lat,
                'min_lon': min_lon, 'max_lon': max_lon
            },
            center_point=(center_lat, center_lon),
            estimated_area_km2=round(area_km2, 2),
            average_node_spacing_km=round(avg_spacing, 2),
            coverage_radius_km=round(coverage_radius, 2),
        )

        # Save snapshot
        self.store.record_coverage(stats)

        return stats

    def get_coverage_history(self, days: int = 7) -> List[Dict]:
        """Get coverage snapshots over time."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self.store._lock:
            conn = sqlite3.connect(self.store.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT timestamp, total_nodes, nodes_with_position,
                           area_km2, coverage_radius_km
                    FROM coverage_snapshots
                    WHERE timestamp > ? ORDER BY timestamp DESC
                """, (cutoff,))
                rows = cursor.fetchall()

                return [
                    {
                        'timestamp': row[0],
                        'total_nodes': row[1],
                        'nodes_with_position': row[2],
                        'area_km2': row[3],
                        'coverage_radius_km': row[4],
                    }
                    for row in rows
                ]
            finally:
                conn.close()


def get_analytics_store() -> AnalyticsStore:
    """Get singleton analytics store instance."""
    global _analytics_store
    try:
        return _analytics_store
    except NameError:
        _analytics_store = AnalyticsStore()
        return _analytics_store


def get_coverage_analyzer() -> CoverageAnalyzer:
    """Get singleton coverage analyzer instance."""
    global _coverage_analyzer
    try:
        return _coverage_analyzer
    except NameError:
        _coverage_analyzer = CoverageAnalyzer(get_analytics_store())
        return _coverage_analyzer
