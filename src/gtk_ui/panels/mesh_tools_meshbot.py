"""
MeshBot Tab Mixin - Extracted from mesh_tools.py

Handles MeshBot management including:
- Status monitoring
- Start/stop controls
- Configuration management
- Connection mode settings (Serial vs TCP)
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import threading
import subprocess
import os
from pathlib import Path

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Import UI standards
try:
    from utils.gtk_helpers import UI
    HAS_UI_HELPERS = True
except ImportError:
    HAS_UI_HELPERS = False


class MeshBotTabMixin:
    """
    Mixin providing MeshBot tab functionality.

    Requires parent class to provide:
    - self._notebook: Gtk.Notebook to add tab to
    - self._settings: dict with settings including "meshbot_path"
    - self._log_message(str): Method to log messages
    - self._set_log_text(str): Method to set log text
    - self._open_folder(str): Method to open folder
    - self._open_url(str): Method to open URL
    """

    def _add_meshbot_tab(self):
        """Add MeshBot management tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(15)

        # Status section
        status_frame = Gtk.Frame()
        status_frame.set_label("MeshBot Status")
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        status_box.set_margin_start(15)
        status_box.set_margin_end(15)
        status_box.set_margin_top(10)
        status_box.set_margin_bottom(10)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self._meshbot_status_icon = Gtk.Image.new_from_icon_name("emblem-question-symbolic")
        self._meshbot_status_icon.set_pixel_size(32)
        status_row.append(self._meshbot_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self._meshbot_status_label = Gtk.Label(label="Checking...")
        self._meshbot_status_label.set_xalign(0)
        self._meshbot_status_label.add_css_class("heading")
        status_info.append(self._meshbot_status_label)

        self._meshbot_detail_label = Gtk.Label(label="")
        self._meshbot_detail_label.set_xalign(0)
        self._meshbot_detail_label.add_css_class("dim-label")
        status_info.append(self._meshbot_detail_label)

        status_row.append(status_info)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self._start_btn = Gtk.Button(label="Start")
        self._start_btn.add_css_class("suggested-action")
        self._start_btn.connect("clicked", self._on_start_bot)
        btn_box.append(self._start_btn)

        self._stop_btn = Gtk.Button(label="Stop")
        self._stop_btn.add_css_class("destructive-action")
        self._stop_btn.connect("clicked", self._on_stop_bot)
        btn_box.append(self._stop_btn)

        install_btn = Gtk.Button(label="Install")
        install_btn.connect("clicked", self._on_install_bot)
        btn_box.append(install_btn)

        status_row.append(btn_box)
        status_box.append(status_row)

        status_frame.set_child(status_box)
        box.append(status_frame)

        # Config section
        config_frame = Gtk.Frame()
        config_frame.set_label("Configuration")
        config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        config_box.set_margin_start(15)
        config_box.set_margin_end(15)
        config_box.set_margin_top(10)
        config_box.set_margin_bottom(10)

        # Path entry
        path_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        path_row.append(Gtk.Label(label="Install Path:"))
        self._path_entry = Gtk.Entry()
        self._path_entry.set_text(self._settings.get("meshbot_path", "/opt/meshing-around"))
        self._path_entry.set_hexpand(True)
        path_row.append(self._path_entry)
        config_box.append(path_row)

        # Config file buttons
        config_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        edit_config_btn = Gtk.Button(label="Edit Config")
        edit_config_btn.connect("clicked", self._on_edit_config)
        edit_config_btn.set_tooltip_text("Edit config.ini in text editor")
        config_btn_row.append(edit_config_btn)

        view_config_btn = Gtk.Button(label="View Config")
        view_config_btn.connect("clicked", self._on_view_config)
        view_config_btn.set_tooltip_text("View config.ini in log viewer below")
        config_btn_row.append(view_config_btn)

        open_folder_btn = Gtk.Button(label="Open Folder")
        open_folder_btn.connect("clicked", self._on_open_meshbot_folder)
        config_btn_row.append(open_folder_btn)

        config_box.append(config_btn_row)

        # Log/Debug buttons row
        log_btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        view_log_btn = Gtk.Button(label="View Log")
        view_log_btn.connect("clicked", self._on_view_meshbot_log)
        view_log_btn.set_tooltip_text("View MeshBot log file")
        log_btn_row.append(view_log_btn)

        tail_log_btn = Gtk.Button(label="Tail Log")
        tail_log_btn.connect("clicked", self._on_tail_meshbot_log)
        tail_log_btn.set_tooltip_text("Show last 50 lines of log")
        log_btn_row.append(tail_log_btn)

        journal_btn = Gtk.Button(label="Journal")
        journal_btn.connect("clicked", self._on_view_journal)
        journal_btn.set_tooltip_text("View systemd journal for mesh_bot")
        log_btn_row.append(journal_btn)

        config_box.append(log_btn_row)

        config_frame.set_child(config_box)
        box.append(config_frame)

        # Connection Mode section
        conn_frame = Gtk.Frame()
        conn_frame.set_label("Connection Mode")
        conn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        conn_box.set_margin_start(15)
        conn_box.set_margin_end(15)
        conn_box.set_margin_top(10)
        conn_box.set_margin_bottom(10)

        # Warning info
        warn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        warn_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        warn_box.append(warn_icon)
        warn_label = Gtk.Label(
            label="Serial mode blocks browser access. Use TCP mode for shared access."
        )
        warn_label.set_wrap(True)
        warn_label.set_xalign(0)
        warn_label.add_css_class("dim-label")
        warn_box.append(warn_label)
        conn_box.append(warn_box)

        # meshtasticd status
        meshtasticd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        meshtasticd_row.append(Gtk.Label(label="meshtasticd:"))
        self._meshtasticd_status = Gtk.Label(label="Checking...")
        self._meshtasticd_status.add_css_class("dim-label")
        meshtasticd_row.append(self._meshtasticd_status)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        meshtasticd_row.append(spacer2)

        start_meshtasticd_btn = Gtk.Button(label="Start meshtasticd")
        start_meshtasticd_btn.connect("clicked", self._on_start_meshtasticd)
        meshtasticd_row.append(start_meshtasticd_btn)

        conn_box.append(meshtasticd_row)

        # Connection mode selector
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mode_row.append(Gtk.Label(label="MeshBot connects via:"))

        self._conn_mode = Gtk.ComboBoxText()
        self._conn_mode.append("serial", "Serial (exclusive - blocks browser)")
        self._conn_mode.append("tcp", "TCP (shared - browser compatible)")
        self._conn_mode.set_active_id("tcp")
        self._conn_mode.set_tooltip_text("TCP mode requires meshtasticd running")
        mode_row.append(self._conn_mode)

        apply_mode_btn = Gtk.Button(label="Apply to Config")
        apply_mode_btn.add_css_class("suggested-action")
        apply_mode_btn.connect("clicked", self._on_apply_connection_mode)
        apply_mode_btn.set_tooltip_text("Update mesh_bot config.ini with selected mode")
        mode_row.append(apply_mode_btn)

        conn_box.append(mode_row)

        # Quick browser access
        browser_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        browser_label = Gtk.Label(label="Quick access:")
        browser_label.add_css_class("dim-label")
        browser_row.append(browser_label)

        open_browser_btn = Gtk.Button(label="Open Meshtastic Web")
        open_browser_btn.connect("clicked", self._on_open_meshtastic_web)
        open_browser_btn.set_tooltip_text("Open Meshtastic web interface")
        browser_row.append(open_browser_btn)

        conn_box.append(browser_row)

        conn_frame.set_child(conn_box)
        box.append(conn_frame)

        # Features overview
        features_frame = Gtk.Frame()
        features_frame.set_label("MeshBot Features")
        features_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        features_box.set_margin_start(15)
        features_box.set_margin_end(15)
        features_box.set_margin_top(10)
        features_box.set_margin_bottom(10)

        features = [
            ("BBS Messaging", "Store-and-forward bulletin board"),
            ("Weather/Alerts", "NOAA, USGS, FEMA emergency data"),
            ("Games", "DopeWars, BlackJack, Poker"),
            ("Inventory/POS", "Point-of-sale with cart system"),
        ]

        for title, desc in features:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title_lbl = Gtk.Label(label=f"{title}:")
            title_lbl.set_xalign(0)
            title_lbl.add_css_class("heading")
            title_lbl.set_width_chars(15)
            row.append(title_lbl)

            desc_lbl = Gtk.Label(label=desc)
            desc_lbl.set_xalign(0)
            desc_lbl.add_css_class("dim-label")
            row.append(desc_lbl)
            features_box.append(row)

        features_frame.set_child(features_box)
        box.append(features_frame)

        scrolled.set_child(box)

        # Tab label with icon
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        tab_box.append(Gtk.Image.new_from_icon_name("mail-send-symbolic"))
        tab_box.append(Gtk.Label(label="MeshBot"))

        self._notebook.append_page(scrolled, tab_box)

    # =========================================================================
    # MeshBot Status Handlers
    # =========================================================================

    def _check_meshtasticd_status(self):
        """Check if meshtasticd is running"""
        def check():
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'meshtasticd'],
                    capture_output=True, text=True, timeout=5
                )
                running = result.returncode == 0 and result.stdout.strip()
                GLib.idle_add(self._update_meshtasticd_status, running)
            except Exception:
                GLib.idle_add(self._update_meshtasticd_status, False)

        threading.Thread(target=check, daemon=True).start()

    def _update_meshtasticd_status(self, running: bool):
        """Update meshtasticd status display"""
        if not hasattr(self, '_meshtasticd_status') or self._meshtasticd_status is None:
            return False
        try:
            if running:
                self._meshtasticd_status.set_label("Running (TCP available)")
                self._meshtasticd_status.remove_css_class("error")
                self._meshtasticd_status.add_css_class("success")
            else:
                self._meshtasticd_status.set_label("Not Running")
                self._meshtasticd_status.remove_css_class("success")
                self._meshtasticd_status.add_css_class("error")
        except Exception:
            pass
        return False

    def _on_start_meshtasticd(self, button):
        """Start meshtasticd service"""
        self._log_message("Starting meshtasticd...")

        def do_start():
            try:
                result = subprocess.run(
                    ['sudo', 'systemctl', 'start', 'meshtasticd'],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    GLib.idle_add(self._log_message, "meshtasticd started via systemctl")
                else:
                    subprocess.Popen(
                        ['sudo', 'meshtasticd'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    GLib.idle_add(self._log_message, "meshtasticd started directly")

                import time
                time.sleep(2)
                GLib.idle_add(self._check_meshtasticd_status)

            except Exception as e:
                GLib.idle_add(self._log_message, f"Failed to start meshtasticd: {e}")

        threading.Thread(target=do_start, daemon=True).start()

    def _on_apply_connection_mode(self, button):
        """Apply connection mode to mesh_bot config"""
        mode = self._conn_mode.get_active_id()
        meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        config_path = Path(meshbot_path) / "config.ini"

        if not config_path.exists():
            self._log_message(f"Config not found: {config_path}")
            return

        self._log_message(f"Applying {mode} connection mode...")

        def do_apply():
            try:
                import configparser
                config = configparser.ConfigParser()
                config.read(str(config_path))

                if 'interface' not in config:
                    config['interface'] = {}

                if mode == 'tcp':
                    config['interface']['type'] = 'tcp'
                    config['interface']['hostname'] = 'localhost'
                    config['interface']['port'] = '4403'
                    if 'port' in config['interface'] and config['interface']['port'].startswith('/dev'):
                        del config['interface']['port']
                else:
                    config['interface']['type'] = 'serial'
                    if 'port' not in config['interface'] or not config['interface']['port'].startswith('/dev'):
                        config['interface']['port'] = '/dev/ttyUSB0'

                with open(str(config_path), 'w') as f:
                    config.write(f)

                GLib.idle_add(self._log_message, f"Config updated to {mode} mode")
                GLib.idle_add(self._log_message, "Restart MeshBot for changes to take effect")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Failed to update config: {e}")

        threading.Thread(target=do_apply, daemon=True).start()

    def _on_open_meshtastic_web(self, button):
        """Open Meshtastic web interface"""
        def check_and_open():
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, text=True, timeout=5
                )
                meshbot_running = result.returncode == 0 and result.stdout.strip()

                if meshbot_running:
                    GLib.idle_add(self._log_message, "MeshBot is running - may conflict with browser")
                    GLib.idle_add(self._log_message, "Consider using TCP mode or stopping MeshBot")

                GLib.idle_add(self._open_url, "https://localhost:9443")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Error: {e}")
                GLib.idle_add(self._open_url, "https://localhost:9443")

        threading.Thread(target=check_and_open, daemon=True).start()

    def _check_meshbot_status(self):
        """Check MeshBot installation and running status"""
        def check():
            status = {"installed": False, "running": False, "path": None}

            meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"

            if Path(meshbot_path).exists() and (Path(meshbot_path) / "mesh_bot.py").exists():
                status["installed"] = True
                status["path"] = meshbot_path

            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'mesh_bot.py'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    status["running"] = True
            except Exception:
                pass

            GLib.idle_add(self._update_meshbot_status, status)

        threading.Thread(target=check, daemon=True).start()

    def _update_meshbot_status(self, status):
        """Update MeshBot status display"""
        if status["running"]:
            self._meshbot_status_icon.set_from_icon_name("emblem-default-symbolic")
            self._meshbot_status_label.set_label("MeshBot Running")
            self._meshbot_detail_label.set_label(f"Path: {status['path']}")
            self._start_btn.set_sensitive(False)
            self._stop_btn.set_sensitive(True)
        elif status["installed"]:
            self._meshbot_status_icon.set_from_icon_name("media-playback-stop-symbolic")
            self._meshbot_status_label.set_label("MeshBot Stopped")
            self._meshbot_detail_label.set_label(f"Path: {status['path']}")
            self._start_btn.set_sensitive(True)
            self._stop_btn.set_sensitive(False)
        else:
            self._meshbot_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self._meshbot_status_label.set_label("MeshBot Not Installed")
            self._meshbot_detail_label.set_label("Click Install to set up")
            self._start_btn.set_sensitive(False)
            self._stop_btn.set_sensitive(False)

    # =========================================================================
    # MeshBot Control Handlers
    # =========================================================================

    def _on_start_bot(self, button):
        """Start MeshBot - use systemd service if available, otherwise direct execution"""
        meshbot_path = self._path_entry.get_text().strip()
        script_path = Path(meshbot_path) / "mesh_bot.py"

        if not script_path.exists():
            self._log_message("Error: mesh_bot.py not found")
            return

        config_path = Path(meshbot_path) / "config.ini"
        if not config_path.exists():
            self._log_message("Warning: config.ini not found - creating from template...")
            template = Path(meshbot_path) / "config.template"
            if template.exists():
                real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
                subprocess.run(['sudo', 'cp', str(template), str(config_path)], timeout=10)
                subprocess.run(['sudo', 'chown', f'{real_user}:{real_user}', str(config_path)], timeout=10)
            else:
                self._log_message("Error: No config.ini or config.template found")
                return

        self._log_message("Starting MeshBot...")

        def do_start():
            try:
                service_check = subprocess.run(
                    ['systemctl', 'cat', 'mesh_bot.service'],
                    capture_output=True, text=True, timeout=5
                )

                if service_check.returncode == 0:
                    GLib.idle_add(self._log_message, "Using systemd service...")
                    result = subprocess.run(
                        ['sudo', 'systemctl', 'start', 'mesh_bot.service'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        GLib.idle_add(self._log_message, "MeshBot service started")
                        GLib.idle_add(self._check_meshbot_status)
                    else:
                        GLib.idle_add(self._log_message, f"Service start failed: {result.stderr}")
                    return

                venv_path = Path(meshbot_path) / "venv"
                venv_python = venv_path / "bin" / "python3"

                if venv_python.exists():
                    cmd = [str(venv_python), str(script_path)]
                    GLib.idle_add(self._log_message, "Using virtual environment")
                else:
                    cmd = ['python3', str(script_path)]
                    GLib.idle_add(self._log_message, "Using system Python (no venv found)")

                GLib.idle_add(self._log_message, f"Running: {' '.join(cmd)}")

                process = subprocess.Popen(
                    cmd,
                    cwd=meshbot_path,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True
                )

                import time
                time.sleep(3)

                if process.poll() is None:
                    GLib.idle_add(self._log_message, "MeshBot started successfully")
                    GLib.idle_add(self._check_meshbot_status)
                else:
                    exit_code = process.returncode
                    output = process.stdout.read() if process.stdout else ""
                    GLib.idle_add(self._log_message, f"MeshBot exited with code {exit_code}")
                    if output:
                        lines = output.strip().split('\n')[:10]
                        for line in lines:
                            GLib.idle_add(self._log_message, f"  {line}")
                    if not venv_python.exists():
                        GLib.idle_add(self._log_message, "Tip: Create venv with:")
                        GLib.idle_add(self._log_message, f"  cd {meshbot_path} && python3 -m venv venv")
                        GLib.idle_add(self._log_message, "  venv/bin/pip install -r requirements.txt")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Start error: {e}")

        threading.Thread(target=do_start, daemon=True).start()

    def _on_stop_bot(self, button):
        """Stop MeshBot - use systemd service if available"""
        self._log_message("Stopping MeshBot...")

        def do_stop():
            try:
                service_check = subprocess.run(
                    ['systemctl', 'is-active', 'mesh_bot.service'],
                    capture_output=True, text=True, timeout=5
                )

                if service_check.stdout.strip() == 'active':
                    GLib.idle_add(self._log_message, "Stopping systemd service...")
                    result = subprocess.run(
                        ['sudo', 'systemctl', 'stop', 'mesh_bot.service'],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        GLib.idle_add(self._log_message, "MeshBot service stopped")
                    else:
                        GLib.idle_add(self._log_message, f"Service stop failed: {result.stderr}")
                else:
                    subprocess.run(['pkill', '-f', 'mesh_bot.py'], timeout=10)
                    GLib.idle_add(self._log_message, "MeshBot stopped")

                import time
                time.sleep(1)
                GLib.idle_add(self._check_meshbot_status)
            except Exception as e:
                GLib.idle_add(self._log_message, f"Stop error: {e}")

        threading.Thread(target=do_stop, daemon=True).start()

    def _on_install_bot(self, button):
        """Install MeshBot"""
        install_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        self._log_message(f"Installing MeshBot to {install_path}...")

        def do_install():
            try:
                install_dir = Path(install_path)
                real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))

                if install_dir.exists():
                    GLib.idle_add(self._log_message, "Updating existing installation...")
                    result = subprocess.run(
                        ['git', '-C', str(install_dir), 'pull'],
                        capture_output=True, text=True, timeout=120
                    )
                else:
                    GLib.idle_add(self._log_message, "Cloning repository...")
                    result = subprocess.run(
                        ['sudo', 'git', 'clone',
                         'https://github.com/SpudGunMan/meshing-around.git',
                         str(install_dir)],
                        capture_output=True, text=True, timeout=300
                    )

                if result.returncode != 0:
                    GLib.idle_add(self._log_message, f"Install failed: {result.stderr}")
                    return

                GLib.idle_add(self._log_message, "Setting permissions...")
                subprocess.run(['sudo', 'chown', '-R', f'{real_user}:{real_user}', str(install_dir)], timeout=60)

                venv_path = install_dir / "venv"
                if not venv_path.exists():
                    GLib.idle_add(self._log_message, "Creating virtual environment...")
                    venv_result = subprocess.run(
                        ['python3', '-m', 'venv', str(venv_path)],
                        capture_output=True, text=True, timeout=120,
                        cwd=str(install_dir)
                    )
                    if venv_result.returncode != 0:
                        GLib.idle_add(self._log_message, f"Venv creation failed: {venv_result.stderr}")
                        GLib.idle_add(self._log_message, "You can create it manually later")
                    else:
                        req_file = install_dir / "requirements.txt"
                        if req_file.exists():
                            GLib.idle_add(self._log_message, "Installing dependencies (this may take a while)...")
                            pip_path = venv_path / "bin" / "pip"
                            pip_result = subprocess.run(
                                [str(pip_path), 'install', '-r', str(req_file)],
                                capture_output=True, text=True, timeout=600,
                                cwd=str(install_dir)
                            )
                            if pip_result.returncode == 0:
                                GLib.idle_add(self._log_message, "Dependencies installed!")
                            else:
                                GLib.idle_add(self._log_message, f"Pip install warning: {pip_result.stderr[:200]}")

                GLib.idle_add(self._log_message, "Installation complete!")
                GLib.idle_add(self._check_meshbot_status)

            except Exception as e:
                GLib.idle_add(self._log_message, f"Install error: {e}")

        threading.Thread(target=do_install, daemon=True).start()

    # =========================================================================
    # MeshBot Config/Log Handlers
    # =========================================================================

    def _on_edit_config(self, button):
        """Open config file in editor"""
        meshbot_path = self._path_entry.get_text().strip()
        config_path = Path(meshbot_path) / "config.ini"

        if not config_path.exists():
            template = Path(meshbot_path) / "config.template"
            if template.exists():
                subprocess.run(['sudo', 'cp', str(template), str(config_path)], timeout=10)
            else:
                self._log_message("No config file found")
                return

        real_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'pi'))
        subprocess.run(['sudo', 'chown', f'{real_user}:{real_user}', str(config_path)], timeout=10)
        subprocess.run(['sudo', 'chmod', '644', str(config_path)], timeout=10)

        try:
            subprocess.Popen(
                ['sudo', '-u', real_user, 'xdg-open', str(config_path)],
                start_new_session=True
            )
            self._log_message(f"Opening {config_path} in editor")
        except Exception as e:
            self._log_message(f"Error opening editor: {e}")

    def _on_view_config(self, button):
        """View config in log viewer"""
        meshbot_path = self._path_entry.get_text().strip()
        config_path = Path(meshbot_path) / "config.ini"

        if config_path.exists():
            content = config_path.read_text()
            self._set_log_text(f"=== {config_path} ===\n\n{content}")
        else:
            self._log_message("Config file not found")

    def _on_open_meshbot_folder(self, button):
        """Open MeshBot folder"""
        meshbot_path = self._path_entry.get_text().strip()
        self._open_folder(meshbot_path)

    def _on_view_meshbot_log(self, button):
        """View MeshBot log file"""
        meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        log_files = [
            Path(meshbot_path) / "logs" / "meshbot.log",
            Path(meshbot_path) / "logs" / "messages.log",
            Path(meshbot_path) / "prior_log.txt",
            Path(meshbot_path) / "prior_debug_log.txt",
        ]

        for log_path in log_files:
            if log_path.exists():
                try:
                    content = log_path.read_text()
                    self._set_log_text(f"=== {log_path} ===\n\n{content[-10000:]}")
                    self._log_message(f"Loaded log from {log_path}")
                    return
                except Exception as e:
                    self._log_message(f"Error reading {log_path}: {e}")

        self._log_message("No log files found")
        self._log_message(f"Checked: {meshbot_path}/logs/meshbot.log")
        self._log_message("Logs are created after MeshBot runs with logging enabled")

    def _on_tail_meshbot_log(self, button):
        """Show last 50 lines of log"""
        meshbot_path = self._path_entry.get_text().strip() or "/opt/meshing-around"
        log_files = [
            Path(meshbot_path) / "logs" / "meshbot.log",
            Path(meshbot_path) / "logs" / "messages.log",
            Path(meshbot_path) / "prior_log.txt",
            Path(meshbot_path) / "prior_debug_log.txt",
        ]

        for log_path in log_files:
            if log_path.exists():
                try:
                    lines = log_path.read_text().strip().split('\n')
                    tail = '\n'.join(lines[-50:])
                    self._set_log_text(f"=== Last 50 lines of {log_path.name} ===\n\n{tail}")
                    return
                except Exception as e:
                    self._log_message(f"Error reading {log_path}: {e}")

        self._log_message("No log files found")
        self._log_message("Enable logging in config.ini: syslog_to_file = True")

    def _on_view_journal(self, button):
        """View systemd journal for mesh_bot service"""
        self._log_message("Fetching journal entries...")

        def fetch_journal():
            try:
                result = subprocess.run(
                    ['journalctl', '--no-pager', '-n', '100', '-u', 'mesh_bot.service'],
                    capture_output=True, text=True, timeout=30
                )

                if result.stdout.strip() and '-- No entries --' not in result.stdout:
                    GLib.idle_add(self._set_log_text, f"=== Journal (mesh_bot.service) ===\n\n{result.stdout}")
                    return

                result2 = subprocess.run(
                    ['journalctl', '--no-pager', '-n', '100', '-g', 'mesh_bot'],
                    capture_output=True, text=True, timeout=30
                )

                if result2.stdout.strip() and '-- No entries --' not in result2.stdout:
                    GLib.idle_add(self._set_log_text, f"=== Journal (mesh_bot) ===\n\n{result2.stdout}")
                else:
                    GLib.idle_add(self._log_message, "No journal entries found")
                    GLib.idle_add(self._log_message, "Use View Log or Tail Log to see file-based logs")

            except Exception as e:
                GLib.idle_add(self._log_message, f"Journal error: {e}")

        threading.Thread(target=fetch_journal, daemon=True).start()
