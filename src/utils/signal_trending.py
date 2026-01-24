"""
Signal Strength Trending — Per-node SNR/RSSI time-series analysis.

Provides windowed statistics, pattern detection, and anomaly identification
for individual mesh nodes over time. Works with NodeHistoryDB observations
or direct sample injection.

Key capabilities:
- Windowed statistics (1h, 6h, 24h, 7d averages/min/max/stddev)
- Linear trend detection (improving/stable/degrading with rate in dB/hour)
- Signal stability scoring (0-100, higher = more stable)
- Time-of-day pattern detection (interference fingerprinting)
- Sudden event detection (drops/spikes exceeding threshold)
- Per-node signal reports combining all analyses

Usage:
    from utils.signal_trending import SignalTrend, NodeSignalReport

    trend = SignalTrend()
    trend.add_sample(time.time(), snr=-5.0, rssi=-95)
    trend.add_sample(time.time() + 60, snr=-6.0, rssi=-97)

    report = trend.get_report()
    print(f"Trend: {report.trend_direction} at {report.trend_rate_db_per_hour:.2f} dB/hr")
    print(f"Stability: {report.stability_score}/100")
"""

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SignalSample:
    """A single signal observation at a point in time."""
    timestamp: float
    snr: Optional[float] = None
    rssi: Optional[float] = None

    @property
    def has_snr(self) -> bool:
        return self.snr is not None

    @property
    def has_rssi(self) -> bool:
        return self.rssi is not None


@dataclass
class WindowStats:
    """Statistics for a time window."""
    window_name: str
    window_seconds: int
    sample_count: int = 0
    snr_avg: Optional[float] = None
    snr_min: Optional[float] = None
    snr_max: Optional[float] = None
    snr_stddev: Optional[float] = None
    rssi_avg: Optional[float] = None
    rssi_min: Optional[float] = None
    rssi_max: Optional[float] = None
    rssi_stddev: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'window': self.window_name,
            'window_seconds': self.window_seconds,
            'sample_count': self.sample_count,
            'snr': {
                'avg': self.snr_avg,
                'min': self.snr_min,
                'max': self.snr_max,
                'stddev': self.snr_stddev,
            } if self.snr_avg is not None else None,
            'rssi': {
                'avg': self.rssi_avg,
                'min': self.rssi_min,
                'max': self.rssi_max,
                'stddev': self.rssi_stddev,
            } if self.rssi_avg is not None else None,
        }


@dataclass
class SignalEvent:
    """A detected signal event (sudden drop or spike)."""
    timestamp: float
    event_type: str  # 'drop', 'spike', 'recovery'
    magnitude_db: float  # How large the change was
    metric: str  # 'snr' or 'rssi'
    before_value: float
    after_value: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'event_type': self.event_type,
            'magnitude_db': round(self.magnitude_db, 1),
            'metric': self.metric,
            'before': round(self.before_value, 1),
            'after': round(self.after_value, 1),
        }


@dataclass
class HourlyPattern:
    """Signal pattern for a specific hour of day."""
    hour: int  # 0-23
    sample_count: int = 0
    snr_avg: Optional[float] = None
    rssi_avg: Optional[float] = None


@dataclass
class NodeSignalReport:
    """Complete signal report for a node."""
    node_id: str
    generated_at: float
    total_samples: int
    time_span_hours: float
    trend_direction: str  # 'improving', 'stable', 'degrading', 'insufficient_data'
    trend_rate_db_per_hour: float  # Rate of change (positive = improving)
    stability_score: int  # 0-100 (100 = perfectly stable)
    current_snr: Optional[float] = None
    current_rssi: Optional[float] = None
    windows: List[WindowStats] = field(default_factory=list)
    events: List[SignalEvent] = field(default_factory=list)
    hourly_pattern: List[HourlyPattern] = field(default_factory=list)
    worst_hour: Optional[int] = None  # Hour with worst signal
    best_hour: Optional[int] = None  # Hour with best signal
    pattern_detected: bool = False  # True if time-of-day pattern exists

    def to_dict(self) -> Dict[str, Any]:
        return {
            'node_id': self.node_id,
            'generated_at': self.generated_at,
            'total_samples': self.total_samples,
            'time_span_hours': round(self.time_span_hours, 1),
            'trend': {
                'direction': self.trend_direction,
                'rate_db_per_hour': round(self.trend_rate_db_per_hour, 3),
            },
            'stability_score': self.stability_score,
            'current': {
                'snr': self.current_snr,
                'rssi': self.current_rssi,
            },
            'windows': [w.to_dict() for w in self.windows],
            'events': [e.to_dict() for e in self.events],
            'pattern': {
                'detected': self.pattern_detected,
                'worst_hour': self.worst_hour,
                'best_hour': self.best_hour,
            },
        }


