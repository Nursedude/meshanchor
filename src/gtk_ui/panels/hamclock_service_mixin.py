"""
HamClock Service Management Mixin for MeshForge.

Provides HamClock service control and installation methods extracted from
HamClockPanel to reduce file size and improve maintainability.

Note: This mixin uses self._schedule_timer() which is provided by the parent
class (HamClockPanel). Timer cleanup is handled via the parent's _pending_timers
list and _cancel_timers() method.
"""

import subprocess
import threading
import logging
from gi.repository import GLib, Gio

# Try to use centralized service checker
try:
    from utils.service_check import check_service, check_systemd_service, ServiceState
    _HAS_SERVICE_CHECK = True
except ImportError:
    _HAS_SERVICE_CHECK = False

logger = logging.getLogger(__name__)


class HamClockServiceMixin:
    """Mixin providing HamClock service management methods."""

    def _check_service_status(self):
        """Check if HamClock service is running"""
        logger.debug("[HamClock] Checking service status...")

        def check():
            status = {
                'installed': False,
                'running': False,
                'service_name': None,
                'error': None
            }

            # Check for different HamClock service names
            service_names = ['hamclock', 'hamclock-web', 'hamclock-systemd']

            for name in service_names:
                try:
                    # Use centralized service checker if available
                    if _HAS_SERVICE_CHECK:
                        is_running, is_enabled = check_systemd_service(name)
                        if is_running:
                            status['installed'] = True
                            status['running'] = True
                            status['service_name'] = name
                            break
                        elif is_enabled or is_running is not None:
                            # Service exists but not running
                            status['installed'] = True
                            status['service_name'] = name
                    else:
                        # Fallback to direct systemctl call
                        result = subprocess.run(
                            ['systemctl', 'is-active', name],
                            capture_output=True, text=True, timeout=5
                        )
                        if result.returncode == 0 and result.stdout.strip() == 'active':
                            status['installed'] = True
                            status['running'] = True
                            status['service_name'] = name
                            break

                        # Check if installed but not running
                        result2 = subprocess.run(
                            ['systemctl', 'is-enabled', name],
                            capture_output=True, text=True, timeout=5
                        )
                        if result2.returncode == 0 or 'disabled' in result2.stdout:
                            status['installed'] = True
                            status['service_name'] = name

                except subprocess.TimeoutExpired:
                    logger.debug(f"[HamClock] Timeout checking service: {name}")
                except FileNotFoundError:
                    logger.debug("[HamClock] systemctl not found")
                    break
                except Exception as e:
                    logger.debug(f"[HamClock] Error checking service {name}: {e}")

            # Also check for running hamclock process (might be started manually)
            if not status['running']:
                try:
                    result = subprocess.run(
                        ['pgrep', '-f', 'hamclock'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        status['running'] = True
                        status['service_name'] = 'hamclock (process)'
                except subprocess.TimeoutExpired:
                    logger.debug("[HamClock] Timeout checking process")
                except FileNotFoundError:
                    logger.debug("[HamClock] pgrep not found")
                except Exception as e:
                    logger.debug(f"[HamClock] Error checking process: {e}")

            GLib.idle_add(self._update_service_status, status)

        threading.Thread(target=check, daemon=True).start()
        return False  # Don't repeat

    def _update_service_status(self, status):
        """Update the service status display"""
        # Store detected service name for copy commands
        self._detected_service = status.get('service_name')

        if status['running']:
            self.service_status_icon.set_from_icon_name("emblem-default-symbolic")
            self.service_status_label.set_label("HamClock Running")
            if status['service_name']:
                self.service_detail_label.set_label(f"Service: {status['service_name']}")
                self.cmd_label.set_label(f"Commands use: {status['service_name']}")
            logger.debug(f"[HamClock] Service running: {status['service_name']}")
        elif status['installed']:
            self.service_status_icon.set_from_icon_name("dialog-warning-symbolic")
            self.service_status_label.set_label("HamClock Stopped")
            self.service_detail_label.set_label(f"Service: {status['service_name']}")
            self.cmd_label.set_label(f"Commands use: {status['service_name']}")
            logger.debug("[HamClock] Service installed but stopped")
        else:
            self.service_status_icon.set_from_icon_name("dialog-question-symbolic")
            self.service_status_label.set_label("HamClock Not Installed")
            self.service_detail_label.set_label("Install via hamclock-systemd or official packages")
            self.cmd_label.set_label("Install HamClock first, then use commands")
            logger.debug("[HamClock] Service not found")

        return False

    def _control_service(self, action):
        """Control HamClock service using D-Bus systemd interface.

        This is the proper GTK/GNOME way to control services - it uses
        polkit for authorization automatically through the system bus.
        """
        service_name = getattr(self, '_detected_service', None) or 'hamclock'
        unit_name = f"{service_name}.service"
        self.main_window.set_status_message(f"Attempting to {action} {service_name}...")
        logger.info(f"[HamClock] Service control via D-Bus: {action} {unit_name}")

        def do_control():
            try:
                # Connect to the system bus
                bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

                # Get the systemd manager object
                systemd = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.NONE,
                    None,
                    'org.freedesktop.systemd1',
                    '/org/freedesktop/systemd1',
                    'org.freedesktop.systemd1.Manager',
                    None
                )

                # Call the appropriate method
                if action == 'start':
                    systemd.call_sync('StartUnit', GLib.Variant('(ss)', (unit_name, 'replace')),
                                     Gio.DBusCallFlags.NONE, 30000, None)
                elif action == 'stop':
                    systemd.call_sync('StopUnit', GLib.Variant('(ss)', (unit_name, 'replace')),
                                     Gio.DBusCallFlags.NONE, 30000, None)
                elif action == 'restart':
                    systemd.call_sync('RestartUnit', GLib.Variant('(ss)', (unit_name, 'replace')),
                                     Gio.DBusCallFlags.NONE, 30000, None)

                GLib.idle_add(self._on_service_control_success, action, service_name)

            except GLib.Error as e:
                error_msg = str(e)
                logger.warning(f"[HamClock] D-Bus control failed: {error_msg}")

                if 'org.freedesktop.PolicyKit' in error_msg or 'not authorized' in error_msg.lower():
                    # Try subprocess fallback with pkexec
                    GLib.idle_add(self._control_service_subprocess, action, service_name)
                else:
                    GLib.idle_add(self._on_service_control_failed, action, error_msg)

            except Exception as e:
                logger.error(f"[HamClock] Service control error: {e}")
                GLib.idle_add(self._on_service_control_failed, action, str(e))

        threading.Thread(target=do_control, daemon=True).start()

    def _control_service_subprocess(self, action, service_name):
        """Fallback service control using subprocess with pkexec."""
        logger.info(f"[HamClock] Trying subprocess fallback for {action}")

        # Import admin helper if available
        try:
            from utils.system import run_admin_command_async
            HAS_ADMIN_HELPER = True
        except ImportError:
            HAS_ADMIN_HELPER = False
            run_admin_command_async = None

        if HAS_ADMIN_HELPER and run_admin_command_async:
            # Use proper privilege escalation
            cmd = ['systemctl', action, service_name]

            def on_complete(success, output, error):
                if success:
                    GLib.idle_add(self._on_service_control_success, action, service_name)
                else:
                    GLib.idle_add(self._show_manual_command, action, service_name)

            run_admin_command_async(cmd, on_complete)
        else:
            # Show manual command to user
            self._show_manual_command(action, service_name)

        return False

    def _on_service_control_success(self, action, service_name):
        """Handle successful service control"""
        self.main_window.set_status_message(f"Service {action}ed successfully")
        logger.info(f"[HamClock] Service {action}ed: {service_name}")
        # Refresh status using parent's timer scheduler for proper tracking
        self._schedule_timer(1000, self._check_service_status)
        return False

    def _on_service_control_failed(self, action, error):
        """Handle failed service control"""
        self.main_window.set_status_message(f"Failed to {action}: {error}")
        logger.warning(f"[HamClock] Service {action} failed: {error}")
        return False

    def _show_manual_command(self, action, service_name):
        """Show manual command when privilege escalation unavailable"""
        cmd = f"sudo systemctl {action} {service_name}"
        self.main_window.set_status_message(f"Run manually: {cmd}")

        # Also copy to clipboard as fallback
        try:
            display = self.get_display()
            clipboard = display.get_clipboard()
            clipboard.set(cmd)
            self.main_window.set_status_message(f"Copied to clipboard: {cmd}")
        except Exception:
            pass
        return False

    def _find_hamclock_service(self):
        """Find the correct HamClock service name"""
        service_names = ['hamclock', 'hamclock-web', 'hamclock-systemd']

        for name in service_names:
            try:
                result = subprocess.run(
                    ['systemctl', 'list-unit-files', f'{name}.service'],
                    capture_output=True, text=True, timeout=5
                )
                if name in result.stdout:
                    return name
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            except Exception as e:
                logger.debug(f"[HamClock] Error finding service: {e}")

        return 'hamclock'  # Default fallback

    def _service_action_complete(self, action, success, error):
        """Handle service action completion (legacy, kept for compatibility)"""
        if success:
            self.main_window.set_status_message(f"HamClock {action} successful")
        else:
            self.main_window.set_status_message(f"HamClock {action} failed: {error}")
        # Use parent's timer scheduler for proper tracking
        self._schedule_timer(500, self._check_service_status)
        return False
