"""
Predictive Maintenance for Mesh Network Nodes.

Provides proactive failure prediction based on:
- Battery drain rate analysis (project time-to-death)
- Node dropout pattern recognition (periodicity, time-of-day)
- Uptime/reliability scoring
- Maintenance scheduling recommendations

Usage:
    from utils.predictive_maintenance import MaintenancePredictor

    predictor = MaintenancePredictor()
    predictor.record_battery("!node1", 85.0, voltage=3.95)
    predictor.record_battery("!node1", 82.0, voltage=3.90)
    report = predictor.get_battery_forecast("!node1")
    print(f"Time to critical: {report.hours_to_critical:.1f}h")

    predictor.record_status("!node2", online=True)
    predictor.record_status("!node2", online=False)
    pattern = predictor.get_dropout_pattern("!node2")
    print(f"Dropout frequency: {pattern.dropouts_per_day:.1f}/day")
"""

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Battery voltage to percentage mapping for common Li-ion/LiPo (3.7V nominal)
# Approximate curve: 4.2V=100%, 3.7V=50%, 3.3V=10%, 3.0V=0%
VOLTAGE_TO_PCT = [
    (4.20, 100.0),
    (4.10, 90.0),
    (4.00, 80.0),
    (3.90, 70.0),
    (3.80, 55.0),
    (3.70, 40.0),
    (3.60, 25.0),
    (3.50, 15.0),
    (3.40, 8.0),
    (3.30, 4.0),
    (3.20, 1.0),
    (3.00, 0.0),
]

# Thresholds
BATTERY_WARNING_PCT = 30.0
BATTERY_CRITICAL_PCT = 15.0
BATTERY_SHUTDOWN_PCT = 5.0

# Minimum samples for meaningful prediction
MIN_BATTERY_SAMPLES = 3
MIN_DROPOUT_EVENTS = 2
MIN_STATUS_SAMPLES = 5


@dataclass
class BatterySample:
    """Single battery measurement."""
    timestamp: float  # Unix timestamp
    percentage: float  # 0-100
    voltage: Optional[float] = None  # Volts


@dataclass
class BatteryForecast:
    """Battery life prediction for a node."""
    node_id: str
    current_pct: float
    current_voltage: Optional[float]
    drain_rate_pct_per_hour: float
    hours_to_warning: Optional[float]  # Hours until 30%
    hours_to_critical: Optional[float]  # Hours until 15%
    hours_to_shutdown: Optional[float]  # Hours until 5%
    confidence: float  # 0.0 to 1.0
    trend: str  # 'draining', 'charging', 'stable', 'insufficient_data'
    sample_count: int
    time_span_hours: float  # How many hours of data we have
    is_solar: bool  # Detected solar/charging pattern

    def to_dict(self) -> Dict:
        """Convert to serializable dict."""
        return {
            'node_id': self.node_id,
            'current_pct': round(self.current_pct, 1),
            'current_voltage': round(self.current_voltage, 3) if self.current_voltage else None,
            'drain_rate_pct_per_hour': round(self.drain_rate_pct_per_hour, 3),
            'hours_to_warning': round(self.hours_to_warning, 1) if self.hours_to_warning else None,
            'hours_to_critical': round(self.hours_to_critical, 1) if self.hours_to_critical else None,
            'hours_to_shutdown': round(self.hours_to_shutdown, 1) if self.hours_to_shutdown else None,
            'confidence': round(self.confidence, 2),
            'trend': self.trend,
            'sample_count': self.sample_count,
            'time_span_hours': round(self.time_span_hours, 1),
            'is_solar': self.is_solar,
        }


@dataclass
class StatusEvent:
    """Node online/offline status event."""
    timestamp: float  # Unix timestamp
    online: bool