# Default time windows for analysis
DEFAULT_WINDOWS = [
    ('1h', 3600),
    ('6h', 6 * 3600),
    ('24h', 24 * 3600),
    ('7d', 7 * 24 * 3600),
]

# Thresholds
EVENT_THRESHOLD_DB = 5.0  # dB change to qualify as an event
TREND_THRESHOLD_DB_HR = 0.1  # dB/hour to be considered non-stable
PATTERN_VARIANCE_THRESHOLD = 3.0  # dB variance across hours = pattern
MIN_SAMPLES_FOR_TREND = 3
MIN_SAMPLES_PER_HOUR_FOR_PATTERN = 2
MAX_SAMPLES = 10080  # ~1 week at 1 sample/minute


class SignalTrend:
    """Per-node signal strength trending and pattern analysis.

    Collects SNR/RSSI samples over time and provides statistical analysis,
    trend detection, and pattern identification.

    Thread-safe for sample addition. Analysis methods return new objects.
    """

    def __init__(self, node_id: str = "", max_samples: int = MAX_SAMPLES):
        """Initialize signal trending for a node.

        Args:
            node_id: The node identifier.
            max_samples: Maximum samples to retain (oldest evicted first).
        """
        self.node_id = node_id
        self.max_samples = max_samples
        self._samples: List[SignalSample] = []
        self._lock = threading.Lock()

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._samples)

    def _snapshot(self) -> List[SignalSample]:
        """Return a thread-safe copy of samples for analysis."""
        with self._lock:
            return list(self._samples)

    def add_sample(self, timestamp: float,
                   snr: Optional[float] = None,
                   rssi: Optional[float] = None) -> None:
        """Add a signal observation.

        Args:
            timestamp: Unix timestamp of the observation.
            snr: Signal-to-noise ratio in dB (optional).
            rssi: Received signal strength in dBm (optional).
        """
        if snr is None and rssi is None:
            return  # No data to record

        sample = SignalSample(timestamp=timestamp, snr=snr, rssi=rssi)
        with self._lock:
            self._samples.append(sample)

            # Evict oldest if over limit
            if len(self._samples) > self.max_samples:
                self._samples = self._samples[-self.max_samples:]

    def add_samples_bulk(self, samples: List[Tuple[float, Optional[float], Optional[float]]]) -> int:
        """Add multiple samples at once.

        Args:
            samples: List of (timestamp, snr, rssi) tuples.

        Returns:
            Number of samples actually added.
        """
        added = 0
        with self._lock:
            for ts, snr, rssi in samples:
                if snr is not None or rssi is not None:
                    self._samples.append(SignalSample(timestamp=ts, snr=snr, rssi=rssi))
                    added += 1

            # Trim to max
            if len(self._samples) > self.max_samples:
                self._samples = self._samples[-self.max_samples:]

        return added

    def get_window_stats(self, window_seconds: int,
                         window_name: str = "",
                         now: Optional[float] = None) -> WindowStats:
        """Calculate statistics for a time window.

        Args:
            window_seconds: How far back to look.
            window_name: Label for this window.
            now: Reference time (default: current time).

        Returns:
            WindowStats with calculated metrics.
        """
        if now is None:
            now = time.time()

        cutoff = now - window_seconds
        with self._lock:
            samples_snapshot = list(self._samples)
        window_samples = [s for s in samples_snapshot if s.timestamp >= cutoff]

        stats = WindowStats(
            window_name=window_name or f"{window_seconds}s",
            window_seconds=window_seconds,
            sample_count=len(window_samples),
        )

        if not window_samples:
            return stats

        # SNR statistics
        snr_values = [s.snr for s in window_samples if s.has_snr]
        if snr_values:
            stats.snr_avg = sum(snr_values) / len(snr_values)
            stats.snr_min = min(snr_values)
            stats.snr_max = max(snr_values)
            if len(snr_values) > 1:
                mean = stats.snr_avg
                variance = sum((v - mean) ** 2 for v in snr_values) / (len(snr_values) - 1)
                stats.snr_stddev = math.sqrt(variance)
            else:
                stats.snr_stddev = 0.0

        # RSSI statistics
        rssi_values = [s.rssi for s in window_samples if s.has_rssi]
        if rssi_values:
            stats.rssi_avg = sum(rssi_values) / len(rssi_values)
            stats.rssi_min = min(rssi_values)
            stats.rssi_max = max(rssi_values)
            if len(rssi_values) > 1:
                mean = stats.rssi_avg
                variance = sum((v - mean) ** 2 for v in rssi_values) / (len(rssi_values) - 1)
                stats.rssi_stddev = math.sqrt(variance)
            else:
                stats.rssi_stddev = 0.0

        return stats

    def get_trend(self, now: Optional[float] = None) -> Tuple[str, float]:
        """Calculate linear trend direction and rate.

        Uses linear regression on SNR values (or RSSI if no SNR).
        Rate is in dB per hour.

        Args:
            now: Reference time (unused, analyzes all samples).

        Returns:
            Tuple of (direction, rate_db_per_hour).
            Direction: 'improving', 'stable', 'degrading', 'insufficient_data'
        """
        samples = self._snapshot()
        if len(samples) < MIN_SAMPLES_FOR_TREND:
            return ('insufficient_data', 0.0)

        # Prefer SNR, fall back to RSSI
        values_with_time = [(s.timestamp, s.snr) for s in samples if s.has_snr]
        if len(values_with_time) < MIN_SAMPLES_FOR_TREND:
            values_with_time = [(s.timestamp, s.rssi) for s in samples if s.has_rssi]

        if len(values_with_time) < MIN_SAMPLES_FOR_TREND:
            return ('insufficient_data', 0.0)

        # Linear regression: y = mx + b
        slope = self._linear_regression_slope(values_with_time)

        # Convert slope from dB/second to dB/hour
        rate_per_hour = slope * 3600.0

        if rate_per_hour > TREND_THRESHOLD_DB_HR:
            return ('improving', rate_per_hour)
        elif rate_per_hour < -TREND_THRESHOLD_DB_HR:
            return ('degrading', rate_per_hour)
        return ('stable', rate_per_hour)

    def get_stability_score(self, window_seconds: int = 3600,
                            now: Optional[float] = None) -> int:
        """Calculate signal stability score (0-100).

        Score based on standard deviation of SNR within the window.
        Lower variance = higher stability.

        Scoring:
        - stddev < 1.0 dB: 90-100 (rock solid)
        - stddev 1-3 dB: 70-90 (normal)
        - stddev 3-6 dB: 40-70 (noisy)
        - stddev 6-10 dB: 10-40 (unstable)
        - stddev > 10 dB: 0-10 (chaotic)

        Args:
            window_seconds: Analysis window (default 1 hour).
            now: Reference time.

        Returns:
            Stability score 0-100.
        """
        stats = self.get_window_stats(window_seconds, now=now)

        if stats.sample_count < 2:
            return 50  # Unknown, neutral score

        # Use SNR stddev preferentially
        stddev = stats.snr_stddev
        if stddev is None:
            stddev = stats.rssi_stddev
        if stddev is None:
            return 50

        # Map stddev to 0-100 score (inverse relationship)
        if stddev < 1.0:
            score = 90 + int(10 * (1.0 - stddev))
        elif stddev < 3.0:
            score = 70 + int(20 * (3.0 - stddev) / 2.0)
        elif stddev < 6.0:
            score = 40 + int(30 * (6.0 - stddev) / 3.0)
        elif stddev < 10.0:
            score = 10 + int(30 * (10.0 - stddev) / 4.0)
        else:
            score = max(0, int(10 * (15.0 - stddev) / 5.0))

        return max(0, min(100, score))

    def detect_events(self, threshold_db: float = EVENT_THRESHOLD_DB) -> List[SignalEvent]:
        """Detect sudden signal events (drops, spikes, recoveries).

        Compares consecutive samples. A change exceeding threshold_db
        between adjacent samples is flagged as an event.

        Args:
            threshold_db: Minimum dB change to flag as event.

        Returns:
            List of SignalEvent objects, ordered by time.
        """
        events: List[SignalEvent] = []
        samples = self._snapshot()

        if len(samples) < 2:
            return events

        # Check SNR events
        snr_samples = [(s.timestamp, s.snr) for s in samples if s.has_snr]
        events.extend(self._detect_metric_events(snr_samples, 'snr', threshold_db))

        # Check RSSI events
        rssi_samples = [(s.timestamp, s.rssi) for s in samples if s.has_rssi]
        events.extend(self._detect_metric_events(rssi_samples, 'rssi', threshold_db))

        events.sort(key=lambda e: e.timestamp)
        return events

    def get_hourly_pattern(self) -> Tuple[List[HourlyPattern], bool]:
        """Analyze signal strength by hour of day.

        Groups samples by hour (0-23) and calculates average SNR/RSSI
        for each hour. Detects if there's a significant time-of-day pattern
        (indicating environmental interference like commute traffic, solar heating, etc.).

        Returns:
            Tuple of (hourly_patterns, pattern_detected).
        """
        import time as time_mod

        # Bin samples by hour
        hourly_snr: Dict[int, List[float]] = {h: [] for h in range(24)}
        hourly_rssi: Dict[int, List[float]] = {h: [] for h in range(24)}
        samples = self._snapshot()

        for sample in samples:
            hour = time_mod.localtime(sample.timestamp).tm_hour
            if sample.has_snr:
                hourly_snr[hour].append(sample.snr)
            if sample.has_rssi:
                hourly_rssi[hour].append(sample.rssi)

        patterns: List[HourlyPattern] = []
        hourly_avgs: List[Optional[float]] = []

        for hour in range(24):
            snr_vals = hourly_snr[hour]
            rssi_vals = hourly_rssi[hour]

            pattern = HourlyPattern(hour=hour)
            pattern.sample_count = len(snr_vals) + len(rssi_vals)

            if len(snr_vals) >= MIN_SAMPLES_PER_HOUR_FOR_PATTERN:
                pattern.snr_avg = sum(snr_vals) / len(snr_vals)
                hourly_avgs.append(pattern.snr_avg)
            else:
                hourly_avgs.append(None)

            if len(rssi_vals) >= MIN_SAMPLES_PER_HOUR_FOR_PATTERN:
                pattern.rssi_avg = sum(rssi_vals) / len(rssi_vals)

            patterns.append(pattern)

        # Detect if pattern exists: variance across hourly averages > threshold
        valid_avgs = [a for a in hourly_avgs if a is not None]
        pattern_detected = False

        if len(valid_avgs) >= 4:  # Need data in at least 4 hours
            mean = sum(valid_avgs) / len(valid_avgs)
            variance = sum((v - mean) ** 2 for v in valid_avgs) / len(valid_avgs)
            stddev = math.sqrt(variance)
            pattern_detected = stddev >= PATTERN_VARIANCE_THRESHOLD

        return patterns, pattern_detected

    def get_report(self, now: Optional[float] = None) -> NodeSignalReport:
        """Generate a complete signal report for this node.

        Combines all analyses into a single report structure.

        Args:
            now: Reference time (default: current time).

        Returns:
            NodeSignalReport with all analyses.
        """
        if now is None:
            now = time.time()

        # Basic info
        samples = self._snapshot()
        total = len(samples)
        if total == 0:
            return NodeSignalReport(
                node_id=self.node_id,
                generated_at=now,
                total_samples=0,
                time_span_hours=0.0,
                trend_direction='insufficient_data',
                trend_rate_db_per_hour=0.0,
                stability_score=50,
            )

        time_span = (samples[-1].timestamp - samples[0].timestamp) / 3600.0

        # Current values (most recent sample)
        latest = samples[-1]

        # Trend
        direction, rate = self.get_trend(now)

        # Stability
        stability = self.get_stability_score(now=now)

        # Window stats
        windows = []
        for name, seconds in DEFAULT_WINDOWS:
            ws = self.get_window_stats(seconds, window_name=name, now=now)
            if ws.sample_count > 0:
                windows.append(ws)

        # Events
        events = self.detect_events()

        # Hourly pattern
        hourly_patterns, pattern_detected = self.get_hourly_pattern()

        # Find best/worst hours
        best_hour = None
        worst_hour = None
        if pattern_detected:
            valid_hours = [(p.hour, p.snr_avg) for p in hourly_patterns
                           if p.snr_avg is not None]
            if valid_hours:
                best_hour = max(valid_hours, key=lambda x: x[1])[0]
                worst_hour = min(valid_hours, key=lambda x: x[1])[0]

        return NodeSignalReport(
            node_id=self.node_id,
            generated_at=now,
            total_samples=total,
            time_span_hours=time_span,
            trend_direction=direction,
            trend_rate_db_per_hour=rate,
            stability_score=stability,
            current_snr=latest.snr,
            current_rssi=latest.rssi,
            windows=windows,
            events=events,
            hourly_pattern=hourly_patterns,
            worst_hour=worst_hour,
            best_hour=best_hour,
            pattern_detected=pattern_detected,
        )

    # -------------------------------------------------------------------------
    # Internal methods
    # -------------------------------------------------------------------------

    def _linear_regression_slope(self,
                                 points: List[Tuple[float, float]]) -> float:
        """Calculate slope of best-fit line using least squares.

        Args:
            points: List of (x, y) tuples.

        Returns:
            Slope (dy/dx).
        """
        n = len(points)
        if n < 2:
            return 0.0

        sum_x = sum(p[0] for p in points)
        sum_y = sum(p[1] for p in points)
        sum_xy = sum(p[0] * p[1] for p in points)
        sum_x2 = sum(p[0] ** 2 for p in points)

        denominator = n * sum_x2 - sum_x ** 2
        if abs(denominator) < 1e-10:
            return 0.0

        return (n * sum_xy - sum_x * sum_y) / denominator

    def _detect_metric_events(self,
                              samples: List[Tuple[float, float]],
                              metric: str,
                              threshold_db: float) -> List[SignalEvent]:
        """Detect events in a sequence of (timestamp, value) pairs."""
        events: List[SignalEvent] = []

        for i in range(1, len(samples)):
            ts_before, val_before = samples[i - 1]
            ts_after, val_after = samples[i]

            change = val_after - val_before
            magnitude = abs(change)

            if magnitude >= threshold_db:
                if change < 0:
                    event_type = 'drop'
                else:
                    # Check if this is a recovery (drop followed by rise)
                    if i >= 2:
                        _, val_prev = samples[i - 2]
                        # Recovery: val_before was below val_prev (in a dip),
                        # and val_after recovered past the midpoint
                        midpoint = (val_before + val_prev) / 2
                        if val_before < val_prev and val_after > midpoint:
                            event_type = 'recovery'
                        else:
                            event_type = 'spike'
                    else:
                        event_type = 'spike'

                events.append(SignalEvent(
                    timestamp=ts_after,
                    event_type=event_type,
                    magnitude_db=magnitude,
                    metric=metric,
                    before_value=val_before,
                    after_value=val_after,
                ))

        return events


