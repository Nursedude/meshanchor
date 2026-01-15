"""
NanoVNA GTK4 Panel

Provides the user interface for NanoVNA antenna analyzer integration.
Displays SWR, impedance, and frequency response data.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# GTK imports
try:
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib, Pango
    HAS_GTK = True
except (ImportError, ValueError):
    HAS_GTK = False
    Gtk = None

# Import device module
from .nanovna_device import (
    NanoVNADevice, SweepResult, SweepPoint,
    format_impedance, format_swr, HAS_SERIAL
)


# Only define panel class if GTK is available
if HAS_GTK:
    _BaseClass = Gtk.Box
else:
    _BaseClass = object


class NanoVNAPanel(_BaseClass):
    """GTK4 Panel for NanoVNA antenna analyzer."""

    def __init__(self, settings: dict = None):
        """Initialize NanoVNA panel.

        Args:
            settings: Plugin settings dictionary.
        """
        if not HAS_GTK:
            raise RuntimeError("GTK4 not available - cannot create NanoVNA panel")

        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._settings = settings or {}
        self._device: Optional[NanoVNADevice] = None
        self._sweep_result: Optional[SweepResult] = None
        self._auto_refresh_timer = None
        self._pending_timers = []  # For cleanup

        self._build_ui()
        self._check_dependencies()

    def _build_ui(self):
        """Build the panel user interface."""
        # Header
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label="NanoVNA Antenna Analyzer")
        title.add_css_class("title-2")
        header.append(title)

        # Status indicator
        self._status_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        header.append(self._status_icon)
        self._status_label = Gtk.Label(label="Disconnected")
        header.append(self._status_label)
        header.set_hexpand(True)

        self.append(header)

        # Connection frame
        conn_frame = Gtk.Frame(label="Connection")
        conn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        conn_box.set_margin_start(10)
        conn_box.set_margin_end(10)
        conn_box.set_margin_top(10)
        conn_box.set_margin_bottom(10)

        # Port selection
        conn_box.append(Gtk.Label(label="Port:"))
        self._port_combo = Gtk.ComboBoxText()
        self._port_combo.set_hexpand(True)
        conn_box.append(self._port_combo)

        # Refresh ports button
        refresh_btn = Gtk.Button(label="Scan")
        refresh_btn.connect("clicked", self._on_scan_ports)
        conn_box.append(refresh_btn)

        # Connect button
        self._connect_btn = Gtk.Button(label="Connect")
        self._connect_btn.add_css_class("suggested-action")
        self._connect_btn.connect("clicked", self._on_connect)
        conn_box.append(self._connect_btn)

        conn_frame.set_child(conn_box)
        self.append(conn_frame)

        # Sweep configuration frame
        sweep_frame = Gtk.Frame(label="Sweep Configuration")
        sweep_grid = Gtk.Grid()
        sweep_grid.set_row_spacing(10)
        sweep_grid.set_column_spacing(10)
        sweep_grid.set_margin_start(10)
        sweep_grid.set_margin_end(10)
        sweep_grid.set_margin_top(10)
        sweep_grid.set_margin_bottom(10)

        # Start frequency
        sweep_grid.attach(Gtk.Label(label="Start (MHz):"), 0, 0, 1, 1)
        self._start_freq_spin = Gtk.SpinButton.new_with_range(1, 3000, 1)
        self._start_freq_spin.set_value(self._settings.get("frequency_start_mhz", 400))
        sweep_grid.attach(self._start_freq_spin, 1, 0, 1, 1)

        # Stop frequency
        sweep_grid.attach(Gtk.Label(label="Stop (MHz):"), 2, 0, 1, 1)
        self._stop_freq_spin = Gtk.SpinButton.new_with_range(1, 3000, 1)
        self._stop_freq_spin.set_value(self._settings.get("frequency_stop_mhz", 500))
        sweep_grid.attach(self._stop_freq_spin, 3, 0, 1, 1)

        # Points
        sweep_grid.attach(Gtk.Label(label="Points:"), 4, 0, 1, 1)
        self._points_spin = Gtk.SpinButton.new_with_range(11, 401, 10)
        self._points_spin.set_value(self._settings.get("sweep_points", 101))
        sweep_grid.attach(self._points_spin, 5, 0, 1, 1)

        # Sweep button
        self._sweep_btn = Gtk.Button(label="Sweep")
        self._sweep_btn.add_css_class("suggested-action")
        self._sweep_btn.set_sensitive(False)
        self._sweep_btn.connect("clicked", self._on_sweep)
        sweep_grid.attach(self._sweep_btn, 6, 0, 1, 1)

        # Auto-refresh toggle
        self._auto_refresh_check = Gtk.CheckButton(label="Auto-refresh")
        self._auto_refresh_check.set_active(self._settings.get("auto_refresh", True))
        self._auto_refresh_check.connect("toggled", self._on_auto_refresh_toggled)
        sweep_grid.attach(self._auto_refresh_check, 0, 1, 2, 1)

        sweep_frame.set_child(sweep_grid)
        self.append(sweep_frame)

        # Results frame with grid
        results_frame = Gtk.Frame(label="Measurement Results")
        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        results_box.set_margin_start(10)
        results_box.set_margin_end(10)
        results_box.set_margin_top(10)
        results_box.set_margin_bottom(10)

        # Key metrics grid
        metrics_grid = Gtk.Grid()
        metrics_grid.set_row_spacing(8)
        metrics_grid.set_column_spacing(20)

        # Create labeled value displays
        self._swr_label = self._create_metric_row(metrics_grid, "Min SWR:", 0)
        self._freq_label = self._create_metric_row(metrics_grid, "Best Match:", 1)
        self._impedance_label = self._create_metric_row(metrics_grid, "Impedance:", 2)
        self._return_loss_label = self._create_metric_row(metrics_grid, "Return Loss:", 3)

        results_box.append(metrics_grid)

        # Separator
        results_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Frequency band SWR summary
        band_label = Gtk.Label(label="Band Analysis")
        band_label.add_css_class("heading")
        band_label.set_halign(Gtk.Align.START)
        results_box.append(band_label)

        # Scrolled list of frequencies
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_min_content_height(200)

        # ListBox for frequency points
        self._freq_list = Gtk.ListBox()
        self._freq_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self._freq_list)
        results_box.append(scroll)

        results_frame.set_child(results_box)
        self.append(results_frame)

        # Initial port scan
        self._on_scan_ports(None)

    def _create_metric_row(self, grid: Gtk.Grid, label_text: str, row: int) -> Gtk.Label:
        """Create a label/value row in metrics grid."""
        label = Gtk.Label(label=label_text)
        label.set_halign(Gtk.Align.START)
        label.add_css_class("dim-label")
        grid.attach(label, 0, row, 1, 1)

        value = Gtk.Label(label="--")
        value.set_halign(Gtk.Align.START)
        value.add_css_class("monospace")
        grid.attach(value, 1, row, 1, 1)

        return value

    def _check_dependencies(self):
        """Check if required dependencies are installed."""
        if not HAS_SERIAL:
            self._status_label.set_label("pyserial not installed")
            self._connect_btn.set_sensitive(False)

    def _on_scan_ports(self, button):
        """Scan for NanoVNA devices."""
        self._port_combo.remove_all()

        if not HAS_SERIAL:
            self._port_combo.append_text("pyserial not installed")
            return

        devices = NanoVNADevice.find_devices()

        if devices:
            for dev in devices:
                self._port_combo.append_text(dev)
            self._port_combo.set_active(0)
        else:
            self._port_combo.append_text("No devices found")

    def _on_connect(self, button):
        """Connect/disconnect from device."""
        if self._device and self._device.is_connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """Connect to selected device."""
        port = self._port_combo.get_active_text()
        if not port or "not" in port.lower():
            return

        self._status_label.set_label("Connecting...")
        self._connect_btn.set_sensitive(False)

        def do_connect():
            device = NanoVNADevice(port=port)
            success = device.connect()

            GLib.idle_add(self._on_connect_result, device, success)

        threading.Thread(target=do_connect, daemon=True).start()

    def _on_connect_result(self, device: NanoVNADevice, success: bool):
        """Handle connection result on main thread."""
        self._connect_btn.set_sensitive(True)

        if success:
            self._device = device
            self._status_label.set_label(f"Connected: {device.device_version}")
            self._status_icon.set_from_icon_name("network-idle-symbolic")
            self._connect_btn.set_label("Disconnect")
            self._sweep_btn.set_sensitive(True)

            # Start auto-refresh if enabled
            if self._auto_refresh_check.get_active():
                self._start_auto_refresh()
        else:
            self._status_label.set_label("Connection failed")
            self._status_icon.set_from_icon_name("network-error-symbolic")

        return False

    def _disconnect(self):
        """Disconnect from device."""
        self._stop_auto_refresh()

        if self._device:
            self._device.disconnect()
            self._device = None

        self._status_label.set_label("Disconnected")
        self._status_icon.set_from_icon_name("network-offline-symbolic")
        self._connect_btn.set_label("Connect")
        self._sweep_btn.set_sensitive(False)

    def _on_sweep(self, button):
        """Perform frequency sweep."""
        if not self._device or not self._device.is_connected:
            return

        start_mhz = self._start_freq_spin.get_value()
        stop_mhz = self._stop_freq_spin.get_value()
        points = int(self._points_spin.get_value())

        start_hz = int(start_mhz * 1e6)
        stop_hz = int(stop_mhz * 1e6)

        self._sweep_btn.set_sensitive(False)
        self._status_label.set_label("Sweeping...")

        def do_sweep():
            result = self._device.sweep(start_hz, stop_hz, points)
            GLib.idle_add(self._on_sweep_result, result)

        threading.Thread(target=do_sweep, daemon=True).start()

    def _on_sweep_result(self, result: SweepResult):
        """Handle sweep result on main thread."""
        self._sweep_result = result
        self._sweep_btn.set_sensitive(True)
        self._status_label.set_label(f"Sweep complete: {len(result.points)} points")

        self._update_display()
        return False

    def _update_display(self):
        """Update display with sweep results."""
        if not self._sweep_result or not self._sweep_result.points:
            return

        result = self._sweep_result

        # Update key metrics
        min_swr, min_freq = result.min_swr
        self._swr_label.set_label(format_swr(min_swr))
        self._freq_label.set_label(f"{min_freq:.3f} MHz")

        # Find the point at minimum SWR
        min_point = min(result.points, key=lambda p: p.swr)
        self._impedance_label.set_label(format_impedance(min_point.impedance))
        self._return_loss_label.set_label(f"{min_point.return_loss_db:.1f} dB")

        # Color the SWR based on value
        if min_swr < 1.5:
            self._swr_label.remove_css_class("warning")
            self._swr_label.remove_css_class("error")
            self._swr_label.add_css_class("success")
        elif min_swr < 3.0:
            self._swr_label.remove_css_class("success")
            self._swr_label.remove_css_class("error")
            self._swr_label.add_css_class("warning")
        else:
            self._swr_label.remove_css_class("success")
            self._swr_label.remove_css_class("warning")
            self._swr_label.add_css_class("error")

        # Update frequency list
        self._update_freq_list()

    def _update_freq_list(self):
        """Update the frequency point list."""
        # Clear existing rows
        while True:
            row = self._freq_list.get_row_at_index(0)
            if row:
                self._freq_list.remove(row)
            else:
                break

        if not self._sweep_result:
            return

        # Add rows for key frequencies (every 10th point to keep list manageable)
        points = self._sweep_result.points
        step = max(1, len(points) // 20)

        for i in range(0, len(points), step):
            point = points[i]
            row = self._create_freq_row(point)
            self._freq_list.append(row)

    def _create_freq_row(self, point: SweepPoint) -> Gtk.ListBoxRow:
        """Create a row for frequency list."""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        box.set_margin_start(5)
        box.set_margin_end(5)
        box.set_margin_top(3)
        box.set_margin_bottom(3)

        # Frequency
        freq_label = Gtk.Label(label=f"{point.frequency_mhz:.3f} MHz")
        freq_label.set_width_chars(12)
        freq_label.set_halign(Gtk.Align.START)
        box.append(freq_label)

        # SWR
        swr_label = Gtk.Label(label=format_swr(point.swr))
        swr_label.set_width_chars(8)
        box.append(swr_label)

        # Color based on SWR
        if point.swr < 1.5:
            swr_label.add_css_class("success")
        elif point.swr < 3.0:
            swr_label.add_css_class("warning")
        else:
            swr_label.add_css_class("error")

        # Impedance
        z_label = Gtk.Label(label=format_impedance(point.impedance))
        z_label.set_width_chars(18)
        box.append(z_label)

        # Return loss
        rl_label = Gtk.Label(label=f"{point.return_loss_db:.1f} dB")
        rl_label.set_width_chars(10)
        box.append(rl_label)

        row.set_child(box)
        return row

    def _on_auto_refresh_toggled(self, check):
        """Handle auto-refresh toggle."""
        if check.get_active() and self._device and self._device.is_connected:
            self._start_auto_refresh()
        else:
            self._stop_auto_refresh()

    def _start_auto_refresh(self):
        """Start auto-refresh timer."""
        if self._auto_refresh_timer:
            return

        interval = self._settings.get("refresh_interval_ms", 2000)
        self._auto_refresh_timer = GLib.timeout_add(interval, self._on_auto_refresh_tick)
        self._pending_timers.append(self._auto_refresh_timer)

    def _stop_auto_refresh(self):
        """Stop auto-refresh timer."""
        if self._auto_refresh_timer:
            GLib.source_remove(self._auto_refresh_timer)
            if self._auto_refresh_timer in self._pending_timers:
                self._pending_timers.remove(self._auto_refresh_timer)
            self._auto_refresh_timer = None

    def _on_auto_refresh_tick(self):
        """Auto-refresh timer callback."""
        if self._device and self._device.is_connected:
            self._on_sweep(None)
            return True  # Continue timer
        return False  # Stop timer

    def get_settings(self) -> dict:
        """Get current settings from UI."""
        return {
            "frequency_start_mhz": self._start_freq_spin.get_value(),
            "frequency_stop_mhz": self._stop_freq_spin.get_value(),
            "sweep_points": int(self._points_spin.get_value()),
            "auto_refresh": self._auto_refresh_check.get_active(),
        }

    def cleanup(self):
        """Clean up resources."""
        logger.debug("[NanoVNA] Panel cleanup")

        # Stop timers
        self._stop_auto_refresh()
        for timer_id in self._pending_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._pending_timers.clear()

        # Disconnect device
        if self._device:
            self._device.disconnect()
            self._device = None
