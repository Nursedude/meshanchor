"""
Mesh Tools Panel - Consolidated mesh network tools

Combines:
- MeshBot (BBS, games, inventory, weather)
- Node Map (network visualization)
- Diagnostics (health monitoring, event log)
- Sensors (telemetry display)

With shared resizable log output at bottom.

Refactored to use tab mixins for maintainability (v0.4.8).
Each tab is implemented in its own module:
- mesh_tools_meshbot.py
- mesh_tools_nodemap.py
- mesh_tools_diagnostics.py
- mesh_tools_sensors.py
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import threading
import subprocess
import os
from pathlib import Path
from datetime import datetime

# Import UI standards
try:
    from utils.gtk_helpers import (
        UI, create_panel_header, create_standard_frame,
        ResizableLogViewer, ResizablePanedLayout, StatusIndicator
    )
    HAS_UI_HELPERS = True
except ImportError:
    HAS_UI_HELPERS = False

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import path utilities
from utils.paths import get_real_user_home

# Import settings manager
try:
    from utils.common import SettingsManager
    HAS_SETTINGS = True
except ImportError:
    HAS_SETTINGS = False

# Import diagnostics
try:
    from utils.network_diagnostics import (
        get_diagnostics, EventCategory, EventSeverity, HealthStatus
    )
    HAS_DIAGNOSTICS = True
except ImportError:
    HAS_DIAGNOSTICS = False

# Import tab mixins
from .mesh_tools_meshbot import MeshBotTabMixin
from .mesh_tools_nodemap import NodeMapTabMixin
from .mesh_tools_diagnostics import DiagnosticsTabMixin
from .mesh_tools_sensors import SensorsTabMixin


class MeshToolsPanel(
    MeshBotTabMixin,
    NodeMapTabMixin,
    DiagnosticsTabMixin,
    SensorsTabMixin,
    Gtk.Box
):
    """
    Consolidated mesh network tools panel.

    Provides sub-tabs for MeshBot, Node Map, Diagnostics, and Sensors
    with a shared resizable log output at the bottom.

    Tab implementations are provided by mixin classes for maintainability.
    """

    SETTINGS_DEFAULTS = {
        "meshbot_path": "/opt/meshing-around",
        "log_position": 300,  # Paned position
        "active_tab": 0,
    }

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main_window = main_window

        # Apply standard margins
        margin = UI.MARGIN_PANEL if HAS_UI_HELPERS else 20
        self.set_margin_start(margin)
        self.set_margin_end(margin)
        self.set_margin_top(margin)
        self.set_margin_bottom(margin)

        # Load settings
        if HAS_SETTINGS:
            self._settings_mgr = SettingsManager("mesh_tools", defaults=self.SETTINGS_DEFAULTS)
            self._settings = self._settings_mgr.all()
        else:
            self._settings = self.SETTINGS_DEFAULTS.copy()

        # Bot process tracking
        self._bot_process = None
        self._log_timer_id = None
        self._pending_timers = []  # Track timers for cleanup

        self._build_ui()

        # Initial status check
        self._schedule_timer(500, self._check_all_status)

    # =========================================================================
    # Timer Management
    # =========================================================================

    def _schedule_timer(self, delay_ms: int, callback, *args) -> int:
        """Schedule a timer and track it for cleanup."""
        if args:
            timer_id = GLib.timeout_add(delay_ms, callback, *args)
        else:
            timer_id = GLib.timeout_add(delay_ms, callback)
        self._pending_timers.append(timer_id)
        return timer_id

    def _cancel_timers(self):
        """Cancel all pending timers."""
        for timer_id in self._pending_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._pending_timers.clear()

    # =========================================================================
    # Settings Management
    # =========================================================================

    def _save_settings(self):
        """Save settings"""
        if HAS_SETTINGS:
            self._settings_mgr.update(self._settings)
            self._settings_mgr.save()

    # =========================================================================
    # UI Building
    # =========================================================================

    def _build_ui(self):
        """Build the main UI with paned layout"""
        # Header
        if HAS_UI_HELPERS:
            header = create_panel_header(
                "Mesh Tools",
                "MeshBot, Node Map, and Network Diagnostics",
                "network-workgroup-symbolic"
            )
        else:
            header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            title = Gtk.Label(label="Mesh Tools")
            title.add_css_class("title-1")
            title.set_xalign(0)
            header.append(title)

        self.append(header)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Main paned layout: tabs on top, log on bottom
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self._paned.set_wide_handle(True)
        self._paned.set_vexpand(True)

        # Top: Notebook with tabs
        self._notebook = Gtk.Notebook()
        self._notebook.set_tab_pos(Gtk.PositionType.TOP)

        # Add tabs from mixins
        self._add_meshbot_tab()
        self._add_map_tab()
        self._add_diagnostics_tab()
        self._add_sensors_tab()

        self._paned.set_start_child(self._notebook)

        # Bottom: Shared log viewer
        self._build_log_viewer()
        self._paned.set_end_child(self._log_frame)

        # Restore paned position
        self._paned.set_position(self._settings.get("log_position", 300))
        self._paned.connect("notify::position", self._on_paned_moved)

        self.append(self._paned)

    def _build_log_viewer(self):
        """Build the shared log viewer at the bottom"""
        self._log_frame = Gtk.Frame()
        self._log_frame.set_label("Output Log")

        log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Controls bar
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        controls.set_margin_start(10)
        controls.set_margin_end(10)
        controls.set_margin_top(5)
        controls.set_margin_bottom(5)

        # Source selector
        controls.append(Gtk.Label(label="Source:"))
        self._log_source = Gtk.ComboBoxText()
        self._log_source.append("meshbot", "MeshBot Output")
        self._log_source.append("diagnostics", "Diagnostics Events")
        self._log_source.append("system", "System Log")
        self._log_source.set_active_id("meshbot")
        self._log_source.connect("changed", self._on_log_source_changed)
        controls.append(self._log_source)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        controls.append(spacer)

        # Auto-scroll toggle
        self._auto_scroll = Gtk.CheckButton(label="Auto-scroll")
        self._auto_scroll.set_active(True)
        controls.append(self._auto_scroll)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh Log")
        refresh_btn.connect("clicked", self._on_refresh_log)
        controls.append(refresh_btn)

        # Clear button
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.connect("clicked", self._on_clear_log)
        controls.append(clear_btn)

        log_box.append(controls)
        log_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Log text view
        self._log_text = Gtk.TextView()
        self._log_text.set_editable(False)
        self._log_text.set_monospace(True)
        self._log_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_text.set_cursor_visible(False)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_vexpand(True)
        log_scroll.set_min_content_height(100)
        log_scroll.set_child(self._log_text)
        self._log_scroll = log_scroll

        log_box.append(log_scroll)
        self._log_frame.set_child(log_box)

    def _on_paned_moved(self, paned, param):
        """Save paned position when moved"""
        self._settings["log_position"] = paned.get_position()
        self._save_settings()

    # =========================================================================
    # Status Checking (Orchestrates tab checks)
    # =========================================================================

    def _check_all_status(self):
        """Check status of all services"""
        self._check_meshbot_status()
        self._check_meshtasticd_status()
        self._update_health_cards()
        return False  # Don't repeat

    # =========================================================================
    # Log Management (Shared by all tabs)
    # =========================================================================

    def _on_log_source_changed(self, combo):
        """Handle log source change"""
        source = combo.get_active_id()
        self._log_message(f"Switched to {source} log source")

    def _on_refresh_log(self, button):
        """Refresh current log based on selected source"""
        source = self._log_source.get_active_id()
        self._log_message(f"Refreshing {source} log...")

        def fetch_log():
            content = ""
            try:
                if source == "meshbot":
                    # Read MeshBot log
                    meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
                    log_file = Path(meshbot_path) / "logs" / "meshbot.log"
                    if log_file.exists():
                        content = log_file.read_text()[-10000:]  # Last 10KB
                    else:
                        content = f"MeshBot log not found at {log_file}"

                elif source == "diagnostics":
                    # Get recent diagnostic events
                    if HAS_DIAGNOSTICS:
                        diag = get_diagnostics()
                        events = diag.get_events(limit=50)
                        lines = []
                        for e in events:
                            lines.append(f"[{e.timestamp.strftime('%H:%M:%S')}] {e.category.value}: {e.message}")
                        content = "\n".join(lines) if lines else "No diagnostic events"
                    else:
                        content = "Diagnostics module not available"

                elif source == "system":
                    # Read system log for meshforge/meshtastic
                    result = subprocess.run(
                        ['journalctl', '-u', 'meshtasticd', '-n', '100', '--no-pager'],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    content = result.stdout if result.returncode == 0 else "No system log available"

            except Exception as e:
                content = f"Error fetching log: {e}"

            GLib.idle_add(self._set_log_text, content)

        threading.Thread(target=fetch_log, daemon=True).start()

    def _on_clear_log(self, button):
        """Clear log output"""
        buffer = self._log_text.get_buffer()
        buffer.set_text("")

    def _log_message(self, message: str):
        """Add message to log output"""
        buffer = self._log_text.get_buffer()
        end_iter = buffer.get_end_iter()
        timestamp = datetime.now().strftime("%H:%M:%S")
        buffer.insert(end_iter, f"[{timestamp}] {message}\n")

        if self._auto_scroll.get_active():
            end_iter = buffer.get_end_iter()
            self._log_text.scroll_to_iter(end_iter, 0, False, 0, 0)

    def _set_log_text(self, text: str):
        """Set log text (replace all)"""
        buffer = self._log_text.get_buffer()
        buffer.set_text(text)

    # =========================================================================
    # Utility Methods (Shared by all tabs)
    # =========================================================================

    def _open_folder(self, path: str):
        """Open folder in file manager"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        try:
            subprocess.Popen(
                ['sudo', '-u', real_user, 'xdg-open', path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        except Exception as e:
            self._log_message(f"Error opening folder: {e}")

    def _open_url(self, url: str):
        """Open URL in browser - runs in thread to avoid blocking GTK"""
        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

        def do_open():
            # Try multiple methods to open browser
            browsers = [
                ['sudo', '-u', real_user, 'xdg-open', url],
                ['sudo', '-u', real_user, 'firefox', url],
                ['sudo', '-u', real_user, 'chromium-browser', url],
                ['sudo', '-u', real_user, 'chromium', url],
                ['sudo', '-u', real_user, 'google-chrome', url],
            ]

            for cmd in browsers:
                try:
                    # Use Popen with full detachment
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                        env={**os.environ, 'DISPLAY': os.environ.get('DISPLAY', ':0')}
                    )
                    GLib.idle_add(self._log_message, f"Opening {url}")
                    GLib.idle_add(self._log_message, f"(Note: HTTPS uses self-signed cert - accept in browser)")
                    return
                except FileNotFoundError:
                    continue
                except Exception as e:
                    GLib.idle_add(self._log_message, f"Browser error: {e}")
                    continue

            GLib.idle_add(self._log_message, f"Could not open browser for {url}")
            GLib.idle_add(self._log_message, "Copy URL and open manually in browser")

        threading.Thread(target=do_open, daemon=True).start()

    # =========================================================================
    # Cleanup
    # =========================================================================

    def cleanup(self):
        """Clean up resources"""
        self._cancel_timers()
        if self._log_timer_id:
            GLib.source_remove(self._log_timer_id)
            self._log_timer_id = None