@dataclass
class DropoutPattern:
    """Analysis of node dropout behavior."""
    node_id: str
    total_events: int
    dropout_count: int  # Number of offline transitions
    recovery_count: int  # Number of online transitions after dropout
    avg_downtime_minutes: float
    max_downtime_minutes: float
    dropouts_per_day: float
    uptime_pct: float  # 0-100
    is_periodic: bool  # True if dropouts follow a regular pattern
    period_hours: Optional[float]  # Period if periodic
    peak_dropout_hour: Optional[int]  # Hour of day (0-23) with most dropouts
    reliability_score: float  # 0-100, higher = more reliable
    prediction: str  # 'stable', 'intermittent', 'failing', 'insufficient_data'

    def to_dict(self) -> Dict:
        """Convert to serializable dict."""
        return {
            'node_id': self.node_id,
            'total_events': self.total_events,
            'dropout_count': self.dropout_count,
            'recovery_count': self.recovery_count,
            'avg_downtime_minutes': round(self.avg_downtime_minutes, 1),
            'max_downtime_minutes': round(self.max_downtime_minutes, 1),
            'dropouts_per_day': round(self.dropouts_per_day, 2),
            'uptime_pct': round(self.uptime_pct, 1),
            'is_periodic': self.is_periodic,
            'period_hours': round(self.period_hours, 1) if self.period_hours else None,
            'peak_dropout_hour': self.peak_dropout_hour,
            'reliability_score': round(self.reliability_score, 1),
            'prediction': self.prediction,
        }


@dataclass
class MaintenanceRecommendation:
    """A maintenance action recommendation."""
    node_id: str
    priority: str  # 'urgent', 'soon', 'scheduled', 'monitor'
    action: str
    reason: str
    deadline_hours: Optional[float]  # Hours until action needed


