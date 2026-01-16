"""
MeshForge - GTK4 Application
LoRa Mesh Network Development & Operations Suite
Main application entry point and window management
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Adw, GLib, Gio, Gdk
import sys
import os
import shutil
import subprocess
import threading
import logging
from pathlib import Path

# Set up logging for GTK app diagnostics
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('/tmp/meshforge-gtk.log'),
    ]
)
logger = logging.getLogger('gtk_app')

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import centralized path utility
from utils.paths import get_real_user_home

from __version__ import __version__, get_full_version, __app_name__

# Edition detection
try:
    from core.edition import Edition, detect_edition, has_feature, get_edition_info
    EDITION_AVAILABLE = True
except ImportError:
    EDITION_AVAILABLE = False
    class Edition:
        PRO = "pro"
        AMATEUR = "amateur"
        IO = "io"
    def detect_edition():
        return Edition.PRO
    def has_feature(f):
        return True
    def get_edition_info():
        return {"edition": "pro", "display_name": "MeshForge PRO"}


class MeshForgeApp(Adw.Application):
    """MeshForge GTK4 Application"""

    def __init__(self):
        super().__init__(
            application_id='org.meshforge.app',
            # NON_UNIQUE allows running without D-Bus registration
            # This fixes "Failed to register: Timeout was reached" when running as root
            flags=Gio.ApplicationFlags.NON_UNIQUE
        )
        self.window = None
        self._icons_registered = False
        self.connect('activate', self.on_activate)

    def _register_custom_icons(self):
        """Register custom icons from assets directory.

        Installs to user's local icon theme (~/.local/share/icons/) which
        works reliably even when running with sudo. Fixes ownership if needed.
        """
        if self._icons_registered:
            return

        # Find assets directory relative to source
        src_dir = Path(__file__).parent.parent.parent
        assets_dir = src_dir / 'assets'
        icon_src = assets_dir / 'meshforge-icon.svg'

        if assets_dir.exists():
            display = Gdk.Display.get_default()
            if display:
                icon_theme = Gtk.IconTheme.get_for_display(display)

                # Install to user's local icon theme (works without root)
                self._install_icon_to_user_theme(icon_src)

                # Add user's local icons to theme search path
                user_home = get_real_user_home()
                local_icons = user_home / '.local' / 'share' / 'icons' / 'hicolor'
                if local_icons.exists():
                    icon_theme.add_search_path(str(local_icons))

                # Add assets directory as fallback
                icon_theme.add_search_path(str(assets_dir))
                self._icons_registered = True

                # Set default window icon
                Gtk.Window.set_default_icon_name("org.meshforge.app")

    def _install_icon_to_user_theme(self, icon_src: Path):
        """Install icon to user's local hicolor icon theme.

        Uses get_real_user_home() to handle running with sudo correctly.
        Fixes file ownership when running as root for another user.
        """
        if not icon_src.exists():
            return

        import shutil

        user_home = get_real_user_home()
        local_icon_dir = user_home / '.local' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps'
        target_icon = local_icon_dir / 'org.meshforge.app.svg'

        def should_update(src: Path, dest: Path) -> bool:
            if not dest.exists():
                return True
            return src.stat().st_mtime > dest.stat().st_mtime

        if not should_update(icon_src, target_icon):
            return

        try:
            local_icon_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(icon_src), str(target_icon))
            logger.debug(f"Installed icon: {target_icon}")

            # Fix ownership if running as root for another user
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and os.geteuid() == 0:
                import pwd
                try:
                    user_info = pwd.getpwnam(sudo_user)
                    icons_base = user_home / '.local' / 'share' / 'icons'
                    for dirpath, dirnames, filenames in os.walk(str(icons_base)):
                        os.chown(dirpath, user_info.pw_uid, user_info.pw_gid)
                        for filename in filenames:
                            os.chown(os.path.join(dirpath, filename), user_info.pw_uid, user_info.pw_gid)
                except (KeyError, OSError) as e:
                    logger.debug(f"Chown skipped: {e}")

            # Update icon cache
            hicolor_dir = user_home / '.local' / 'share' / 'icons' / 'hicolor'
            subprocess.run(
                ['gtk-update-icon-cache', '-f', '-q', str(hicolor_dir)],
                capture_output=True, timeout=10
            )
        except (PermissionError, OSError, subprocess.SubprocessError) as e:
            logger.debug(f"Icon installation skipped: {e}")

    def on_activate(self, app):
        """Called when application is activated"""
        logger.info("MeshForge GTK application activating...")
        logger.info(f"Logging to /tmp/meshforge-gtk.log")

        # Register custom icons (display is now available)
        self._register_custom_icons()

        if not self.window:
            logger.debug("Creating main window")
            self.window = MeshForgeWindow(application=app)
        self.window.present()
        logger.info("MeshForge GTK application ready")


# Backwards compatibility alias
MeshtasticdApp = MeshForgeApp


class MeshForgeWindow(Adw.ApplicationWindow):
    """MeshForge main application window"""

    # Window constraints
    MIN_WIDTH = 800
    MIN_HEIGHT = 600
    DEFAULT_WIDTH = 1024
    DEFAULT_HEIGHT = 768
    SIDEBAR_COLLAPSE_WIDTH = 900  # Collapse sidebar below this width

    # Edition-specific colors
    EDITION_COLORS = {
        "pro": "#1a73e8",      # Blue
        "amateur": "#fbbc04",   # Gold
        "io": "#673ab7",        # Purple
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Detect edition
        self.edition = detect_edition()
        self.edition_info = get_edition_info()

        # Set edition-aware title
        edition_suffix = ""
        if EDITION_AVAILABLE:
            if self.edition == Edition.AMATEUR:
                edition_suffix = " Amateur"
            elif self.edition == Edition.IO:
                edition_suffix = ".io"
            else:
                edition_suffix = " PRO"

        self.set_title(f"MeshForge{edition_suffix} v{__version__}")

        # Set window icon directly (fixes taskbar icon)
        self._set_window_icon()

        # Set minimum size constraints
        self.set_size_request(self.MIN_WIDTH, self.MIN_HEIGHT)

        # Get monitor dimensions and set appropriate default size
        default_width, default_height = self._get_smart_default_size()
        self.set_default_size(default_width, default_height)

        # Track sidebar visibility for responsive layout
        self._sidebar_visible = True
        self._sidebar_widget = None

        # Track subprocess for nano/terminal operations
        self.external_process = None

        # Cache for node count (avoid repeated connections)
        self._node_count_cache = "--"
        self._node_count_timestamp = 0
        self._node_count_cache_ttl = 30  # Cache for 30 seconds

        # Timer tracking for cleanup
        self._status_timer = None

        # Apply saved theme settings on startup
        self._apply_saved_theme()

        # Create the main layout
        self._build_ui()

        # Set up keyboard shortcuts
        self._setup_keyboard_shortcuts()

        # Set up close handler to cleanup panels
        self.connect("close-request", self._on_close_request)

        # Check if we're resuming after reboot
        self._check_resume_state()

    def _on_close_request(self, window):
        """Handle window close - cleanup all panels with cleanup methods.

        Auto-discovers all panel attributes and calls cleanup() on any that have it.
        This replaces the hard-coded list approach which was prone to missing panels.
        """
        logger.info("Window close requested, cleaning up panels...")

        # Stop the main status update timer
        if self._status_timer:
            GLib.source_remove(self._status_timer)
            self._status_timer = None

        # Auto-discover all panels and clean them up
        # Look for any attribute ending in '_panel' that has a cleanup method
        cleaned_up = []
        failed = []

        for attr_name in dir(self):
            if attr_name.endswith('_panel'):
                panel = getattr(self, attr_name, None)
                if panel and hasattr(panel, 'cleanup') and callable(panel.cleanup):
                    try:
                        panel.cleanup()
                        cleaned_up.append(attr_name)
                    except Exception as e:
                        failed.append(f"{attr_name}: {e}")
                        logger.warning(f"Error cleaning up {attr_name}: {e}")

        if cleaned_up:
            logger.info(f"Cleaned up {len(cleaned_up)} panels: {', '.join(cleaned_up)}")
        if failed:
            logger.warning(f"Failed to cleanup {len(failed)} panels: {failed}")

        # Return False to allow the window to close
        return False

    def _apply_saved_theme(self):
        """Apply saved theme settings on startup."""
        try:
            import json
            from utils.paths import get_real_user_home

            settings_file = get_real_user_home() / ".config" / "meshforge" / "settings.json"
            if settings_file.exists():
                settings = json.loads(settings_file.read_text())

                # Get theme setting (default to dark)
                theme = settings.get("theme", "dark")
                dark_mode = settings.get("dark_mode", True)

                # Apply theme using Adw.StyleManager
                style_manager = Adw.StyleManager.get_default()
                if theme == "system":
                    style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
                elif theme == "dark" or dark_mode:
                    style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                else:
                    style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

                logger.debug(f"Applied saved theme: {theme}")
            else:
                # Default to dark mode
                style_manager = Adw.StyleManager.get_default()
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                logger.debug("No settings file, defaulting to dark theme")

        except Exception as e:
            logger.warning(f"Could not apply saved theme: {e}")
            # Default to dark mode on error
            try:
                style_manager = Adw.StyleManager.get_default()
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            except Exception:
                pass

    def _get_smart_default_size(self):
        """Calculate smart default window size based on monitor dimensions"""
        try:
            # Get the display
            display = Gdk.Display.get_default()
            if display:
                # Get all monitors
                monitors = display.get_monitors()
                if monitors and monitors.get_n_items() > 0:
                    # Use primary or first monitor
                    monitor = monitors.get_item(0)
                    if monitor:
                        geometry = monitor.get_geometry()
                        mon_width = geometry.width
                        mon_height = geometry.height

                        # Use 75% of monitor size, but respect min/max bounds
                        target_width = int(mon_width * 0.75)
                        target_height = int(mon_height * 0.75)

                        # Clamp to reasonable bounds
                        width = max(self.MIN_WIDTH, min(target_width, 1600))
                        height = max(self.MIN_HEIGHT, min(target_height, 1000))

                        return width, height
        except Exception:
            pass

        # Fallback to defaults
        return self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT

    def _setup_responsive_layout(self):
        """Set up responsive layout handling for window resize"""
        # Connect to window state changes for resize handling
        self.connect("notify::default-width", self._on_window_resize)

    def _on_window_resize(self, widget, param):
        """Handle window resize for responsive layout"""
        if not self._sidebar_widget:
            return

        width = self.get_width()
        if width > 0:
            if width < self.SIDEBAR_COLLAPSE_WIDTH and self._sidebar_visible:
                # Hide sidebar on small screens
                self._sidebar_widget.set_visible(False)
                if hasattr(self, '_sidebar_separator'):
                    self._sidebar_separator.set_visible(False)
                self._sidebar_visible = False
            elif width >= self.SIDEBAR_COLLAPSE_WIDTH and not self._sidebar_visible:
                # Show sidebar on larger screens
                self._sidebar_widget.set_visible(True)
                if hasattr(self, '_sidebar_separator'):
                    self._sidebar_separator.set_visible(True)
                self._sidebar_visible = True

    def toggle_sidebar(self):
        """Manually toggle sidebar visibility (for menu button)"""
        if self._sidebar_widget:
            self._sidebar_visible = not self._sidebar_visible
            self._sidebar_widget.set_visible(self._sidebar_visible)
            if hasattr(self, '_sidebar_separator'):
                self._sidebar_separator.set_visible(self._sidebar_visible)

    def _set_window_icon(self):
        """Set window icon directly from file for taskbar display"""
        try:
            from pathlib import Path

            # Find icon file
            src_dir = Path(__file__).parent.parent
            icon_paths = [
                src_dir / 'assets' / 'meshforge-icon.svg',
                Path('/usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg'),
                Path('/usr/share/pixmaps/org.meshforge.app.svg'),
                Path('/opt/meshforge/assets/meshforge-icon.svg'),
            ]

            icon_path = None
            for path in icon_paths:
                if path.exists():
                    icon_path = path
                    break

            if icon_path:
                # For GTK4, use GdkTexture for the icon
                try:
                    texture = Gdk.Texture.new_from_filename(str(icon_path))
                    # GTK4 doesn't have set_icon on windows, but we can use paintable
                    # The icon theme approach should work if properly installed
                    logger.debug(f"Icon loaded from: {icon_path}")
                except Exception as e:
                    logger.debug(f"Could not load icon texture: {e}")

            # Also ensure icon name is set correctly
            self.set_icon_name("org.meshforge.app")

        except Exception as e:
            logger.debug(f"Icon setup: {e}")

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts for the window"""
        # Create key controller
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle key press events"""
        # Escape key - unfullscreen if fullscreened
        if keyval == Gdk.KEY_Escape:
            if self.is_fullscreen():
                self.unfullscreen()
                return True
        # F9 - toggle sidebar
        elif keyval == Gdk.KEY_F9:
            self.toggle_sidebar()
            return True
        # F11 - toggle fullscreen
        elif keyval == Gdk.KEY_F11:
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        # Ctrl+Q - quit
        elif keyval == Gdk.KEY_q and (state & Gdk.ModifierType.CONTROL_MASK):
            self.get_application().quit()
            return True
        # Ctrl+1 through Ctrl+9 - quick page navigation
        elif state & Gdk.ModifierType.CONTROL_MASK:
            nav_keys = {
                Gdk.KEY_1: "dashboard",
                Gdk.KEY_2: "service",
                Gdk.KEY_3: "install",
                Gdk.KEY_4: "config",
                Gdk.KEY_5: "radio_config",
                Gdk.KEY_6: "rns",
                Gdk.KEY_7: "map",
                Gdk.KEY_8: "hamclock",
                Gdk.KEY_9: "tools",
            }
            if keyval in nav_keys:
                self.content_stack.set_visible_child_name(nav_keys[keyval])
                return True
        return False

    def _build_ui(self):
        """Build the main UI layout"""
        # Main vertical box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar with title and menu
        header = Adw.HeaderBar()
        header_title = self.edition_info.get("display_name", "MeshForge")
        header.set_title_widget(Gtk.Label(label=f"{header_title} v{__version__}"))

        # Sidebar toggle button (for responsive layout)
        self._sidebar_toggle_btn = Gtk.Button()
        self._sidebar_toggle_btn.set_icon_name("sidebar-show-symbolic")
        self._sidebar_toggle_btn.set_tooltip_text("Toggle sidebar (F9)")
        self._sidebar_toggle_btn.connect("clicked", lambda btn: self.toggle_sidebar())
        header.pack_start(self._sidebar_toggle_btn)

        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Application menu")
        menu = Gio.Menu()
        menu.append("Toggle Sidebar", "app.toggle-sidebar")
        menu.append("About", "app.about")
        menu.append("Quit", "app.quit")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        main_box.append(header)

        # Create actions
        self._create_actions()

        # Status bar at top
        self.status_bar = self._create_status_bar()
        main_box.append(self.status_bar)

        # Main content area with navigation
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content_box.set_vexpand(True)
        main_box.append(content_box)

        # Main content stack (create BEFORE sidebar to avoid callback issues)
        self.content_stack = Gtk.Stack()
        self.content_stack.set_hexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        # Lazy loading: Store panel loaders but only instantiate on first navigation
        # This prevents the "thundering herd" problem where 20+ panels spawn threads at startup
        logger.info("Setting up lazy panel loading...")
        self._panel_loaders = {
            "dashboard": self._add_dashboard_page,
            "service": self._add_service_page,
            "install": self._add_install_page,
            "config": self._add_config_page,
            "radio_config": self._add_radio_config_page,
            "rns": self._add_rns_page,
            # Consolidated tool panels
            "mesh_tools": self._add_mesh_tools_page,
            "ham_tools": self._add_ham_tools_page,
            # Legacy panels (still available)
            "map": self._add_map_page,
            "hamclock": self._add_hamclock_page,
            "cli": self._add_cli_page,
            "hardware": self._add_hardware_page,
            "tools": self._add_tools_page,
            "diagnostics": self._add_diagnostics_page,
            "aredn": self._add_aredn_page,
            "amateur": self._add_amateur_page,
            "meshbot": self._add_meshbot_page,
            "messaging": self._add_messaging_page,
            "message_routing": self._add_message_routing_page,
            "mqtt_dashboard": self._add_mqtt_dashboard_page,
            "eas_alerts": self._add_eas_alerts_page,
            "settings": self._add_settings_page,
        }
        self._loaded_panels = set()

        # Create lightweight placeholders for all panels (allows sidebar to work)
        for name in self._panel_loaders.keys():
            placeholder = self._create_loading_placeholder(name)
            self.content_stack.add_named(placeholder, name)

        # Only load dashboard immediately (it's the default view)
        # Other panels load on-demand when navigated to
        try:
            logger.debug("Loading initial panel: dashboard")
            self._load_panel("dashboard")
            logger.debug("Dashboard loaded OK")
        except Exception as e:
            logger.error(f"Failed to load dashboard: {e}", exc_info=True)

        # Left sidebar navigation (after content_stack exists)
        sidebar = self._create_sidebar()
        self._sidebar_widget = sidebar  # Store reference for responsive layout
        content_box.append(sidebar)

        # Separator (also needs to hide with sidebar)
        self._sidebar_separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self._sidebar_separator)

        # Add content stack to layout
        content_box.append(self.content_stack)

        # Bottom status bar
        self.bottom_status = self._create_bottom_status()
        main_box.append(self.bottom_status)

        # Start status update timer (store ID for cleanup)
        # Delay first update by 2 seconds to let UI render first
        # Use 10 second interval (was 5s) to reduce subprocess overhead
        self._status_update_running = False  # Prevent overlapping updates
        GLib.timeout_add_seconds(2, self._delayed_status_start)

        # Set up responsive layout handling
        self._setup_responsive_layout()

    def _create_actions(self):
        """Create application actions"""
        app = self.get_application()

        # Toggle sidebar action
        sidebar_action = Gio.SimpleAction.new("toggle-sidebar", None)
        sidebar_action.connect("activate", lambda *_: self.toggle_sidebar())
        app.add_action(sidebar_action)

        # About action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        app.add_action(about_action)

        # Quit action
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: app.quit())
        app.add_action(quit_action)

    def _on_about(self, action, param):
        """Show about dialog"""
        # Edition-aware app name
        app_name = self.edition_info.get("display_name", "MeshForge")
        tagline = ""
        if EDITION_AVAILABLE:
            if self.edition == Edition.AMATEUR:
                tagline = "When All Else Fails"
            elif self.edition == Edition.IO:
                tagline = "Mesh Made Simple"
            else:
                tagline = "Professional Mesh Management"

        dialog = Adw.AboutWindow(
            transient_for=self,
            application_name=app_name,
            application_icon="meshforge-icon",
            version=get_full_version(),
            developer_name="MeshForge Community",
            license_type=Gtk.License.GPL_3_0,
            website="https://meshforge.org",
            issue_url="https://github.com/Nursedude/meshforge/issues",
            developers=["Nursedude", "Contributors"],
            comments=tagline,
        )
        dialog.present()

    def _create_status_bar(self):
        """Create the top status bar"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(5)
        box.set_margin_bottom(5)
        box.add_css_class("status-bar")

        # Service status indicator
        self.service_status_icon = Gtk.Image.new_from_icon_name("emblem-default")
        box.append(self.service_status_icon)

        self.service_status_label = Gtk.Label(label="Service: Checking...")
        box.append(self.service_status_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        box.append(spacer)

        # Node count
        self.node_count_label = Gtk.Label(label="Nodes: --")
        box.append(self.node_count_label)

        # Separator
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # Uptime
        self.uptime_label = Gtk.Label(label="Uptime: --")
        box.append(self.uptime_label)

        # Separator
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # View Logs button
        logs_btn = Gtk.Button()
        logs_btn.set_icon_name("utilities-terminal-symbolic")
        logs_btn.set_tooltip_text("View Application Logs")
        logs_btn.connect("clicked", self._on_view_logs)
        box.append(logs_btn)

        return box

    def _create_sidebar(self):
        """Create the navigation sidebar"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_size_request(200, -1)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.add_css_class("navigation-sidebar")
        listbox.connect("row-selected", self._on_nav_selected)
        scrolled.set_child(listbox)

        # Navigation items with feature requirements
        # Format: (page_name, label, icon, required_feature or None)
        # Reorganized into: Core, Consolidated Tools, Legacy/Advanced, Settings
        all_nav_items = [
            # Core functionality
            ("dashboard", "Dashboard", "utilities-system-monitor-symbolic", None),
            ("service", "Service Management", "system-run-symbolic", None),
            ("install", "Install / Update", "system-software-install-symbolic", None),
            ("config", "Config File Manager", "document-edit-symbolic", None),
            ("radio_config", "Radio Configuration", "network-wireless-symbolic", None),
            ("rns", "Reticulum (RNS)", "network-transmit-receive-symbolic", "rns_integration"),
            # Consolidated tool panels (new)
            ("mesh_tools", "Mesh Tools", "network-workgroup-symbolic", None),
            ("ham_tools", "Ham Tools", "audio-speakers-symbolic", None),
            # Messaging & Alerts
            ("messaging", "Messaging", "mail-unread-symbolic", None),
            ("eas_alerts", "EAS Alerts", "dialog-warning-symbolic", None),
            # Advanced/Legacy panels
            ("cli", "Meshtastic CLI", "utilities-terminal-symbolic", None),
            ("hardware", "Hardware Detection", "drive-harddisk-symbolic", None),
            ("tools", "System Tools", "applications-utilities-symbolic", None),
            ("aredn", "AREDN Mesh", "network-server-symbolic", "aredn_integration"),
            # Settings
            ("settings", "Settings", "preferences-system-symbolic", None),
        ]

        # Filter by edition features
        nav_items = []
        for item in all_nav_items:
            name, label, icon, feature = item
            if feature is None or has_feature(feature):
                nav_items.append((name, label, icon))

        for name, label, icon in nav_items:
            row = Gtk.ListBoxRow()
            row.set_name(name)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.set_margin_top(8)
            box.set_margin_bottom(8)

            img = Gtk.Image.new_from_icon_name(icon)
            box.append(img)

            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            box.append(lbl)

            row.set_child(box)
            listbox.append(row)

        # Select first item
        listbox.select_row(listbox.get_row_at_index(0))

        return scrolled

    def _on_nav_selected(self, listbox, row):
        """Handle navigation selection with lazy loading"""
        if row:
            page_name = row.get_name()
            # Lazy load the panel if not already loaded
            if page_name not in self._loaded_panels and page_name in self._panel_loaders:
                self._load_panel(page_name)
            self.content_stack.set_visible_child_name(page_name)

    def _create_loading_placeholder(self, panel_name: str) -> Gtk.Box:
        """Create a lightweight placeholder widget shown while panel loads."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        spinner = Gtk.Spinner()
        spinner.set_size_request(32, 32)
        spinner.start()
        box.append(spinner)

        label = Gtk.Label(label=f"Loading {panel_name.replace('_', ' ').title()}...")
        label.add_css_class("dim-label")
        box.append(label)

        return box

    def _load_panel(self, panel_name: str):
        """Load a panel, replacing its placeholder."""
        if panel_name in self._loaded_panels:
            return  # Already loaded

        loader = self._panel_loaders.get(panel_name)
        if not loader:
            logger.warning(f"No loader for panel: {panel_name}")
            return

        # Remove placeholder
        placeholder = self.content_stack.get_child_by_name(panel_name)
        if placeholder:
            self.content_stack.remove(placeholder)

        # Load the actual panel
        try:
            logger.debug(f"Lazy loading panel: {panel_name}")
            loader()
            self._loaded_panels.add(panel_name)
            logger.debug(f"Loaded panel: {panel_name} OK")
        except Exception as e:
            logger.error(f"Failed to load panel {panel_name}: {e}", exc_info=True)
            self._add_error_placeholder(panel_name, str(e))

    def _add_dashboard_page(self):
        """Add the dashboard page"""
        from .panels.dashboard import DashboardPanel
        panel = DashboardPanel(self)
        self.content_stack.add_named(panel, "dashboard")
        self.dashboard_panel = panel

    def _add_service_page(self):
        """Add the service management page"""
        from .panels.service import ServicePanel
        panel = ServicePanel(self)
        self.content_stack.add_named(panel, "service")
        self.service_panel = panel

    def _add_install_page(self):
        """Add the install/update page"""
        from .panels.install import InstallPanel
        panel = InstallPanel(self)
        self.content_stack.add_named(panel, "install")
        self.install_panel = panel

    def _add_config_page(self):
        """Add the config file manager page"""
        from .panels.config import ConfigPanel
        panel = ConfigPanel(self)
        self.content_stack.add_named(panel, "config")
        self.config_panel = panel

    def _add_radio_config_page(self):
        """Add the radio configuration page"""
        # Use simplified panel - direct library access, no CLI parsing
        from .panels.radio_config_simple import RadioConfigSimple
        panel = RadioConfigSimple(self)
        self.content_stack.add_named(panel, "radio_config")
        self.radio_config_panel = panel

    def _add_rns_page(self):
        """Add the RNS/Reticulum management page"""
        from .panels.rns import RNSPanel
        panel = RNSPanel(self)
        self.content_stack.add_named(panel, "rns")
        self.rns_panel = panel

    def _add_map_page(self):
        """Add the unified node map page"""
        from .panels.map import MapPanel
        panel = MapPanel(self)
        self.content_stack.add_named(panel, "map")
        self.map_panel = panel

    def _add_hamclock_page(self):
        """Add the HamClock integration page"""
        from .panels.hamclock import HamClockPanel
        panel = HamClockPanel(self)
        self.content_stack.add_named(panel, "hamclock")
        self.hamclock_panel = panel

    def _add_cli_page(self):
        """Add the meshtastic CLI page"""
        from .panels.cli import CLIPanel
        panel = CLIPanel(self)
        self.content_stack.add_named(panel, "cli")
        self.cli_panel = panel

    def _add_hardware_page(self):
        """Add the hardware detection page"""
        from .panels.hardware import HardwarePanel
        panel = HardwarePanel(self)
        self.content_stack.add_named(panel, "hardware")
        self.hardware_panel = panel

    def _add_tools_page(self):
        """Add the system tools page"""
        from .panels.tools import ToolsPanel
        panel = ToolsPanel(self)
        self.content_stack.add_named(panel, "tools")
        self.tools_panel = panel

    def _add_diagnostics_page(self):
        """Add the network diagnostics page"""
        from .panels.diagnostics import DiagnosticsPanel
        panel = DiagnosticsPanel(self)
        self.content_stack.add_named(panel, "diagnostics")
        self.diagnostics_panel = panel

    def _add_aredn_page(self):
        """Add the AREDN mesh network page"""
        from .panels.aredn import AREDNPanel
        panel = AREDNPanel(self)
        self.content_stack.add_named(panel, "aredn")
        self.aredn_panel = panel

    def _add_amateur_page(self):
        """Add the Amateur Radio page (Amateur Edition)"""
        try:
            from .amateur_panel import AmateurPanel
            panel = AmateurPanel(self)
            self.content_stack.add_named(panel, "amateur")
            self.amateur_panel = panel
        except ImportError:
            # Create placeholder if amateur module not available
            placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            placeholder.set_valign(Gtk.Align.CENTER)
            placeholder.set_halign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name("audio-speakers-symbolic")
            icon.set_pixel_size(64)
            placeholder.append(icon)
            label = Gtk.Label(label="Amateur Radio Features")
            label.add_css_class("title-1")
            placeholder.append(label)
            desc = Gtk.Label(label="Ham radio specific features for licensed operators")
            desc.add_css_class("dim-label")
            placeholder.append(desc)
            self.content_stack.add_named(placeholder, "amateur")
            self.amateur_panel = None

    def _add_meshbot_page(self):
        """Add the MeshBot (meshing-around) integration page"""
        from .panels.meshbot import MeshBotPanel
        panel = MeshBotPanel(self)
        self.content_stack.add_named(panel, "meshbot")
        self.meshbot_panel = panel

    def _add_messaging_page(self):
        """Add the native messaging page"""
        from .panels.messaging import MessagingPanel
        panel = MessagingPanel(self)
        self.content_stack.add_named(panel, "messaging")
        self.messaging_panel = panel

    def _add_mesh_tools_page(self):
        """Add the consolidated Mesh Tools page (MeshBot, Map, Diagnostics)"""
        from .panels.mesh_tools import MeshToolsPanel
        panel = MeshToolsPanel(self)
        self.content_stack.add_named(panel, "mesh_tools")
        self.mesh_tools_panel = panel

    def _add_ham_tools_page(self):
        """Add the consolidated Ham Tools page (HamClock, Propagation, Callsign)"""
        from .panels.ham_tools import HamToolsPanel
        panel = HamToolsPanel(self)
        self.content_stack.add_named(panel, "ham_tools")
        self.ham_tools_panel = panel

    def _add_message_routing_page(self):
        """Add the message routing visualization page"""
        from .panels.message_routing import MessageRoutingPanel
        panel = MessageRoutingPanel(self)
        self.content_stack.add_named(panel, "message_routing")
        self.message_routing_panel = panel

    def _add_mqtt_dashboard_page(self):
        """Add the MQTT dashboard page"""
        from .panels.mqtt_dashboard import MQTTDashboardPanel
        panel = MQTTDashboardPanel(self)
        self.content_stack.add_named(panel, "mqtt_dashboard")
        self.mqtt_dashboard_panel = panel

    def _add_eas_alerts_page(self):
        """Add the Emergency Alert System page"""
        from .panels.eas_alerts import EASAlertsPanel
        panel = EASAlertsPanel(self)
        self.content_stack.add_named(panel, "eas_alerts")
        self.eas_alerts_panel = panel

    def _add_settings_page(self):
        """Add the settings page"""
        from .panels.settings import SettingsPanel
        panel = SettingsPanel(self)
        self.content_stack.add_named(panel, "settings")
        self.settings_panel = panel

    def _add_error_placeholder(self, panel_name: str, error_message: str):
        """Add a placeholder for a panel that failed to load"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_start(40)
        box.set_margin_end(40)

        icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        icon.set_pixel_size(64)
        box.append(icon)

        title = Gtk.Label(label=f"Failed to load: {panel_name}")
        title.add_css_class("title-2")
        box.append(title)

        error_label = Gtk.Label(label=error_message)
        error_label.set_wrap(True)
        error_label.set_max_width_chars(60)
        error_label.add_css_class("error")
        box.append(error_label)

        hint = Gtk.Label(label="Check /tmp/meshforge-gtk.log for details")
        hint.add_css_class("dim-label")
        box.append(hint)

        self.content_stack.add_named(box, panel_name)

    def _create_bottom_status(self):
        """Create bottom status bar"""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(5)
        box.set_margin_bottom(5)

        self.bottom_message = Gtk.Label(label="Ready")
        self.bottom_message.set_xalign(0)
        self.bottom_message.set_hexpand(True)
        box.append(self.bottom_message)

        return box

    def set_status_message(self, message):
        """Set the bottom status message"""
        # Defensive check: bottom_message may not exist during panel initialization
        if hasattr(self, 'bottom_message') and self.bottom_message:
            self.bottom_message.set_label(message)

    def _on_view_logs(self, button):
        """Show log viewer dialog with scrollable content"""
        try:
            log_content = self._get_recent_logs()

            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Application Logs",
                body=""  # Empty body - we use extra_child for scrollable content
            )

            # Create scrollable text view for logs
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_min_content_height(400)
            scrolled.set_min_content_width(600)

            text_view = Gtk.TextView()
            text_view.set_editable(False)
            text_view.set_monospace(True)
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.get_buffer().set_text(log_content)
            scrolled.set_child(text_view)

            dialog.set_extra_child(scrolled)
            dialog.add_response("ok", "Close")
            dialog.present()
        except Exception as e:
            logger.error(f"Failed to show log viewer: {e}")
            self.set_status_message(f"Could not open logs: {e}")

    def _get_recent_logs(self):
        """Get recent log content as string"""
        try:
            from utils.logging_utils import LOG_DIR
            log_dir = LOG_DIR
        except ImportError:
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and sudo_user != 'root':
                log_dir = Path(f'/home/{sudo_user}/.config/meshforge/logs')
            else:
                log_dir = get_real_user_home() / '.config' / 'meshforge' / 'logs'

        if not log_dir.exists():
            return f"Log directory not found: {log_dir}"

        log_files = sorted(log_dir.glob("meshforge_*.log"), reverse=True)
        if not log_files:
            return "No log files found yet."

        try:
            with open(log_files[0], 'r') as f:
                lines = f.readlines()
                recent = lines[-200:] if len(lines) > 200 else lines
                return ''.join(recent)
        except Exception as e:
            return f"Error reading logs: {e}"

    def _delayed_status_start(self):
        """Start the status timer after initial delay (lets UI render first)."""
        self._status_timer = GLib.timeout_add_seconds(10, self._update_status)
        self._update_status()
        return False  # Don't repeat this one-shot timer

    def _update_status(self):
        """Update status bar information"""
        # Prevent overlapping updates (previous one still running)
        if self._status_update_running:
            return True  # Continue timer but skip this tick

        self._status_update_running = True
        thread = threading.Thread(target=self._update_status_thread)
        thread.daemon = True
        thread.start()
        return True  # Continue timer

    def _update_status_thread(self):
        """Thread for updating status - optimized to reduce subprocess overhead."""
        try:
            import socket

            # Simplified check: just use systemctl is-active (most reliable)
            # Removed redundant pgrep and socket checks that added latency
            is_active = False
            uptime = "--"

            result = subprocess.run(
                ['systemctl', 'is-active', 'meshtasticd'],
                capture_output=True, text=True, timeout=3
            )
            if result.stdout.strip() == 'active':
                is_active = True

            # Get uptime only if active (single combined call)
            node_count = "--"
            if is_active:
                result = subprocess.run(
                    ['systemctl', 'show', 'meshtasticd', '--property=ActiveEnterTimestamp'],
                    capture_output=True, text=True, timeout=3
                )
                if 'ActiveEnterTimestamp=' in result.stdout:
                    timestamp = result.stdout.split('=')[1].strip()
                    if timestamp:
                        uptime = self._calculate_uptime(timestamp)

                # Node count uses caching internally
                node_count = self._get_node_count()

            # Update UI in main thread
            GLib.idle_add(self._update_status_ui, is_active, uptime, node_count)

        except Exception as e:
            GLib.idle_add(self._update_status_ui, False, "--", "--")
        finally:
            self._status_update_running = False

    def _get_node_count(self):
        """Get the number of nodes from meshtastic TCP interface or CLI"""
        import time as time_module
        import socket

        # Check for web client mode - don't connect if enabled
        try:
            from utils.common import SettingsManager
            settings = SettingsManager()
            if settings.get("web_client_mode", False):
                return "--"  # Return placeholder when web client mode is on
        except Exception:  # Non-critical: settings check may fail
            pass

        # Check cache first
        now = time_module.time()
        if now - self._node_count_timestamp < self._node_count_cache_ttl:
            return self._node_count_cache

        # Quick pre-check: is meshtasticd TCP port reachable?
        sock = None
        port_reachable = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(("localhost", 4403))
            port_reachable = True
        except (socket.timeout, socket.error, OSError):
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:  # Ignore errors during cleanup
                    pass

        if not port_reachable:
            # Port not reachable, skip node count
            return self._node_count_cache

        # Use CLI method (more reliable, avoids meshtastic library noise)
        try:
            import re
            # Find meshtastic CLI using centralized function
            try:
                from utils.cli import find_meshtastic_cli
                cli_path = find_meshtastic_cli()
            except ImportError:
                cli_path = shutil.which('meshtastic')

            if not cli_path:
                return self._node_count_cache

            # Run meshtastic --nodes to get node info (suppress stderr)
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--nodes'],
                capture_output=True, text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout:
                # Look for node IDs in the output (format: !xxxxxxxx)
                node_ids = re.findall(r'!([0-9a-fA-F]{8})', result.stdout)
                if node_ids:
                    self._node_count_cache = str(len(set(node_ids)))
                    self._node_count_timestamp = now
                    return self._node_count_cache
            return self._node_count_cache
        except subprocess.TimeoutExpired:
            return self._node_count_cache
        except Exception:
            return self._node_count_cache

    def _update_status_ui(self, is_active, uptime, node_count="--"):
        """Update status UI elements (must run in main thread)"""
        if is_active:
            self.service_status_icon.set_from_icon_name("emblem-default")
            self.service_status_label.set_label("Service: Running")
            self.service_status_label.remove_css_class("error")
            self.service_status_label.add_css_class("success")
        else:
            self.service_status_icon.set_from_icon_name("dialog-error")
            self.service_status_label.set_label("Service: Stopped")
            self.service_status_label.remove_css_class("success")
            self.service_status_label.add_css_class("error")

        self.uptime_label.set_label(f"Uptime: {uptime}")
        self.node_count_label.set_label(f"Nodes: {node_count}")
        return False

    def _calculate_uptime(self, timestamp):
        """Calculate uptime from timestamp"""
        try:
            from datetime import datetime
            import re

            if not timestamp or timestamp == 'n/a':
                return "--"

            # Try multiple timestamp formats
            # Format 1: "2026-01-02 10:30:45 UTC"
            # Format 2: "Thu 2026-01-02 10:30:45 UTC"
            # Format 3: "Thu Jan  2 10:30:45 2026"

            start = None

            # Try ISO format first (most common)
            try:
                # Remove timezone suffix
                clean_ts = re.sub(r'\s+(UTC|[A-Z]{3,4})$', '', timestamp)
                # Remove day name prefix if present
                clean_ts = re.sub(r'^[A-Za-z]+\s+', '', clean_ts)
                start = datetime.strptime(clean_ts[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass

            # Try alternative format (ctime-like)
            if not start:
                try:
                    # Format: "Thu Jan  2 10:30:45 2026"
                    start = datetime.strptime(timestamp, '%a %b %d %H:%M:%S %Y')
                except ValueError:
                    pass

            # Try with timezone
            if not start:
                try:
                    # Format: "Thu 2026-01-02 10:30:45 PST"
                    clean_ts = re.sub(r'\s+[A-Z]{3,4}$', '', timestamp)
                    start = datetime.strptime(clean_ts, '%a %Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass

            if not start:
                return "--"

            now = datetime.now()
            delta = now - start

            total_seconds = delta.total_seconds()
            if total_seconds < 0:
                return "--"

            hours = int(total_seconds // 3600)
            minutes = int((total_seconds % 3600) // 60)

            if hours > 24:
                days = hours // 24
                hours = hours % 24
                return f"{days}d {hours}h"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
        except (ValueError, TypeError, OSError):
            return "--"

    def open_terminal_editor(self, file_path, callback=None):
        """
        Open a file in nano editor in a terminal window.
        This ensures the user can always return to the installer.

        Args:
            file_path: Path to file to edit
            callback: Optional callback when editor closes
        """
        def run_editor():
            try:
                # Try different terminal emulators
                terminals = [
                    ['x-terminal-emulator', '-e'],
                    ['gnome-terminal', '--'],
                    ['xfce4-terminal', '-e'],
                    ['lxterminal', '-e'],
                    ['xterm', '-e'],
                ]

                for term_cmd in terminals:
                    try:
                        # Build command - nano with the file
                        cmd = term_cmd + ['nano', str(file_path)]

                        # Run and wait for completion (interactive session)
                        self.external_process = subprocess.Popen(cmd)
                        try:
                            self.external_process.wait(timeout=14400)  # 4 hour max
                        except subprocess.TimeoutExpired:
                            self.external_process.terminate()
                            logger.warning("Editor session timed out after 4 hours")
                        finally:
                            self.external_process = None

                        # Call callback in main thread
                        if callback:
                            GLib.idle_add(callback)
                        return

                    except FileNotFoundError:
                        continue

                # No terminal found - show error in main thread
                GLib.idle_add(
                    self._show_error_dialog,
                    "No Terminal Found",
                    "Could not find a terminal emulator. Please install one of:\n"
                    "gnome-terminal, xfce4-terminal, lxterminal, or xterm"
                )

            except Exception as e:
                GLib.idle_add(
                    self._show_error_dialog,
                    "Editor Error",
                    f"Failed to open editor: {e}"
                )

        # Run in thread to not block UI
        thread = threading.Thread(target=run_editor)
        thread.daemon = True
        thread.start()

    def run_command_with_output(self, command, callback=None):
        """
        Run a command and capture output for display.

        Args:
            command: Command list to run
            callback: Callback with (success, stdout, stderr)
        """
        def run_cmd():
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if callback:
                    GLib.idle_add(callback, result.returncode == 0, result.stdout, result.stderr)
            except subprocess.TimeoutExpired:
                if callback:
                    GLib.idle_add(callback, False, "", "Command timed out")
            except Exception as e:
                if callback:
                    GLib.idle_add(callback, False, "", str(e))

        thread = threading.Thread(target=run_cmd)
        thread.daemon = True
        thread.start()

    def _show_error_dialog(self, title, message):
        """Show an error dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def show_info_dialog(self, title, message):
        """Show an info dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def show_confirm_dialog(self, title, message, callback):
        """Show a confirmation dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=title,
            body=message
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("confirm", "Confirm")
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda d, r: callback(r == "confirm"))
        dialog.present()

    def request_reboot(self, reason):
        """
        Request a system reboot with persistence.
        Sets up autostart so installer returns after reboot.
        """
        def do_reboot(confirmed):
            if confirmed:
                # Save resume state
                self._save_resume_state(reason)

                # Enable autostart
                self._enable_autostart()

                # Schedule reboot
                self.set_status_message("Rebooting in 5 seconds...")
                GLib.timeout_add_seconds(5, self._perform_reboot)

        self.show_confirm_dialog(
            "Reboot Required",
            f"{reason}\n\nThe system needs to reboot. The installer will automatically "
            "restart after reboot.\n\nReboot now?",
            do_reboot
        )

    def _perform_reboot(self):
        """Perform the actual reboot - runs in background thread"""
        def do_reboot():
            try:
                subprocess.run(['systemctl', 'reboot'], check=True, timeout=10)
            except Exception as e:
                GLib.idle_add(self._show_error_dialog, "Reboot Failed", str(e))
        threading.Thread(target=do_reboot, daemon=True).start()
        return False

    def _save_resume_state(self, reason):
        """Save state for resume after reboot"""
        state_file = get_real_user_home() / '.meshtasticd-installer-resume'
        try:
            state_file.write_text(f"reason={reason}\npage=config\n")
        except Exception as e:
            print(f"Failed to save resume state: {e}")

    def _check_resume_state(self):
        """Check if we're resuming after reboot"""
        state_file = get_real_user_home() / '.meshtasticd-installer-resume'
        if state_file.exists():
            try:
                content = state_file.read_text()
                state_file.unlink()  # Remove the state file

                # Parse state
                state = {}
                for line in content.strip().split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        state[key] = value

                # Show resume message
                reason = state.get('reason', 'Unknown')
                self.show_info_dialog(
                    "Welcome Back",
                    f"System rebooted successfully.\n\nReason: {reason}\n\n"
                    "You can now continue with configuration."
                )

                # Navigate to the appropriate page
                page = state.get('page', 'dashboard')
                self.content_stack.set_visible_child_name(page)

            except Exception as e:
                print(f"Failed to read resume state: {e}")

    def _enable_autostart(self):
        """Enable autostart for the installer after reboot"""
        autostart_dir = get_real_user_home() / '.config' / 'autostart'
        autostart_dir.mkdir(parents=True, exist_ok=True)

        desktop_entry = f"""[Desktop Entry]
Type=Application
Name=Meshtasticd Manager
Exec=sudo python3 {Path(__file__).parent.parent}/main_gtk.py
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
Comment=Resume Meshtasticd Manager after reboot
"""

        desktop_file = autostart_dir / 'meshtasticd-manager.desktop'
        try:
            desktop_file.write_text(desktop_entry)
        except Exception as e:
            print(f"Failed to create autostart entry: {e}")

    def _disable_autostart(self):
        """Disable autostart"""
        desktop_file = get_real_user_home() / '.config' / 'autostart' / 'meshtasticd-manager.desktop'
        try:
            if desktop_file.exists():
                desktop_file.unlink()
        except Exception as e:
            print(f"Failed to remove autostart entry: {e}")


def main():
    """Main entry point for GTK4 application"""
    app = MeshtasticdApp()
    return app.run(sys.argv)


if __name__ == '__main__':
    main()
