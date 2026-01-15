"""
PanelBase - Standardized GTK4 panel infrastructure for MeshForge

Provides automatic resource management to prevent:
- Timer leaks (GLib.timeout_add handlers not cancelled)
- Signal handler leaks (GTK signals not disconnected)
- Thread race conditions (callbacks firing after destruction)
- File descriptor exhaustion (sockets not closed)

Usage:
    from gtk_ui.panel_base import PanelBase

    class MyPanel(PanelBase):
        def __init__(self, main_window):
            super().__init__(main_window)
            # Your initialization here

        def _build_ui(self):
            # Build your UI here
            # Use self._schedule_timer() instead of GLib.timeout_add()
            # Use self._connect_signal() instead of widget.connect()
            pass

All panels inheriting from PanelBase will automatically:
- Track and cancel timers on cleanup
- Track and disconnect signal handlers on cleanup
- Set _is_destroyed flag for thread-safe guards
- Log cleanup operations for debugging
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
import logging
from typing import Optional, Callable, Any, Dict, List, Tuple

# Import logging
try:
    from utils.logging_utils import get_logger
    logger = get_logger(__name__)
except ImportError:
    logger = logging.getLogger(__name__)


class PanelBase(Gtk.Box):
    """
    Base class for all MeshForge GTK4 panels.

    Provides standardized resource management including:
    - Timer tracking and automatic cleanup
    - Signal handler tracking and automatic disconnection
    - Thread-safe destruction flag
    - Automatic cleanup on widget unrealize

    Subclasses should:
    - Call super().__init__(main_window) first
    - Override _build_ui() to construct the UI
    - Use _schedule_timer() instead of GLib.timeout_add()
    - Use _connect_signal() instead of widget.connect()
    - Override cleanup() for additional resource cleanup (call super().cleanup())
    """

    def __init__(self, main_window, orientation=Gtk.Orientation.VERTICAL, spacing=0):
        """
        Initialize the panel base.

        Args:
            main_window: Reference to the main MeshForgeWindow
            orientation: Box orientation (default VERTICAL)
            spacing: Spacing between children (default 0)
        """
        super().__init__(orientation=orientation, spacing=spacing)

        self.main_window = main_window

        # Resource tracking
        self._pending_timers: List[int] = []
        self._signal_handlers: Dict[Gtk.Widget, List[int]] = {}
        self._is_destroyed: bool = False

        # Auto-cleanup on unrealize
        self.connect("unrealize", self._on_unrealize)

        # Standard margins (can be overridden by subclass)
        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

    def _on_unrealize(self, widget):
        """Called when widget is being destroyed - triggers cleanup."""
        if not self._is_destroyed:
            logger.debug(f"[{self.__class__.__name__}] unrealize triggered, running cleanup")
            self.cleanup()

    # -------------------------------------------------------------------------
    # Timer Management
    # -------------------------------------------------------------------------

    def _schedule_timer(self, delay_ms: int, callback: Callable, *args) -> int:
        """
        Schedule a timer with automatic tracking for cleanup.

        Use this instead of GLib.timeout_add() or GLib.timeout_add_seconds().

        Args:
            delay_ms: Delay in milliseconds
            callback: Function to call when timer fires
            *args: Arguments to pass to callback

        Returns:
            Timer ID (can be used with _cancel_timer())

        Example:
            # Instead of: GLib.timeout_add(1000, self._update)
            self._schedule_timer(1000, self._update)

            # With arguments:
            self._schedule_timer(500, self._update_label, "new text")
        """
        if self._is_destroyed:
            logger.debug(f"[{self.__class__.__name__}] Timer scheduled after destruction, ignoring")
            return 0

        if args:
            timer_id = GLib.timeout_add(delay_ms, callback, *args)
        else:
            timer_id = GLib.timeout_add(delay_ms, callback)

        self._pending_timers.append(timer_id)
        return timer_id

    def _schedule_timer_seconds(self, delay_seconds: int, callback: Callable, *args) -> int:
        """
        Schedule a timer in seconds with automatic tracking.

        Use this instead of GLib.timeout_add_seconds().

        Args:
            delay_seconds: Delay in seconds
            callback: Function to call when timer fires
            *args: Arguments to pass to callback

        Returns:
            Timer ID
        """
        if self._is_destroyed:
            logger.debug(f"[{self.__class__.__name__}] Timer scheduled after destruction, ignoring")
            return 0

        if args:
            timer_id = GLib.timeout_add_seconds(delay_seconds, callback, *args)
        else:
            timer_id = GLib.timeout_add_seconds(delay_seconds, callback)

        self._pending_timers.append(timer_id)
        return timer_id

    def _cancel_timer(self, timer_id: int) -> bool:
        """
        Cancel a specific timer.

        Args:
            timer_id: Timer ID returned from _schedule_timer()

        Returns:
            True if timer was found and cancelled
        """
        if timer_id in self._pending_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
            self._pending_timers.remove(timer_id)
            return True
        return False

    def _cancel_all_timers(self):
        """Cancel all pending timers."""
        cancelled = 0
        for timer_id in self._pending_timers:
            try:
                GLib.source_remove(timer_id)
                cancelled += 1
            except Exception:
                pass
        self._pending_timers.clear()

        if cancelled > 0:
            logger.debug(f"[{self.__class__.__name__}] Cancelled {cancelled} timers")

    # -------------------------------------------------------------------------
    # Signal Handler Management
    # -------------------------------------------------------------------------

    def _connect_signal(self, widget: Gtk.Widget, signal_name: str,
                        callback: Callable, *args) -> int:
        """
        Connect a signal with automatic tracking for cleanup.

        Use this instead of widget.connect().

        Args:
            widget: GTK widget to connect signal on
            signal_name: Name of the signal (e.g., "clicked", "changed")
            callback: Handler function
            *args: Additional arguments for the callback

        Returns:
            Signal handler ID

        Example:
            # Instead of: button.connect("clicked", self._on_click)
            self._connect_signal(button, "clicked", self._on_click)
        """
        if args:
            handler_id = widget.connect(signal_name, callback, *args)
        else:
            handler_id = widget.connect(signal_name, callback)

        # Track by widget
        if widget not in self._signal_handlers:
            self._signal_handlers[widget] = []
        self._signal_handlers[widget].append(handler_id)

        return handler_id

    def _disconnect_signal(self, widget: Gtk.Widget, handler_id: int) -> bool:
        """
        Disconnect a specific signal handler.

        Args:
            widget: Widget the signal is connected to
            handler_id: Handler ID returned from _connect_signal()

        Returns:
            True if handler was found and disconnected
        """
        if widget in self._signal_handlers and handler_id in self._signal_handlers[widget]:
            try:
                widget.disconnect(handler_id)
            except Exception:
                pass
            self._signal_handlers[widget].remove(handler_id)
            return True
        return False

    def _disconnect_all_signals(self):
        """Disconnect all tracked signal handlers."""
        disconnected = 0
        for widget, handler_ids in self._signal_handlers.items():
            for handler_id in handler_ids:
                try:
                    widget.disconnect(handler_id)
                    disconnected += 1
                except Exception:
                    pass
        self._signal_handlers.clear()

        if disconnected > 0:
            logger.debug(f"[{self.__class__.__name__}] Disconnected {disconnected} signal handlers")

    # -------------------------------------------------------------------------
    # Thread-Safe UI Updates
    # -------------------------------------------------------------------------

    def _idle_add(self, callback: Callable, *args) -> int:
        """
        Schedule a callback to run in the main GTK thread (thread-safe).

        Use this from background threads to update the UI.
        Automatically checks _is_destroyed to prevent updates after destruction.

        Args:
            callback: Function to call in main thread
            *args: Arguments to pass to callback

        Returns:
            Source ID (or 0 if panel is destroyed)

        Example:
            def background_work():
                result = slow_operation()
                self._idle_add(self._update_ui, result)

            threading.Thread(target=background_work, daemon=True).start()
        """
        if self._is_destroyed:
            return 0

        def safe_callback(*cb_args):
            if not self._is_destroyed:
                return callback(*cb_args)
            return False

        if args:
            return GLib.idle_add(safe_callback, *args)
        return GLib.idle_add(safe_callback)

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    def cleanup(self):
        """
        Clean up all resources.

        Called automatically on unrealize, or manually by MeshForgeWindow on close.
        Subclasses should override this and call super().cleanup() at the end.

        Example:
            def cleanup(self):
                # Your cleanup code here
                if self.my_connection:
                    self.my_connection.close()

                # Always call parent cleanup last
                super().cleanup()
        """
        if self._is_destroyed:
            logger.debug(f"[{self.__class__.__name__}] cleanup() called but already destroyed")
            return

        logger.debug(f"[{self.__class__.__name__}] Running cleanup...")
        self._is_destroyed = True

        # Cancel all timers
        self._cancel_all_timers()

        # Disconnect all signals
        self._disconnect_all_signals()

        logger.debug(f"[{self.__class__.__name__}] Cleanup complete")

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def set_status_message(self, message: str):
        """
        Set the status message in the main window.

        Safely handles case where main_window might not have status bar yet.

        Args:
            message: Status message to display
        """
        if self.main_window and hasattr(self.main_window, 'set_status_message'):
            self.main_window.set_status_message(message)

    @property
    def is_destroyed(self) -> bool:
        """Check if the panel has been destroyed."""
        return self._is_destroyed


# -----------------------------------------------------------------------------
# Mixin for existing panels that can't change base class yet
# -----------------------------------------------------------------------------

class PanelResourceMixin:
    """
    Mixin providing resource management for existing panels.

    Use this when you can't change the base class but want resource tracking.
    Call _init_resource_tracking() in __init__ and cleanup_resources() in cleanup().

    Example:
        class ExistingPanel(Gtk.Box, PanelResourceMixin):
            def __init__(self, main_window):
                Gtk.Box.__init__(self)
                self._init_resource_tracking()
                # ... rest of init

            def cleanup(self):
                self.cleanup_resources()
    """

    def _init_resource_tracking(self):
        """Initialize resource tracking. Call this in __init__."""
        self._pending_timers: List[int] = []
        self._signal_handlers: Dict[Gtk.Widget, List[int]] = {}
        self._is_destroyed: bool = False

    def _schedule_timer(self, delay_ms: int, callback: Callable, *args) -> int:
        """Schedule a timer with automatic tracking."""
        if getattr(self, '_is_destroyed', False):
            return 0

        if args:
            timer_id = GLib.timeout_add(delay_ms, callback, *args)
        else:
            timer_id = GLib.timeout_add(delay_ms, callback)

        if not hasattr(self, '_pending_timers'):
            self._pending_timers = []
        self._pending_timers.append(timer_id)
        return timer_id

    def _schedule_timer_seconds(self, delay_seconds: int, callback: Callable, *args) -> int:
        """Schedule a timer in seconds with automatic tracking."""
        if getattr(self, '_is_destroyed', False):
            return 0

        if args:
            timer_id = GLib.timeout_add_seconds(delay_seconds, callback, *args)
        else:
            timer_id = GLib.timeout_add_seconds(delay_seconds, callback)

        if not hasattr(self, '_pending_timers'):
            self._pending_timers = []
        self._pending_timers.append(timer_id)
        return timer_id

    def _cancel_all_timers(self):
        """Cancel all pending timers."""
        if not hasattr(self, '_pending_timers'):
            return

        for timer_id in self._pending_timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._pending_timers.clear()

    def _connect_signal(self, widget: Gtk.Widget, signal_name: str,
                        callback: Callable, *args) -> int:
        """Connect a signal with automatic tracking."""
        if args:
            handler_id = widget.connect(signal_name, callback, *args)
        else:
            handler_id = widget.connect(signal_name, callback)

        if not hasattr(self, '_signal_handlers'):
            self._signal_handlers = {}

        if widget not in self._signal_handlers:
            self._signal_handlers[widget] = []
        self._signal_handlers[widget].append(handler_id)
        return handler_id

    def _disconnect_all_signals(self):
        """Disconnect all tracked signal handlers."""
        if not hasattr(self, '_signal_handlers'):
            return

        for widget, handler_ids in self._signal_handlers.items():
            for handler_id in handler_ids:
                try:
                    widget.disconnect(handler_id)
                except Exception:
                    pass
        self._signal_handlers.clear()

    def cleanup_resources(self):
        """Clean up all tracked resources. Call this in cleanup()."""
        self._is_destroyed = True
        self._cancel_all_timers()
        self._disconnect_all_signals()
