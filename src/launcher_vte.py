#!/usr/bin/env python3
"""
MeshForge VTE Launcher - GTK4 Terminal Wrapper

Embeds the TUI launcher in a GTK4 VTE terminal widget.
This provides:
- Proper taskbar icon (via GTK4 app_id)
- Window class support for desktop integration
- Native terminal experience with GTK4 decorations

Requirements:
- gir1.2-vte-2.91
- libvte-2.91-gtk4-0

Install: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0
"""

import os
import sys
from pathlib import Path

# Setup gi before any imports
import gi


def get_real_user_home() -> Path:
    """Get the real user's home directory, even when running with sudo.

    When running with sudo, Path.home() returns /root. This function
    checks for SUDO_USER to get the original user's home.
    """
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user and sudo_user != 'root':
        return Path(f'/home/{sudo_user}')
    return Path.home()

# VTE 2.91 is the GIR binding version - works with both GTK3 and GTK4
# The library (libvte-2.91-gtk4-0) provides GTK4 support
try:
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    gi.require_version('Vte', '2.91')  # VTE GIR version (same for GTK3/GTK4)
    GTK_VERSION = 4
except ValueError:
    try:
        gi.require_version('Gtk', '3.0')
        gi.require_version('Vte', '2.91')
        GTK_VERSION = 3
    except ValueError:
        print("Error: GTK and VTE libraries not found.")
        print("Install with: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0")
        sys.exit(1)

if GTK_VERSION == 4:
    from gi.repository import Gtk, Adw, GLib, Gio, Gdk
    try:
        from gi.repository import Vte
        VTE_AVAILABLE = True
    except ImportError:
        VTE_AVAILABLE = False
else:
    from gi.repository import Gtk, GLib, Gio, Gdk
    try:
        from gi.repository import Vte
        VTE_AVAILABLE = True
    except ImportError:
        VTE_AVAILABLE = False


# Import version
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from __version__ import __version__
except ImportError:
    __version__ = "0.4.6-beta"


class MeshForgeVTEApp(Adw.Application if GTK_VERSION == 4 else Gtk.Application):
    """MeshForge VTE Terminal Application"""

    def __init__(self):
        app_id = 'org.meshforge.app'
        flags = Gio.ApplicationFlags.NON_UNIQUE

        if GTK_VERSION == 4:
            super().__init__(application_id=app_id, flags=flags)
        else:
            super().__init__(application_id=app_id, flags=flags)

        self.window = None
        self._icons_registered = False
        self.connect('activate', self.on_activate)

    def _register_icons(self):
        """Register MeshForge icons with the icon theme"""
        if self._icons_registered:
            return

        src_dir = Path(__file__).parent.parent
        assets_dir = src_dir / 'assets'
        icon_src = assets_dir / 'meshforge-icon.svg'

        if GTK_VERSION == 4:
            display = Gdk.Display.get_default()
            if display:
                icon_theme = Gtk.IconTheme.get_for_display(display)

                # Install icon to user's local hicolor theme for better integration
                if icon_src.exists():
                    self._install_icon_to_user_theme(icon_src)

                    # Add user's local icons to theme search path
                    local_icons = get_real_user_home() / '.local' / 'share' / 'icons'
                    if local_icons.exists():
                        icon_theme.add_search_path(str(local_icons))

                # Also add assets dir as fallback
                if assets_dir.exists():
                    icon_theme.add_search_path(str(assets_dir))

                Gtk.Window.set_default_icon_name("org.meshforge.app")
                self._icons_registered = True

    def _install_icon_to_user_theme(self, icon_src: Path):
        """Install icon to user's local hicolor icon theme."""
        try:
            import shutil

            # Install to scalable apps directory
            local_icon_dir = get_real_user_home() / '.local' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps'
            local_icon_dir.mkdir(parents=True, exist_ok=True)

            target_icon = local_icon_dir / 'org.meshforge.app.svg'

            # Only copy if source is newer or target doesn't exist
            if not target_icon.exists() or icon_src.stat().st_mtime > target_icon.stat().st_mtime:
                shutil.copy2(icon_src, target_icon)

                # Fix ownership if running as root
                if os.geteuid() == 0:
                    sudo_uid = os.environ.get('SUDO_UID')
                    sudo_gid = os.environ.get('SUDO_GID')
                    if sudo_uid and sudo_gid:
                        os.chown(target_icon, int(sudo_uid), int(sudo_gid))
                        # Also fix parent directories
                        for parent in [local_icon_dir, local_icon_dir.parent, local_icon_dir.parent.parent]:
                            try:
                                os.chown(parent, int(sudo_uid), int(sudo_gid))
                            except (OSError, PermissionError):
                                break

        except Exception as e:
            # Non-fatal - icon might just show as generic
            pass

    def on_activate(self, app):
        """Handle app activation"""
        # Register icons before creating window
        self._register_icons()

        if not self.window:
            self.window = MeshForgeVTEWindow(application=app)
        self.window.present()


