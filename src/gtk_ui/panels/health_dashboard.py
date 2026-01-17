"""
Health Dashboard Panel - Unified network health view with predictive alerts.

Sprint B: Provides a single pane of glass for network health monitoring,
integrating service status, network metrics, and predictive analytics.

Uses:
- utils/service_check.py for service status (SINGLE SOURCE OF TRUTH)
- utils/analytics.py for trend data
- utils/diagnostic_engine.py for predictive alerts
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Import service checker (SINGLE SOURCE OF TRUTH)
try:
    from utils.service_check import check_service, KNOWN_SERVICES
    HAS_SERVICE_CHECK = True
except ImportError:
    check_service = None
    KNOWN_SERVICES = {}
    HAS_SERVICE_CHECK = False

# Import analytics for trends
try:
    from utils.analytics import get_predictive_analyzer, get_analytics_store
    HAS_ANALYTICS = True
except ImportError:
    get_predictive_analyzer = None
    get_analytics_store = None
    HAS_ANALYTICS = False

# Import diagnostic engine for predictive alerts
try:
    from utils.diagnostic_engine import get_diagnostic_engine, Category
    HAS_DIAGNOSTICS = True
except ImportError:
    get_diagnostic_engine = None
    Category = None
    HAS_DIAGNOSTICS = False


class HealthDashboardPanel(Gtk.Box):
    """
    Unified health dashboard showing network status at a glance.

    Features:
    - Service status with agreement indicators
    - Network health metrics (SNR, RSSI, node count)
    - Predictive alerts with time-to-critical estimates
    - Network forecast (24-hour outlook)
    """

    # Services to monitor
    MONITORED_SERVICES = ['meshtasticd', 'rnsd', 'hamclock']

    # Refresh interval in milliseconds
    REFRESH_INTERVAL_MS = 30000  # 30 seconds

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.main_window = main_window
        self._refresh_timer_id = None
        self._init_timer_id = None

        self.set_margin_start(16)
        self.set_margin_end(16)
        self.set_margin_top(16)
        self.set_margin_bottom(16)

        self._build_ui()

        # Defer initial load
        self._init_timer_id = GLib.timeout_add(500, self._initial_refresh)
        self.connect("unrealize", self._on_unrealize)

    def _on_unrealize(self, widget):
        """Clean up timers when panel is destroyed."""
        self.cleanup()

    def cleanup(self):
        """
        Clean up resources when panel is removed or destroyed.

        Required by all panels to prevent resource leaks.
        Called automatically on unrealize and can be called manually.
        """
        if self._refresh_timer_id:
            GLib.source_remove(self._refresh_timer_id)
            self._refresh_timer_id = None
        if self._init_timer_id:
            GLib.source_remove(self._init_timer_id)
            self._init_timer_id = None

    def _build_ui(self):
        """Build the health dashboard UI."""
        # Header with title and refresh button
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        title = Gtk.Label(label="Network Health")
        title.add_css_class("title-1")
        title.set_xalign(0)
        title.set_hexpand(True)
        header.append(title)

        # Last updated label
        self.last_updated_label = Gtk.Label(label="")
        self.last_updated_label.add_css_class("dim-label")
        header.append(self.last_updated_label)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh health data")
        refresh_btn.connect("clicked", lambda b: self._refresh_data())
        header.append(refresh_btn)

        self.append(header)

        # Scrollable content area
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # === SERVICES SECTION ===
        services_frame = self._create_section_frame("Services")
        self.services_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        services_frame.set_child(self.services_box)
        content.append(services_frame)

        # === NETWORK HEALTH SECTION ===
        health_frame = self._create_section_frame("Network Metrics")
        self.health_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        health_frame.set_child(self.health_box)
        content.append(health_frame)

        # === FORECAST SECTION ===
        forecast_frame = self._create_section_frame("24-Hour Forecast")
        self.forecast_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        forecast_frame.set_child(self.forecast_box)
        content.append(forecast_frame)

        # === PREDICTIVE ALERTS SECTION ===
        alerts_frame = self._create_section_frame("Predictive Alerts")
        self.alerts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        alerts_frame.set_child(self.alerts_box)
        content.append(alerts_frame)

        # === RECENT ISSUES SECTION ===
        issues_frame = self._create_section_frame("Recent Issues (24h)")
        self.issues_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        issues_frame.set_child(self.issues_box)
        content.append(issues_frame)

        scroll.set_child(content)
        self.append(scroll)

    def _create_section_frame(self, title: str) -> Gtk.Frame:
        """Create a styled section frame."""
        frame = Gtk.Frame()
        frame.add_css_class("card")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(12)

        label = Gtk.Label(label=title)
        label.add_css_class("heading")
        label.set_xalign(0)
        box.append(label)

        frame.set_child(box)
        return frame

    def _initial_refresh(self) -> bool:
        """Initial data load."""
        self._init_timer_id = None
        self._refresh_data()
        # Start periodic refresh
        self._refresh_timer_id = GLib.timeout_add(
            self.REFRESH_INTERVAL_MS, self._periodic_refresh
        )
        return False  # Don't repeat

    def _periodic_refresh(self) -> bool:
        """Periodic refresh callback."""
        self._refresh_data()
        return True  # Continue timer

    def _refresh_data(self):
        """Refresh all health data in background thread."""
        self.last_updated_label.set_text("Refreshing...")

        def do_refresh():
            data = {
                'services': self._check_services(),
                'health': self._get_network_health(),
                'forecast': self._get_forecast(),
                'alerts': self._get_predictive_alerts(),
                'issues': self._get_recent_issues(),
            }
            GLib.idle_add(self._update_ui, data)

        threading.Thread(target=do_refresh, daemon=True).start()

    def _check_services(self) -> List[Dict]:
        """Check status of monitored services."""
        results = []
        if not HAS_SERVICE_CHECK:
            return results

        for name in self.MONITORED_SERVICES:
            status = check_service(name)
            results.append({
                'name': name,
                'available': status.available,
                'state': status.state.value if status.state else 'unknown',
                'details': status.details or '',
            })
        return results

    def _get_network_health(self) -> Dict:
        """Get current network health metrics."""
        if not HAS_ANALYTICS:
            return {}

        try:
            store = get_analytics_store()
            history = store.get_network_health_history(hours=4)

            if not history:
                return {'has_data': False}

            latest = history[0]
            return {
                'has_data': True,
                'online_nodes': latest.online_nodes,
                'offline_nodes': latest.offline_nodes,
                'avg_snr_db': latest.avg_snr_db,
                'avg_rssi_dbm': latest.avg_rssi_dbm,
                'packet_success_rate': latest.packet_success_rate,
            }
        except Exception as e:
            logger.warning(f"Failed to get network health: {e}")
            return {'has_data': False, 'error': str(e)}

    def _get_forecast(self) -> Dict:
        """Get 24-hour network forecast."""
        if not HAS_ANALYTICS:
            return {'has_forecast': False}

        try:
            analyzer = get_predictive_analyzer()
            return analyzer.get_network_forecast(hours_ahead=24)
        except Exception as e:
            logger.warning(f"Failed to get forecast: {e}")
            return {'has_forecast': False, 'reason': str(e)}

    def _get_predictive_alerts(self) -> List[Dict]:
        """Get predictive alerts from analytics."""
        if not HAS_ANALYTICS:
            return []

        try:
            analyzer = get_predictive_analyzer()
            alerts = analyzer.analyze_all()
            return [
                {
                    'type': a.alert_type,
                    'severity': a.severity,
                    'message': a.message,
                    'predicted_time_hours': a.predicted_time_hours,
                    'confidence': a.confidence,
                    'suggestions': a.suggestions,
                }
                for a in alerts
            ]
        except Exception as e:
            logger.warning(f"Failed to get predictive alerts: {e}")
            return []

    def _get_recent_issues(self) -> List[Dict]:
        """Get recent diagnostic issues."""
        if not HAS_DIAGNOSTICS:
            return []

        try:
            engine = get_diagnostic_engine()
            return engine.get_history(limit=10, since_hours=24)
        except Exception as e:
            logger.warning(f"Failed to get recent issues: {e}")
            return []

    def _update_ui(self, data: Dict):
        """Update UI with refreshed data (called on main thread)."""
        self._update_services(data.get('services', []))
        self._update_health(data.get('health', {}))
        self._update_forecast(data.get('forecast', {}))
        self._update_alerts(data.get('alerts', []))
        self._update_issues(data.get('issues', []))

        self.last_updated_label.set_text(
            f"Updated: {datetime.now().strftime('%H:%M:%S')}"
        )

    def _update_services(self, services: List[Dict]):
        """Update services section."""
        # Clear existing
        while child := self.services_box.get_first_child():
            self.services_box.remove(child)

        if not services:
            label = Gtk.Label(label="Service monitoring unavailable")
            label.add_css_class("dim-label")
            self.services_box.append(label)
            return

        for svc in services:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            # Status indicator
            if svc['available']:
                icon = Gtk.Label(label="●")
                icon.add_css_class("success")
            else:
                icon = Gtk.Label(label="●")
                icon.add_css_class("error")
            row.append(icon)

            # Service name
            name = Gtk.Label(label=svc['name'])
            name.set_xalign(0)
            name.set_hexpand(True)
            row.append(name)

            # Status
            status = Gtk.Label(label=svc['state'].upper())
            status.add_css_class("caption")
            row.append(status)

            self.services_box.append(row)

    def _update_health(self, health: Dict):
        """Update network health section."""
        # Clear existing
        while child := self.health_box.get_first_child():
            self.health_box.remove(child)

        if not health.get('has_data'):
            label = Gtk.Label(label="Collecting network data...")
            label.add_css_class("dim-label")
            self.health_box.append(label)
            return

        # Create metrics grid
        metrics = [
            ("Nodes Online", str(health.get('online_nodes', 0))),
            ("Nodes Offline", str(health.get('offline_nodes', 0))),
            ("Avg SNR", f"{health.get('avg_snr_db', 0):.1f} dB"),
            ("Avg RSSI", f"{health.get('avg_rssi_dbm', 0):.1f} dBm"),
            ("Packet Success", f"{(health.get('packet_success_rate', 0) * 100):.0f}%"),
        ]

        grid = Gtk.Grid()
        grid.set_column_spacing(20)
        grid.set_row_spacing(4)

        for i, (label_text, value_text) in enumerate(metrics):
            row = i // 2
            col = (i % 2) * 2

            label = Gtk.Label(label=label_text + ":")
            label.set_xalign(0)
            label.add_css_class("dim-label")
            grid.attach(label, col, row, 1, 1)

            value = Gtk.Label(label=value_text)
            value.set_xalign(0)
            value.add_css_class("heading")
            grid.attach(value, col + 1, row, 1, 1)

        self.health_box.append(grid)

    def _update_forecast(self, forecast: Dict):
        """Update forecast section."""
        # Clear existing
        while child := self.forecast_box.get_first_child():
            self.forecast_box.remove(child)

        if not forecast.get('has_forecast'):
            label = Gtk.Label(label=forecast.get('reason', 'Insufficient data for forecast'))
            label.add_css_class("dim-label")
            self.forecast_box.append(label)
            return

        # Outlook indicator
        outlook = forecast.get('outlook', 'unknown')
        outlook_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        outlook_label = Gtk.Label(label="Outlook:")
        outlook_label.set_xalign(0)
        outlook_row.append(outlook_label)

        outlook_value = Gtk.Label(label=outlook.upper())
        if outlook == 'degrading':
            outlook_value.add_css_class("error")
        elif outlook == 'improving':
            outlook_value.add_css_class("success")
        else:
            outlook_value.add_css_class("warning")
        outlook_row.append(outlook_value)

        confidence = forecast.get('confidence', 0)
        conf_label = Gtk.Label(label=f"({confidence:.0%} confidence)")
        conf_label.add_css_class("dim-label")
        outlook_row.append(conf_label)

        self.forecast_box.append(outlook_row)

        # Trend indicators
        trends = forecast.get('trends', {})
        if trends:
            trends_text = []
            if snr_trend := trends.get('snr_per_hour'):
                direction = "+" if snr_trend > 0 else ""
                trends_text.append(f"SNR: {direction}{snr_trend:.2f} dB/hr")
            if node_trend := trends.get('nodes_per_hour'):
                direction = "+" if node_trend > 0 else ""
                trends_text.append(f"Nodes: {direction}{node_trend:.1f}/hr")

            if trends_text:
                trend_label = Gtk.Label(label="  ".join(trends_text))
                trend_label.add_css_class("caption")
                self.forecast_box.append(trend_label)

    def _update_alerts(self, alerts: List[Dict]):
        """Update predictive alerts section."""
        # Clear existing
        while child := self.alerts_box.get_first_child():
            self.alerts_box.remove(child)

        if not alerts:
            label = Gtk.Label(label="No predicted issues")
            label.add_css_class("success")
            self.alerts_box.append(label)
            return

        for alert in alerts[:5]:  # Show max 5 alerts
            alert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            # Alert header with severity
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            severity = alert.get('severity', 'warning')
            icon = "⚠" if severity == 'warning' else "⛔"
            icon_label = Gtk.Label(label=icon)
            header.append(icon_label)

            message = Gtk.Label(label=alert.get('message', 'Unknown alert'))
            message.set_xalign(0)
            message.set_hexpand(True)
            message.set_wrap(True)
            if severity == 'critical':
                message.add_css_class("error")
            else:
                message.add_css_class("warning")
            header.append(message)

            alert_box.append(header)

            # Time prediction if available
            time_hours = alert.get('predicted_time_hours')
            if time_hours:
                if time_hours < 24:
                    time_text = f"~{time_hours:.0f} hours until critical"
                else:
                    time_text = f"~{time_hours/24:.1f} days until critical"
                time_label = Gtk.Label(label=time_text)
                time_label.set_xalign(0)
                time_label.set_margin_start(24)
                time_label.add_css_class("dim-label")
                alert_box.append(time_label)

            # First suggestion
            suggestions = alert.get('suggestions', [])
            if suggestions:
                suggestion = Gtk.Label(label=f"→ {suggestions[0]}")
                suggestion.set_xalign(0)
                suggestion.set_margin_start(24)
                suggestion.add_css_class("caption")
                alert_box.append(suggestion)

            self.alerts_box.append(alert_box)

    def _update_issues(self, issues: List[Dict]):
        """Update recent issues section."""
        # Clear existing
        while child := self.issues_box.get_first_child():
            self.issues_box.remove(child)

        if not issues:
            label = Gtk.Label(label="No issues in last 24 hours")
            label.add_css_class("success")
            self.issues_box.append(label)
            return

        for issue in issues[:8]:  # Show max 8 issues
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            # Timestamp
            timestamp = issue.get('timestamp', '')
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    time_str = dt.strftime('%H:%M')
                except ValueError:
                    time_str = '??:??'
            else:
                time_str = '??:??'

            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("dim-label")
            time_label.set_size_request(50, -1)
            row.append(time_label)

            # Severity icon
            severity = issue.get('symptom_severity', 'warning')
            if severity == 'critical':
                icon = "⛔"
            elif severity == 'error':
                icon = "❌"
            elif severity == 'warning':
                icon = "⚠"
            else:
                icon = "ℹ"
            icon_label = Gtk.Label(label=icon)
            row.append(icon_label)

            # Message
            message = issue.get('symptom_message', 'Unknown issue')[:50]
            msg_label = Gtk.Label(label=message)
            msg_label.set_xalign(0)
            msg_label.set_hexpand(True)
            msg_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
            row.append(msg_label)

            self.issues_box.append(row)