class MaintenancePredictor:
    """
    Predictive maintenance engine for mesh network nodes.

    Tracks battery levels and online/offline status to predict:
    - When nodes will run out of power
    - Whether nodes have dropout patterns (periodic reboots, thermal shutdowns)
    - Maintenance scheduling recommendations
    """

    # Maximum samples to retain per node
    MAX_BATTERY_SAMPLES = 500
    MAX_STATUS_EVENTS = 1000

    def __init__(self):
        self._battery_history: Dict[str, List[BatterySample]] = {}
        self._status_history: Dict[str, List[StatusEvent]] = {}

    def record_battery(self, node_id: str, percentage: float,
                       voltage: Optional[float] = None,
                       timestamp: Optional[float] = None) -> None:
        """
        Record a battery measurement for a node.

        Args:
            node_id: Node identifier
            percentage: Battery percentage (0-100)
            voltage: Optional battery voltage
            timestamp: Optional Unix timestamp (defaults to now)
        """
        if node_id not in self._battery_history:
            self._battery_history[node_id] = []

        sample = BatterySample(
            timestamp=timestamp or time.time(),
            percentage=max(0.0, min(100.0, percentage)),
            voltage=voltage,
        )
        self._battery_history[node_id].append(sample)

        # Trim to max size
        if len(self._battery_history[node_id]) > self.MAX_BATTERY_SAMPLES:
            self._battery_history[node_id] = self._battery_history[node_id][-self.MAX_BATTERY_SAMPLES:]

    def record_status(self, node_id: str, online: bool,
                      timestamp: Optional[float] = None) -> None:
        """
        Record a node status change.

        Args:
            node_id: Node identifier
            online: True if node came online, False if went offline
            timestamp: Optional Unix timestamp (defaults to now)
        """
        if node_id not in self._status_history:
            self._status_history[node_id] = []

        event = StatusEvent(
            timestamp=timestamp or time.time(),
            online=online,
        )
        self._status_history[node_id].append(event)

        # Trim to max size
        if len(self._status_history[node_id]) > self.MAX_STATUS_EVENTS:
            self._status_history[node_id] = self._status_history[node_id][-self.MAX_STATUS_EVENTS:]

    def get_battery_forecast(self, node_id: str) -> BatteryForecast:
        """
        Get battery life prediction for a node.

        Args:
            node_id: Node identifier

        Returns:
            BatteryForecast with drain rate and time-to-thresholds
        """
        samples = self._battery_history.get(node_id, [])

        if len(samples) < MIN_BATTERY_SAMPLES:
            current = samples[-1] if samples else BatterySample(time.time(), 0.0)
            return BatteryForecast(
                node_id=node_id,
                current_pct=current.percentage,
                current_voltage=current.voltage,
                drain_rate_pct_per_hour=0.0,
                hours_to_warning=None,
                hours_to_critical=None,
                hours_to_shutdown=None,
                confidence=0.0,
                trend='insufficient_data',
                sample_count=len(samples),
                time_span_hours=0.0,
                is_solar=False,
            )

        # Sort by timestamp
        sorted_samples = sorted(samples, key=lambda s: s.timestamp)
        current = sorted_samples[-1]

        # Calculate time span
        time_span_s = sorted_samples[-1].timestamp - sorted_samples[0].timestamp
        time_span_hours = max(time_span_s / 3600.0, 0.001)  # Avoid division by zero

        # Calculate drain rate using linear regression
        drain_rate = self._calculate_drain_rate(sorted_samples)

        # Detect solar/charging patterns
        is_solar = self._detect_charging_pattern(sorted_samples)

        # Determine trend
        if abs(drain_rate) < 0.05:
            trend = 'stable'
        elif drain_rate > 0:
            trend = 'charging'
        else:
            trend = 'draining'

        # Calculate time-to-thresholds (only meaningful when draining)
        hours_to_warning = None
        hours_to_critical = None
        hours_to_shutdown = None

        if drain_rate < -0.01:  # Draining
            rate_abs = abs(drain_rate)
            if current.percentage > BATTERY_WARNING_PCT:
                hours_to_warning = (current.percentage - BATTERY_WARNING_PCT) / rate_abs
            if current.percentage > BATTERY_CRITICAL_PCT:
                hours_to_critical = (current.percentage - BATTERY_CRITICAL_PCT) / rate_abs
            if current.percentage > BATTERY_SHUTDOWN_PCT:
                hours_to_shutdown = (current.percentage - BATTERY_SHUTDOWN_PCT) / rate_abs

        # Confidence based on sample count and time span
        confidence = self._calculate_battery_confidence(sorted_samples, time_span_hours)

        return BatteryForecast(
            node_id=node_id,
            current_pct=current.percentage,
            current_voltage=current.voltage,
            drain_rate_pct_per_hour=drain_rate,
            hours_to_warning=hours_to_warning,
            hours_to_critical=hours_to_critical,
            hours_to_shutdown=hours_to_shutdown,
            confidence=confidence,
            trend=trend,
            sample_count=len(sorted_samples),
            time_span_hours=time_span_hours,
            is_solar=is_solar,
        )

    def get_dropout_pattern(self, node_id: str) -> DropoutPattern:
        """
        Analyze dropout patterns for a node.

        Args:
            node_id: Node identifier

        Returns:
            DropoutPattern with frequency, periodicity, and reliability info
        """
        events = self._status_history.get(node_id, [])

        if len(events) < MIN_STATUS_SAMPLES:
            return DropoutPattern(
                node_id=node_id,
                total_events=len(events),
                dropout_count=0,
                recovery_count=0,
                avg_downtime_minutes=0.0,
                max_downtime_minutes=0.0,
                dropouts_per_day=0.0,
                uptime_pct=100.0 if not events else 50.0,
                is_periodic=False,
                period_hours=None,
                peak_dropout_hour=None,
                reliability_score=50.0,
                prediction='insufficient_data',
            )

        # Sort by timestamp
        sorted_events = sorted(events, key=lambda e: e.timestamp)

        # Count transitions
        dropouts: List[float] = []  # timestamps of offline events
        recoveries: List[float] = []  # timestamps of online events
        downtimes: List[float] = []  # durations in seconds

        for i, event in enumerate(sorted_events):
            if not event.online:
                dropouts.append(event.timestamp)
                # Find next online event for downtime calculation
                for j in range(i + 1, len(sorted_events)):
                    if sorted_events[j].online:
                        downtimes.append(sorted_events[j].timestamp - event.timestamp)
                        break
            else:
                if i > 0 and not sorted_events[i - 1].online:
                    recoveries.append(event.timestamp)

        # Time span
        time_span_s = sorted_events[-1].timestamp - sorted_events[0].timestamp
        time_span_days = max(time_span_s / 86400.0, 0.001)

        # Calculate uptime percentage
        total_downtime_s = sum(downtimes)
        uptime_pct = max(0.0, min(100.0,
                                  100.0 * (1.0 - total_downtime_s / max(time_span_s, 1.0))))

        # Dropouts per day
        dropouts_per_day = len(dropouts) / time_span_days if dropouts else 0.0

        # Average and max downtime
        avg_downtime_min = (sum(downtimes) / len(downtimes) / 60.0) if downtimes else 0.0
        max_downtime_min = (max(downtimes) / 60.0) if downtimes else 0.0

        # Detect periodicity in dropout timestamps
        is_periodic, period_hours = self._detect_periodicity(dropouts)

        # Find peak dropout hour
        peak_hour = self._find_peak_hour(dropouts)

        # Calculate reliability score
        reliability = self._calculate_reliability(
            uptime_pct, dropouts_per_day, avg_downtime_min, len(sorted_events)
        )

        # Determine prediction
        prediction = self._classify_dropout_behavior(
            dropouts_per_day, uptime_pct, is_periodic, len(dropouts)
        )

        return DropoutPattern(
            node_id=node_id,
            total_events=len(sorted_events),
            dropout_count=len(dropouts),
            recovery_count=len(recoveries),
            avg_downtime_minutes=avg_downtime_min,
            max_downtime_minutes=max_downtime_min,
            dropouts_per_day=dropouts_per_day,
            uptime_pct=uptime_pct,
            is_periodic=is_periodic,
            period_hours=period_hours,
            peak_dropout_hour=peak_hour,
            reliability_score=reliability,
            prediction=prediction,
        )

    def get_maintenance_recommendations(self) -> List[MaintenanceRecommendation]:
        """
        Generate maintenance recommendations for all tracked nodes.

        Returns:
            List of MaintenanceRecommendation sorted by priority
        """
        recommendations: List[MaintenanceRecommendation] = []

        # Check battery forecasts
        for node_id in self._battery_history:
            forecast = self.get_battery_forecast(node_id)

            # Already below shutdown threshold — urgent regardless of trend
            if forecast.current_pct <= BATTERY_SHUTDOWN_PCT and forecast.sample_count >= MIN_BATTERY_SAMPLES:
                recommendations.append(MaintenanceRecommendation(
                    node_id=node_id,
                    priority='urgent',
                    action='Replace or charge battery immediately',
                    reason=f'Battery at {forecast.current_pct:.0f}% — below shutdown threshold',
                    deadline_hours=0.0,
                ))
                continue

            # Already below critical threshold
            if forecast.current_pct <= BATTERY_CRITICAL_PCT and forecast.sample_count >= MIN_BATTERY_SAMPLES:
                recommendations.append(MaintenanceRecommendation(
                    node_id=node_id,
                    priority='urgent',
                    action='Replace or charge battery immediately',
                    reason=f'Battery at {forecast.current_pct:.0f}% — critically low',
                    deadline_hours=1.0,
                ))
                continue

            if forecast.trend == 'draining':
                if forecast.hours_to_shutdown is not None and forecast.hours_to_shutdown < 6:
                    recommendations.append(MaintenanceRecommendation(
                        node_id=node_id,
                        priority='urgent',
                        action='Replace or charge battery immediately',
                        reason=f'Battery at {forecast.current_pct:.0f}%, '
                               f'shutdown in ~{forecast.hours_to_shutdown:.0f}h',
                        deadline_hours=forecast.hours_to_shutdown,
                    ))
                elif forecast.hours_to_critical is not None and forecast.hours_to_critical < 24:
                    recommendations.append(MaintenanceRecommendation(
                        node_id=node_id,
                        priority='soon',
                        action='Schedule battery replacement',
                        reason=f'Battery at {forecast.current_pct:.0f}%, '
                               f'critical in ~{forecast.hours_to_critical:.0f}h',
                        deadline_hours=forecast.hours_to_critical,
                    ))
                elif forecast.hours_to_warning is not None and forecast.hours_to_warning < 72:
                    recommendations.append(MaintenanceRecommendation(
                        node_id=node_id,
                        priority='scheduled',
                        action='Plan battery maintenance visit',
                        reason=f'Battery draining at {abs(forecast.drain_rate_pct_per_hour):.1f}%/h',
                        deadline_hours=forecast.hours_to_warning,
                    ))

        # Check dropout patterns
        for node_id in self._status_history:
            pattern = self.get_dropout_pattern(node_id)

            if pattern.prediction == 'failing':
                recommendations.append(MaintenanceRecommendation(
                    node_id=node_id,
                    priority='urgent',
                    action='Investigate node hardware — showing failure pattern',
                    reason=f'{pattern.dropouts_per_day:.1f} dropouts/day, '
                           f'{pattern.uptime_pct:.0f}% uptime',
                    deadline_hours=24.0,
                ))
            elif pattern.prediction == 'intermittent':
                if pattern.is_periodic and pattern.period_hours:
                    reason = (f'Periodic dropouts every ~{pattern.period_hours:.0f}h '
                              f'(possible thermal/watchdog issue)')
                else:
                    reason = f'{pattern.dropouts_per_day:.1f} dropouts/day'

                recommendations.append(MaintenanceRecommendation(
                    node_id=node_id,
                    priority='soon',
                    action='Investigate intermittent connectivity',
                    reason=reason,
                    deadline_hours=72.0,
                ))

        # Sort by priority
        priority_order = {'urgent': 0, 'soon': 1, 'scheduled': 2, 'monitor': 3}
        recommendations.sort(key=lambda r: priority_order.get(r.priority, 4))

        return recommendations

    def get_all_forecasts(self) -> Dict[str, BatteryForecast]:
        """Get battery forecasts for all tracked nodes."""
        return {
            node_id: self.get_battery_forecast(node_id)
            for node_id in self._battery_history
        }

    def get_all_patterns(self) -> Dict[str, DropoutPattern]:
        """Get dropout patterns for all tracked nodes."""
        return {
            node_id: self.get_dropout_pattern(node_id)
            for node_id in self._status_history
        }

    def get_node_ids(self) -> List[str]:
        """Get all tracked node IDs."""
        ids = set(self._battery_history.keys())
        ids.update(self._status_history.keys())
        return sorted(ids)

    # =========================================================================
    # Private helpers
    # =========================================================================

    def _calculate_drain_rate(self, samples: List[BatterySample]) -> float:
        """
        Calculate battery drain rate using linear regression.

        Returns:
            Drain rate in %/hour (negative = draining, positive = charging)
        """
        if len(samples) < 2:
            return 0.0

        # Normalize timestamps to hours from first sample
        t0 = samples[0].timestamp
        x = [(s.timestamp - t0) / 3600.0 for s in samples]
        y = [s.percentage for s in samples]

        n = len(x)
        x_mean = sum(x) / n
        y_mean = sum(y) / n

        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

        if denominator < 1e-10:
            return 0.0

        return numerator / denominator

    def _detect_charging_pattern(self, samples: List[BatterySample]) -> bool:
        """
        Detect if node has solar/charging patterns (cycles up and down).

        Returns:
            True if charging pattern detected
        """
        if len(samples) < 6:
            return False

        # Look for direction changes (drain → charge → drain)
        direction_changes = 0
        window = max(2, len(samples) // 5)

        for i in range(window, len(samples) - window):
            prev_trend = samples[i].percentage - samples[i - window].percentage
            next_trend = samples[i + window].percentage - samples[i].percentage

            if (prev_trend > 2.0 and next_trend < -2.0) or \
               (prev_trend < -2.0 and next_trend > 2.0):
                direction_changes += 1

        # 2+ direction changes suggests solar/charging cycle
        return direction_changes >= 2

    def _calculate_battery_confidence(self, samples: List[BatterySample],
                                      time_span_hours: float) -> float:
        """Calculate confidence in battery prediction."""
        # More samples = more confidence
        sample_factor = min(1.0, len(samples) / 20.0)

        # Longer time span = more confidence
        span_factor = min(1.0, time_span_hours / 24.0)

        # Combine factors
        confidence = 0.3 + (0.4 * sample_factor) + (0.3 * span_factor)

        return min(0.95, confidence)

    def _detect_periodicity(self, timestamps: List[float]) -> Tuple[bool, Optional[float]]:
        """
        Detect if dropout timestamps show periodic behavior.

        Returns:
            (is_periodic, period_hours)
        """
        if len(timestamps) < MIN_DROPOUT_EVENTS + 1:
            return False, None

        # Calculate intervals between dropouts
        intervals = []
        for i in range(1, len(timestamps)):
            interval_hours = (timestamps[i] - timestamps[i - 1]) / 3600.0
            if interval_hours > 0.1:  # Filter out rapid-fire events
                intervals.append(interval_hours)

        if len(intervals) < 2:
            return False, None

        # Check if intervals are roughly consistent
        avg_interval = sum(intervals) / len(intervals)
        if avg_interval < 0.5:  # Less than 30 min average — too frequent for periodicity
            return False, None

        # Calculate coefficient of variation
        if avg_interval > 0:
            std_dev = math.sqrt(sum((i - avg_interval) ** 2 for i in intervals) / len(intervals))
            cv = std_dev / avg_interval
        else:
            cv = 1.0

        # CV < 0.4 suggests periodic behavior
        is_periodic = cv < 0.4 and len(intervals) >= 3
        period = avg_interval if is_periodic else None

        return is_periodic, period

    def _find_peak_hour(self, timestamps: List[float]) -> Optional[int]:
        """Find the hour of day with most dropout events."""
        if not timestamps:
            return None

        # Count dropouts by hour
        hour_counts = [0] * 24
        for ts in timestamps:
            dt = datetime.fromtimestamp(ts)
            hour_counts[dt.hour] += 1

        if max(hour_counts) == 0:
            return None

        # Only report peak if there's a clear pattern (max > 1.5x average)
        avg = sum(hour_counts) / 24.0
        peak_hour = hour_counts.index(max(hour_counts))

        if max(hour_counts) > avg * 1.5 and max(hour_counts) >= 2:
            return peak_hour
        return None

    def _calculate_reliability(self, uptime_pct: float, dropouts_per_day: float,
                               avg_downtime_min: float, sample_count: int) -> float:
        """
        Calculate reliability score (0-100).

        Weighted:
        - 40% uptime percentage
        - 30% dropout frequency (fewer = better)
        - 20% average downtime (shorter = better)
        - 10% data confidence
        """
        # Uptime score (0-100)
        uptime_score = uptime_pct

        # Frequency score (0 dropouts/day = 100, 10+/day = 0)
        freq_score = max(0.0, 100.0 * (1.0 - dropouts_per_day / 10.0))

        # Downtime score (0 min = 100, 60+ min = 0)
        downtime_score = max(0.0, 100.0 * (1.0 - avg_downtime_min / 60.0))

        # Confidence score based on sample count
        confidence_score = min(100.0, sample_count * 5.0)

        return (uptime_score * 0.40 +
                freq_score * 0.30 +
                downtime_score * 0.20 +
                confidence_score * 0.10)

    def _classify_dropout_behavior(self, dropouts_per_day: float,
                                    uptime_pct: float, is_periodic: bool,
                                    dropout_count: int) -> str:
        """Classify dropout behavior pattern."""
        if dropout_count < MIN_DROPOUT_EVENTS:
            return 'stable'

        if uptime_pct < 50.0 or dropouts_per_day > 5.0:
            return 'failing'

        if dropouts_per_day > 1.0 or is_periodic or uptime_pct < 90.0:
            return 'intermittent'

        return 'stable'


def voltage_to_percentage(voltage: float) -> float:
    """
    Convert battery voltage to approximate percentage.

    Uses linear interpolation between known voltage/percentage points
    for typical Li-ion/LiPo cells (3.7V nominal).

    Args:
        voltage: Battery voltage (typically 3.0-4.2V)

    Returns:
        Estimated percentage (0-100)
    """
    if voltage >= VOLTAGE_TO_PCT[0][0]:
        return 100.0
    if voltage <= VOLTAGE_TO_PCT[-1][0]:
        return 0.0

    for i in range(len(VOLTAGE_TO_PCT) - 1):
        v_high, pct_high = VOLTAGE_TO_PCT[i]
        v_low, pct_low = VOLTAGE_TO_PCT[i + 1]

        if v_low <= voltage <= v_high:
            # Linear interpolation
            ratio = (voltage - v_low) / (v_high - v_low)
            return pct_low + ratio * (pct_high - pct_low)

    return 50.0  # Fallback


def format_maintenance_report(predictor: MaintenancePredictor) -> str:
    """
    Format a maintenance report for TUI display.

    Args:
        predictor: MaintenancePredictor with recorded data

    Returns:
        Formatted string for terminal display
    """
    lines = ["=" * 60, "  PREDICTIVE MAINTENANCE REPORT", "=" * 60, ""]

    # Battery forecasts
    forecasts = predictor.get_all_forecasts()
    if forecasts:
        lines.append("BATTERY STATUS:")
        lines.append("-" * 40)
        for node_id, forecast in sorted(forecasts.items()):
            icon = _battery_icon(forecast.current_pct)
            status = f"  {icon} {node_id}: {forecast.current_pct:.0f}%"
            if forecast.current_voltage:
                status += f" ({forecast.current_voltage:.2f}V)"

            if forecast.trend == 'draining' and forecast.hours_to_shutdown:
                if forecast.hours_to_shutdown < 6:
                    status += f"  !! SHUTDOWN in {forecast.hours_to_shutdown:.0f}h"
                elif forecast.hours_to_critical:
                    status += f"  -> critical in {forecast.hours_to_critical:.0f}h"
            elif forecast.trend == 'charging':
                status += "  [charging]"
            elif forecast.trend == 'stable':
                status += "  [stable]"
            elif forecast.trend == 'insufficient_data':
                status += "  [awaiting data]"

            lines.append(status)
        lines.append("")

    # Dropout patterns
    patterns = predictor.get_all_patterns()
    if patterns:
        lines.append("NODE RELIABILITY:")
        lines.append("-" * 40)
        for node_id, pattern in sorted(patterns.items()):
            icon = _reliability_icon(pattern.reliability_score)
            status = f"  {icon} {node_id}: {pattern.uptime_pct:.0f}% uptime"

            if pattern.prediction == 'failing':
                status += f"  !! FAILING ({pattern.dropouts_per_day:.1f}/day)"
            elif pattern.prediction == 'intermittent':
                status += f"  ~ intermittent"
                if pattern.is_periodic and pattern.period_hours:
                    status += f" (every {pattern.period_hours:.0f}h)"
            elif pattern.prediction == 'stable':
                status += "  [stable]"

            lines.append(status)
        lines.append("")

    # Recommendations
    recommendations = predictor.get_maintenance_recommendations()
    if recommendations:
        lines.append("MAINTENANCE ACTIONS:")
        lines.append("-" * 40)
        for rec in recommendations:
            priority_icon = {'urgent': '!!!', 'soon': '!!', 'scheduled': '!', 'monitor': '?'}
            icon = priority_icon.get(rec.priority, ' ')
            lines.append(f"  [{icon}] {rec.node_id}: {rec.action}")
            lines.append(f"       Reason: {rec.reason}")
            if rec.deadline_hours:
                lines.append(f"       Deadline: {rec.deadline_hours:.0f}h")
            lines.append("")
    else:
        lines.append("  No maintenance actions needed at this time.")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def _battery_icon(pct: float) -> str:
    """Get text icon for battery level."""
    if pct > 75:
        return "[####]"
    elif pct > 50:
        return "[### ]"
    elif pct > 25:
        return "[##  ]"
    elif pct > 10:
        return "[#   ]"
    else:
        return "[!   ]"


def _reliability_icon(score: float) -> str:
    """Get text icon for reliability score."""
    if score >= 90:
        return "[OK]"
    elif score >= 70:
        return "[~ ]"
    elif score >= 50:
        return "[! ]"
    else:
        return "[!!]"