class SignalTrendingManager:
    """Manages signal trending for multiple nodes.

    Provides a unified interface for tracking signal trends across
    all known nodes, with methods for bulk ingestion from NodeHistoryDB.

    Usage:
        manager = SignalTrendingManager()
        manager.ingest_from_history(node_history_db, hours=24)

        for node_id in manager.get_tracked_nodes():
            report = manager.get_report(node_id)
            if report.trend_direction == 'degrading':
                print(f"WARNING: {node_id} signal degrading")
    """

    def __init__(self, max_samples_per_node: int = MAX_SAMPLES):
        self._trends: Dict[str, SignalTrend] = {}
        self._max_samples = max_samples_per_node

    def get_or_create(self, node_id: str) -> SignalTrend:
        """Get existing trend tracker or create new one."""
        if node_id not in self._trends:
            self._trends[node_id] = SignalTrend(
                node_id=node_id,
                max_samples=self._max_samples,
            )
        return self._trends[node_id]

    def add_sample(self, node_id: str, timestamp: float,
                   snr: Optional[float] = None,
                   rssi: Optional[float] = None) -> None:
        """Add a signal sample for a node."""
        trend = self.get_or_create(node_id)
        trend.add_sample(timestamp, snr, rssi)

    def get_tracked_nodes(self) -> List[str]:
        """Get list of all tracked node IDs."""
        return list(self._trends.keys())

    def get_report(self, node_id: str) -> Optional[NodeSignalReport]:
        """Get signal report for a specific node."""
        trend = self._trends.get(node_id)
        if trend is None:
            return None
        return trend.get_report()

    def get_all_reports(self) -> List[NodeSignalReport]:
        """Get reports for all tracked nodes."""
        return [t.get_report() for t in self._trends.values()]

    def get_degrading_nodes(self) -> List[NodeSignalReport]:
        """Get reports for nodes with degrading signal."""
        reports = self.get_all_reports()
        return [r for r in reports if r.trend_direction == 'degrading']

    def get_unstable_nodes(self, threshold: int = 40) -> List[NodeSignalReport]:
        """Get reports for nodes with low stability scores.

        Args:
            threshold: Stability score below which a node is considered unstable.

        Returns:
            List of reports for unstable nodes.
        """
        reports = self.get_all_reports()
        return [r for r in reports if r.stability_score < threshold]

    def ingest_from_history(self, history_db: Any, hours: float = 24) -> int:
        """Ingest signal data from a NodeHistoryDB instance.

        Queries the history database for observations within the time window
        and populates trend trackers for each node.

        Args:
            history_db: NodeHistoryDB instance.
            hours: How far back to load.

        Returns:
            Total number of samples ingested.
        """
        total = 0

        # Get all nodes with recent observations
        nodes = history_db.get_unique_nodes(hours=hours)

        for node_info in nodes:
            node_id = node_info.get('node_id', '')
            if not node_id:
                continue

            # Get trajectory (all observations in window)
            observations = history_db.get_trajectory(node_id, hours=hours)

            trend = self.get_or_create(node_id)
            for obs in observations:
                if obs.snr is not None:
                    trend.add_sample(obs.timestamp, snr=obs.snr)
                    total += 1

        return total

    def get_summary(self) -> Dict[str, Any]:
        """Get overall summary of all tracked nodes.

        Returns:
            Dict with node counts, health overview, and alerts.
        """
        reports = self.get_all_reports()

        if not reports:
            return {
                'total_nodes': 0,
                'total_samples': 0,
                'health': {},
                'alerts': [],
            }

        degrading = [r for r in reports if r.trend_direction == 'degrading']
        improving = [r for r in reports if r.trend_direction == 'improving']
        unstable = [r for r in reports if r.stability_score < 40]
        with_patterns = [r for r in reports if r.pattern_detected]

        alerts = []
        for r in degrading:
            alerts.append({
                'node_id': r.node_id,
                'type': 'degrading_signal',
                'rate': round(r.trend_rate_db_per_hour, 3),
                'stability': r.stability_score,
            })
        for r in unstable:
            if r not in degrading:  # Avoid duplicate alerts
                alerts.append({
                    'node_id': r.node_id,
                    'type': 'unstable_signal',
                    'stability': r.stability_score,
                })

        return {
            'total_nodes': len(reports),
            'total_samples': sum(r.total_samples for r in reports),
            'health': {
                'improving': len(improving),
                'stable': len([r for r in reports if r.trend_direction == 'stable']),
                'degrading': len(degrading),
                'insufficient_data': len([r for r in reports
                                          if r.trend_direction == 'insufficient_data']),
                'unstable': len(unstable),
                'with_patterns': len(with_patterns),
            },
            'alerts': alerts,
        }