class MeshForgeVTEWindow(Adw.ApplicationWindow if GTK_VERSION == 4 else Gtk.ApplicationWindow):
    """MeshForge VTE Terminal Window"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title(f"MeshForge v{__version__}")
        self.set_default_size(900, 700)

        # Set window icon
        self._set_window_icon()

        # Build UI
        self._build_ui()

    def _set_window_icon(self):
        """Set window icon for taskbar - properly installs to XDG icon theme.

        GTK4/libadwaita requires icons in the hicolor theme structure.
        We install to user's local icons (~/.local/share/icons/) which
        doesn't require root and works reliably.
        """
        try:
            src_dir = Path(__file__).parent.parent
            assets_dir = src_dir / 'assets'
            icon_file = assets_dir / 'meshforge-icon.svg'

            if not icon_file.exists():
                # Try alternate location
                icon_file = Path(__file__).parent / 'assets' / 'meshforge-icon.svg'

            if GTK_VERSION == 4:
                # Install icon to user's local icon theme (no root needed)
                self._install_icon_to_theme(icon_file)

                display = Gdk.Display.get_default()
                if display:
                    icon_theme = Gtk.IconTheme.get_for_display(display)

                    # Add user's local icons to search path
                    local_icons = get_real_user_home() / '.local' / 'share' / 'icons'
                    if local_icons.exists():
                        icon_theme.add_search_path(str(local_icons / 'hicolor'))

                    # Also add assets directory as fallback
                    if assets_dir.exists():
                        icon_theme.add_search_path(str(assets_dir))

                # Set the icon name - must match installed filename (without .svg)
                Gtk.Window.set_default_icon_name("org.meshforge.app")
                self.set_icon_name("org.meshforge.app")
            else:
                # GTK3: use set_icon_from_file directly
                user_home = get_real_user_home()
                icon_paths = [
                    user_home / '.local' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps' / 'org.meshforge.app.svg',
                    icon_file,
                    Path('/usr/share/icons/hicolor/scalable/apps/org.meshforge.app.svg'),
                ]
                for path in icon_paths:
                    if path.exists():
                        self.set_icon_from_file(str(path))
                        break
        except Exception as e:
            print(f"Icon setup: {e}")

    def _install_icon_to_theme(self, source_icon: Path):
        """Install icon to user's local hicolor icon theme.

        Uses get_real_user_home() to handle running with sudo correctly.
        """
        if not source_icon.exists():
            return

        import shutil

        # User's local icon directory - use real user home, not /root
        user_home = get_real_user_home()
        local_icon_dir = user_home / '.local' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps'
        target_icon = local_icon_dir / 'org.meshforge.app.svg'

        # Skip if already installed and up to date
        if target_icon.exists():
            if target_icon.stat().st_mtime >= source_icon.stat().st_mtime:
                return

        try:
            # Create directory structure
            local_icon_dir.mkdir(parents=True, exist_ok=True)

            # Copy icon file
            shutil.copy2(str(source_icon), str(target_icon))

            # Fix ownership if running as root for another user
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and os.geteuid() == 0:
                import pwd
                try:
                    user_info = pwd.getpwnam(sudo_user)
                    # Chown the entire .local/share/icons tree we created
                    icons_base = user_home / '.local' / 'share' / 'icons'
                    for dirpath, dirnames, filenames in os.walk(str(icons_base)):
                        os.chown(dirpath, user_info.pw_uid, user_info.pw_gid)
                        for filename in filenames:
                            os.chown(os.path.join(dirpath, filename), user_info.pw_uid, user_info.pw_gid)
                except (KeyError, OSError):
                    pass

            # Update icon cache (best effort)
            hicolor_dir = user_home / '.local' / 'share' / 'icons' / 'hicolor'
            import subprocess
            subprocess.run(
                ['gtk-update-icon-cache', '-f', '-q', str(hicolor_dir)],
                capture_output=True, timeout=10
            )
        except (PermissionError, OSError, subprocess.SubprocessError):
            pass  # Best effort - icon theme search path will still work

    def _build_ui(self):
        """Build the terminal UI"""
        if GTK_VERSION == 4:
            self._build_gtk4_ui()
        else:
            self._build_gtk3_ui()

    def _build_gtk4_ui(self):
        """Build GTK4 + libadwaita UI"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label=f"MeshForge v{__version__}"))
        main_box.append(header)

        # Terminal area
        if VTE_AVAILABLE:
            self.terminal = Vte.Terminal()
            self.terminal.set_vexpand(True)
            self.terminal.set_hexpand(True)

            # Configure terminal
            self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            self.terminal.set_mouse_autohide(True)
            self.terminal.set_scroll_on_output(True)
            self.terminal.set_scroll_on_keystroke(True)

            # Set dark theme colors
            self._apply_terminal_colors()

            # Scrolled container
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_vexpand(True)
            scrolled.set_child(self.terminal)
            main_box.append(scrolled)

            # Connect signals
            self.terminal.connect("child-exited", self._on_child_exited)

            # Spawn the TUI
            self._spawn_tui()
        else:
            # VTE not available - show error
            error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
            error_box.set_valign(Gtk.Align.CENTER)
            error_box.set_halign(Gtk.Align.CENTER)

            icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
            icon.set_pixel_size(64)
            error_box.append(icon)

            label = Gtk.Label(label="VTE Terminal Widget Not Available")
            label.add_css_class("title-1")
            error_box.append(label)

            hint = Gtk.Label(label="Install with: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0")
            hint.add_css_class("dim-label")
            error_box.append(hint)

            # Fallback button
            fallback_btn = Gtk.Button(label="Launch External Terminal")
            fallback_btn.add_css_class("suggested-action")
            fallback_btn.connect("clicked", self._launch_external_terminal)
            error_box.append(fallback_btn)

            main_box.append(error_box)

    def _build_gtk3_ui(self):
        """Build GTK3 UI"""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(main_box)

        if VTE_AVAILABLE:
            self.terminal = Vte.Terminal()
            self.terminal.set_vexpand(True)
            self.terminal.set_hexpand(True)

            # Configure terminal
            self.terminal.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            self.terminal.set_mouse_autohide(True)
            self.terminal.set_scroll_on_output(True)
            self.terminal.set_scroll_on_keystroke(True)

            # Set colors
            self._apply_terminal_colors()

            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_vexpand(True)
            scrolled.add(self.terminal)
            main_box.pack_start(scrolled, True, True, 0)

            # Connect signals
            self.terminal.connect("child-exited", self._on_child_exited)

            # Spawn the TUI
            self._spawn_tui()
        else:
            label = Gtk.Label(label="VTE not available. Install gir1.2-vte-2.91")
            main_box.pack_start(label, True, True, 0)

        main_box.show_all()

    def _apply_terminal_colors(self):
        """Apply terminal color scheme"""
        try:
            if GTK_VERSION == 4:
                # GTK4 uses RGBA
                bg = Gdk.RGBA()
                bg.parse("#1e1e2e")  # Dark background
                fg = Gdk.RGBA()
                fg.parse("#cdd6f4")  # Light foreground

                self.terminal.set_color_background(bg)
                self.terminal.set_color_foreground(fg)
            else:
                # GTK3 uses different API
                from gi.repository import Gdk as Gdk3
                bg = Gdk3.RGBA()
                bg.parse("#1e1e2e")
                fg = Gdk3.RGBA()
                fg.parse("#cdd6f4")

                self.terminal.set_color_background(bg)
                self.terminal.set_color_foreground(fg)
        except Exception as e:
            print(f"Color setup: {e}")

    def _spawn_tui(self):
        """Spawn the TUI launcher in the terminal"""
        # Find the TUI launcher
        src_dir = Path(__file__).parent
        tui_path = src_dir / 'launcher_tui/main.py'

        if not tui_path.exists():
            # Try alternative locations
            for alt_path in [
                Path('/opt/meshforge/src/launcher_tui/main.py'),
                Path(__file__).parent.parent / 'src' / 'launcher_tui/main.py',
            ]:
                if alt_path.exists():
                    tui_path = alt_path
                    break

        # Build command - run TUI with sudo
        argv = ['/usr/bin/sudo', '/usr/bin/python3', str(tui_path)]

        # Environment
        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        env['COLORTERM'] = 'truecolor'

        try:
            if GTK_VERSION == 4:
                # GTK4/VTE async spawn
                self.terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    str(src_dir),  # Working directory
                    argv,
                    list(f"{k}={v}" for k, v in env.items()),
                    GLib.SpawnFlags.DEFAULT,
                    None,  # child_setup
                    None,  # child_setup_data
                    -1,    # timeout
                    None,  # cancellable
                    self._spawn_callback,  # callback
                    None   # user_data
                )
            else:
                # GTK3 sync spawn
                self.terminal.spawn_sync(
                    Vte.PtyFlags.DEFAULT,
                    str(src_dir),
                    argv,
                    list(f"{k}={v}" for k, v in env.items()),
                    GLib.SpawnFlags.DEFAULT,
                    None,
                    None
                )
        except Exception as e:
            print(f"Spawn error: {e}")
            # Try fallback spawn
            self._spawn_fallback()

    def _spawn_callback(self, terminal, pid, error, user_data):
        """Callback for async spawn"""
        if error:
            print(f"Spawn error: {error}")
            self._spawn_fallback()
        else:
            print(f"TUI started with PID: {pid}")

    def _spawn_fallback(self):
        """Fallback spawn method"""
        try:
            src_dir = Path(__file__).parent
            tui_path = src_dir / 'launcher_tui/main.py'

            # Use simpler spawn
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                None,
                ['/bin/bash', '-c', f'sudo python3 {tui_path}'],
                None,
                GLib.SpawnFlags.DEFAULT,
                None, None, -1, None, None, None
            )
        except Exception as e:
            print(f"Fallback spawn error: {e}")

    def _on_child_exited(self, terminal, status):
        """Handle TUI exit"""
        print(f"TUI exited with status: {status}")
        # Close the window when TUI exits
        self.close()

    def _launch_external_terminal(self, button):
        """Launch TUI in external terminal as fallback"""
        import subprocess
        import shutil

        src_dir = Path(__file__).parent
        tui_path = src_dir / 'launcher_tui/main.py'

        terminals = [
            ['gnome-terminal', '--', 'sudo', 'python3', str(tui_path)],
            ['xfce4-terminal', '-e', f'sudo python3 {tui_path}'],
            ['konsole', '-e', 'sudo', 'python3', str(tui_path)],
            ['xterm', '-e', f'sudo python3 {tui_path}'],
        ]

        for term_cmd in terminals:
            if shutil.which(term_cmd[0]):
                try:
                    # Detach process from parent so it survives window close
                    subprocess.Popen(
                        term_cmd,
                        start_new_session=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    self.close()
                    return
                except Exception as e:
                    print(f"Failed to launch {term_cmd[0]}: {e}")
                    continue

        print("No terminal emulator found")


def main():
    """Main entry point"""
    if not VTE_AVAILABLE:
        print("VTE library not available.")
        print("Install with: sudo apt install gir1.2-vte-2.91 libvte-2.91-gtk4-0")
        print("\nFalling back to external terminal...")

        # Fallback to external terminal
        import subprocess
        import shutil

        src_dir = Path(__file__).parent
        tui_path = src_dir / 'launcher_tui/main.py'

        if not tui_path.exists():
            # Try alternative paths
            for alt_path in [
                Path('/opt/meshforge/src/launcher_tui/main.py'),
                Path(__file__).parent.parent / 'src' / 'launcher_tui/main.py',
            ]:
                if alt_path.exists():
                    tui_path = alt_path
                    break

        if not tui_path.exists():
            print(f"Error: TUI launcher not found at {tui_path}")
            sys.exit(1)

        # Terminal launch configurations (terminal, args_format)
        terminals = [
            ('gnome-terminal', ['--', 'sudo', 'python3', str(tui_path)]),
            ('xfce4-terminal', ['-e', f'sudo python3 {tui_path}']),
            ('konsole', ['-e', 'sudo', 'python3', str(tui_path)]),
            ('xterm', ['-fa', 'Monospace', '-fs', '11', '-geometry', '100x35',
                      '-bg', '#1e1e2e', '-fg', '#cdd6f4',
                      '-e', f'sudo python3 {tui_path}']),
            ('lxterminal', ['-e', f'sudo python3 {tui_path}']),
            ('mate-terminal', ['-e', f'sudo python3 {tui_path}']),
            ('terminator', ['-e', f'sudo python3 {tui_path}']),
            ('tilix', ['-e', f'sudo python3 {tui_path}']),
            ('kitty', ['sudo', 'python3', str(tui_path)]),
            ('alacritty', ['-e', 'sudo', 'python3', str(tui_path)]),
        ]

        launched = False
        for term, args in terminals:
            term_path = shutil.which(term)
            if term_path:
                print(f"Launching with {term}...")
                try:
                    # Long timeout for interactive terminal (4 hours max session)
                    result = subprocess.run(
                        [term_path] + args,
                        check=False,
                        timeout=14400  # 4 hours - reasonable max for interactive session
                    )
                    launched = True
                    sys.exit(result.returncode)
                except subprocess.TimeoutExpired:
                    print(f"Terminal session timed out after 4 hours")
                    launched = True
                    sys.exit(0)
                except subprocess.SubprocessError as e:
                    print(f"Failed to launch {term}: {e}")
                    continue

        if not launched:
            # Last resort: try x-terminal-emulator
            xterm = shutil.which('x-terminal-emulator')
            if xterm:
                print("Launching with x-terminal-emulator...")
                try:
                    # Long timeout for interactive terminal (4 hours max session)
                    subprocess.run(
                        [xterm, '-e', f'sudo python3 {tui_path}'],
                        check=False,
                        timeout=14400  # 4 hours - reasonable max for interactive session
                    )
                except subprocess.TimeoutExpired:
                    print("Terminal session timed out after 4 hours")
            else:
                print("\nNo terminal emulator found!")
                print("Please run directly: sudo python3 " + str(tui_path))
                sys.exit(1)
        return

    app = MeshForgeVTEApp()
    app.run(sys.argv)


if __name__ == '__main__':
    main()
