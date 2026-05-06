"""
MeshCore Connection Manager — companion to ``meshtastic_connection.py``.

MeshCore has no daemon (unlike Meshtastic's meshtasticd). The radio firmware
speaks the meshcore_py wire protocol directly over serial / TCP / BLE. That
means the *first* process to open the device wins exclusive ownership; any
second consumer that races a probe against the live link gets EBUSY or
silently corrupts the running session.

This module mirrors the Meshtastic pattern:

* ``MESHCORE_CONNECTION_LOCK`` — module-level threading.Lock. Any code that
  opens the radio device (raw serial, BLE, or via ``MeshCore.create_*``)
  must hold this lock for the duration of the open/close cycle.
* ``MeshCoreConnectionManager`` — singleton. The long-running owner (the
  gateway bridge handler) registers its live ``MeshCore`` instance + the
  asyncio loop that owns it via ``register_persistent()``. Other consumers
  share the link via ``get_meshcore()`` / ``run_in_radio_loop()`` instead
  of opening a second one.
* ``MeshCoreConnection`` — short-lived sync context manager for raw probes
  (``validate_meshcore_device``) and one-shot CLI/TUI helpers. Acquires the
  lock, refuses to proceed if the persistent owner is active, releases on
  exit.

Why a lock when meshcore_py serializes its own command queue?  meshcore_py
serialises commands inside one MeshCore instance, but it does NOT prevent
two MeshCore instances (or one MeshCore + one raw pyserial probe) from
fighting for the same /dev/ttyACMx slot. The lock guards the *open*
boundary, not the wire protocol.

Lint: see MF014 in ``scripts/lint.py``. Regression guard:
``tests/test_regression_guards.py::TestMeshCoreConnectionContract``.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import Any, Dict, Optional

from utils.boundary_timing import timed_boundary
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# meshcore_py is optional — gateway code already gates on _HAS_MESHCORE.
_meshcore_mod, _HAS_MESHCORE = safe_import('meshcore')


class ConnectionMode(Enum):
    """Transport for the MeshCore companion radio."""
    SERIAL = "serial"
    TCP = "tcp"
    BLE = "ble"
    SIMULATION = "simulation"


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Global lock — held during device open/close. Short-lived consumers acquire
# this around raw serial probes; the gateway handler acquires it across the
# bring-up window before promoting itself to persistent owner.
MESHCORE_CONNECTION_LOCK = threading.Lock()

# Cooldown between successive opens of the same device. The Meshtastic side
# uses 1.0s; meshcore_py's serial backend reopens faster so 0.5s is enough.
CONNECTION_COOLDOWN = 0.5

_last_global_close_time: float = 0.0


class ConnectionError(Exception):
    """Raised when MeshCore connect / probe fails."""


class ConnectionBusy(Exception):
    """Raised when the persistent owner holds the radio and a short-lived
    consumer asked for exclusive access."""


# ---------------------------------------------------------------------------
# Device discovery + raw probe (moved here from meshcore_handler so all code
# that touches the OS device lives in one place — see MF014 allowlist).
# ---------------------------------------------------------------------------


def detect_meshcore_devices() -> list[str]:
    """Enumerate candidate MeshCore companion-radio device paths.

    Persistent ``/dev/ttyMeshCore`` (from ``scripts/99-meshcore.rules``) is
    listed first. Then raw ``ttyUSB*`` / ``ttyACM*`` are added, skipping the
    one that the symlink already points at.
    """
    devices: list[str] = []
    if os.path.exists('/dev/ttyMeshCore'):
        devices.append('/dev/ttyMeshCore')
    for pattern in ('/dev/ttyUSB*', '/dev/ttyACM*'):
        for dev in sorted(glob.glob(pattern)):
            if '/dev/ttyMeshCore' in devices:
                try:
                    if os.path.realpath(dev) == os.path.realpath('/dev/ttyMeshCore'):
                        continue
                except OSError:
                    pass
            devices.append(dev)
    return devices


def validate_meshcore_device(
    device_path: str,
    baud_rate: int = 115200,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Pre-flight: open ``device_path`` raw and check whether it responds.

    Acquires ``MESHCORE_CONNECTION_LOCK`` for the duration of the probe so
    we don't fight the gateway handler (or any future consumer) for the
    serial slot. If the lock cannot be acquired within 2s, the probe is
    skipped — the device is assumed busy in the persistent owner.

    Returns a dict with keys ``exists``, ``readable``, ``responds``,
    ``error``.
    """
    result: Dict[str, Any] = {
        'exists': False,
        'readable': False,
        'responds': False,
        'error': None,
    }

    if not os.path.exists(device_path):
        result['error'] = f"Device not found: {device_path}"
        return result
    result['exists'] = True

    try:
        import serial  # type: ignore[import-not-found]
    except ImportError:
        result['error'] = "pyserial not installed (pip install pyserial)"
        return result

    # If the persistent owner already holds the radio, don't probe — we'd
    # either block them or get EBUSY. Treat as "exists, not probed".
    mgr = get_connection_manager()
    if mgr.has_persistent():
        result['readable'] = True  # by inference: persistent owner has it open
        result['responds'] = True
        result['error'] = (
            f"persistent owner '{mgr.get_persistent_owner()}' active — "
            "skipping raw probe"
        )
        return result

    if not MESHCORE_CONNECTION_LOCK.acquire(timeout=2.0):
        result['error'] = "MESHCORE_CONNECTION_LOCK busy (another consumer probing)"
        return result

    try:
        wait_for_cooldown()
        with timed_boundary("meshcore.probe_serial", target=device_path,
                            threshold_s=3.0):
            with serial.Serial(device_path, baud_rate, timeout=timeout) as ser:
                result['readable'] = True
                ser.reset_input_buffer()
                ser.write(b'\n')
                response = ser.read(64)
                if response:
                    result['responds'] = True
    except serial.SerialException as e:  # type: ignore[attr-defined]
        result['error'] = f"Serial error: {e}"
    except PermissionError:
        result['error'] = (
            f"Permission denied: {device_path} — add user to 'dialout' group"
        )
    except OSError as e:
        result['error'] = f"OS error: {e}"
    finally:
        _mark_close()
        MESHCORE_CONNECTION_LOCK.release()

    return result


def wait_for_cooldown() -> None:
    """Sleep out the inter-open cooldown window if one is active."""
    elapsed = time.time() - _last_global_close_time
    if elapsed < CONNECTION_COOLDOWN:
        time.sleep(CONNECTION_COOLDOWN - elapsed)


def _mark_close() -> None:
    global _last_global_close_time
    _last_global_close_time = time.time()


# ---------------------------------------------------------------------------
# MeshCoreConnectionManager — singleton
# ---------------------------------------------------------------------------


class MeshCoreConnectionManager:
    """Tracks the live MeshCore instance + the asyncio loop that owns it.

    The bridge handler is the canonical persistent owner. When it brings up
    the radio it calls :py:meth:`register_persistent`; on shutdown it calls
    :py:meth:`unregister_persistent`. Everything else queries
    :py:meth:`get_meshcore` and runs coroutines through
    :py:meth:`run_in_radio_loop`.
    """

    def __init__(self) -> None:
        self._state_lock = threading.Lock()
        self._meshcore: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._owner: Optional[str] = None
        self._mode: Optional[ConnectionMode] = None
        self._device: Optional[str] = None

    # -- persistent owner registration --------------------------------------

    def register_persistent(
        self,
        meshcore: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        owner: str = "gateway-bridge",
        mode: ConnectionMode = ConnectionMode.SERIAL,
        device: Optional[str] = None,
    ) -> None:
        """Mark a live MeshCore instance as the shared persistent link.

        Caller must already hold ``MESHCORE_CONNECTION_LOCK`` and have a
        running ``MeshCore`` instance bound to ``loop``. This method only
        records the references for sharing.
        """
        with self._state_lock:
            if self._meshcore is not None:
                logger.warning(
                    "register_persistent: already held by '%s' — replacing "
                    "with '%s'", self._owner, owner
                )
            self._meshcore = meshcore
            self._loop = loop
            self._owner = owner
            self._mode = mode
            self._device = device
            logger.info(
                "MeshCore persistent link registered: owner=%s mode=%s device=%s",
                owner, mode.value, device
            )

    def unregister_persistent(self) -> None:
        """Clear the persistent registration. Safe to call when nothing is
        registered. Does NOT close the MeshCore instance — the owner is
        responsible for ``await meshcore.disconnect()``."""
        with self._state_lock:
            if self._meshcore is None:
                return
            logger.info(
                "MeshCore persistent link unregistered (was owner=%s)", self._owner
            )
            self._meshcore = None
            self._loop = None
            self._owner = None
            self._mode = None
            self._device = None
            _mark_close()

    # -- shared access ------------------------------------------------------

    def has_persistent(self) -> bool:
        with self._state_lock:
            return self._meshcore is not None

    def get_meshcore(self) -> Any:
        """Return the live MeshCore instance, or None if no owner is registered."""
        with self._state_lock:
            return self._meshcore

    def get_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        with self._state_lock:
            return self._loop

    def get_persistent_owner(self) -> Optional[str]:
        with self._state_lock:
            return self._owner

    def get_mode(self) -> Optional[ConnectionMode]:
        with self._state_lock:
            return self._mode

    def get_device(self) -> Optional[str]:
        with self._state_lock:
            return self._device

    def status(self) -> Dict[str, Any]:
        """Snapshot of connection state for diagnostics / status panels."""
        with self._state_lock:
            return {
                'connected': self._meshcore is not None,
                'owner': self._owner,
                'mode': self._mode.value if self._mode else None,
                'device': self._device,
                'lock_held': MESHCORE_CONNECTION_LOCK.locked(),
            }

    def run_in_radio_loop(
        self,
        coro: Any,
        *,
        timeout: float = 10.0,
    ) -> Any:
        """Schedule ``coro`` on the persistent owner's loop and block for
        the result.

        Raises :py:class:`ConnectionError` if no persistent owner is
        registered or if the owner's loop is not running. Use this from
        sync code (TUI, CLI) to talk to the radio without opening a second
        connection.
        """
        loop = self.get_loop()
        if loop is None or not loop.is_running():
            raise ConnectionError(
                "no persistent MeshCore owner — start gateway bridge or "
                "use MeshCoreConnection for one-shot ops"
            )
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)


# Singleton plumbing -- lazy so test code can swap it out via reset().
_manager_lock = threading.Lock()
_connection_manager: Optional[MeshCoreConnectionManager] = None


def get_connection_manager() -> MeshCoreConnectionManager:
    """Return the process-wide :py:class:`MeshCoreConnectionManager`."""
    global _connection_manager
    with _manager_lock:
        if _connection_manager is None:
            _connection_manager = MeshCoreConnectionManager()
        return _connection_manager


def reset_connection_manager() -> None:
    """Drop the singleton (for tests). Does NOT close anything."""
    global _connection_manager
    with _manager_lock:
        _connection_manager = None


# ---------------------------------------------------------------------------
# Short-lived sync consumer
# ---------------------------------------------------------------------------


class MeshCoreConnection:
    """Sync context manager for one-shot ops that need exclusive port access.

    Usage::

        with MeshCoreConnection() as conn:
            if conn is None:
                # busy — persistent owner active, or lock held elsewhere
                return
            # ... talk to the radio for a few seconds, then release

    The context value is ``self`` on success or ``None`` on busy / not
    available. ``self`` exposes :py:meth:`device` and :py:meth:`probe`.
    """

    def __init__(
        self,
        device_path: Optional[str] = None,
        *,
        baud_rate: int = 115200,
        lock_timeout: float = 5.0,
        respect_persistent: bool = True,
    ) -> None:
        self._device_path = device_path
        self._baud_rate = baud_rate
        self._lock_timeout = lock_timeout
        self._respect_persistent = respect_persistent
        self._lock_held = False

    def device(self) -> Optional[str]:
        return self._device_path

    def probe(self, timeout: float = 3.0) -> Dict[str, Any]:
        """Run the standard pre-flight on the bound device. Caller must be
        inside the ``with`` block (lock already held)."""
        if not self._lock_held:
            raise RuntimeError("MeshCoreConnection.probe outside `with`")
        if not self._device_path:
            return {'exists': False, 'error': 'no device path bound'}
        # Re-enter validate_meshcore_device — it sees the lock is held by us
        # and skips the inner acquire by quickly succeeding the timeout? No —
        # the lock is non-reentrant. Inline the probe instead.
        return _probe_device_unlocked(
            self._device_path, self._baud_rate, timeout=timeout,
        )

    def __enter__(self) -> Optional['MeshCoreConnection']:
        mgr = get_connection_manager()
        if self._respect_persistent and mgr.has_persistent():
            logger.debug(
                "MeshCoreConnection: persistent owner '%s' active — refusing",
                mgr.get_persistent_owner(),
            )
            return None
        if not MESHCORE_CONNECTION_LOCK.acquire(timeout=self._lock_timeout):
            logger.debug("MeshCoreConnection: lock acquire timed out")
            return None
        self._lock_held = True
        wait_for_cooldown()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._lock_held:
            _mark_close()
            MESHCORE_CONNECTION_LOCK.release()
            self._lock_held = False


def _probe_device_unlocked(
    device_path: str,
    baud_rate: int,
    *,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """Internal: probe assuming the caller already holds the lock."""
    result: Dict[str, Any] = {
        'exists': False,
        'readable': False,
        'responds': False,
        'error': None,
    }
    if not os.path.exists(device_path):
        result['error'] = f"Device not found: {device_path}"
        return result
    result['exists'] = True
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError:
        result['error'] = "pyserial not installed"
        return result
    try:
        with timed_boundary("meshcore.probe_serial", target=device_path,
                            threshold_s=3.0):
            with serial.Serial(device_path, baud_rate, timeout=timeout) as ser:
                result['readable'] = True
                ser.reset_input_buffer()
                ser.write(b'\n')
                if ser.read(64):
                    result['responds'] = True
    except serial.SerialException as e:  # type: ignore[attr-defined]
        result['error'] = f"Serial error: {e}"
    except PermissionError:
        result['error'] = f"Permission denied: {device_path}"
    except OSError as e:
        result['error'] = f"OS error: {e}"
    return result


# ---------------------------------------------------------------------------
# High-level connect helper used by the gateway handler
# ---------------------------------------------------------------------------


@contextmanager
def acquire_for_connect(
    *,
    owner: str,
    lock_timeout: float = 30.0,
):
    """Acquire ``MESHCORE_CONNECTION_LOCK`` across a connect bring-up.

    The gateway handler wraps its ``MeshCore.create_serial / create_tcp``
    calls in this context manager. On success the caller registers the
    resulting MeshCore + loop via ``register_persistent`` BEFORE the
    context exits — that way the lock is released only after the
    persistent owner is observable to other consumers.

    Yields True if the lock was acquired, False otherwise. The caller is
    responsible for honoring the False case (typically by logging and
    bailing out of the connect).
    """
    if get_connection_manager().has_persistent():
        logger.error(
            "acquire_for_connect: persistent owner '%s' already active — "
            "refusing second connect from '%s'",
            get_connection_manager().get_persistent_owner(), owner,
        )
        yield False
        return
    if not MESHCORE_CONNECTION_LOCK.acquire(timeout=lock_timeout):
        logger.error(
            "acquire_for_connect: lock timed out after %.1fs (owner=%s)",
            lock_timeout, owner,
        )
        yield False
        return
    try:
        wait_for_cooldown()
        yield True
    finally:
        # Always release. The handler's register_persistent() has already
        # recorded the live instance, so subsequent consumers will see the
        # link as held without needing the lock.
        _mark_close()
        try:
            MESHCORE_CONNECTION_LOCK.release()
        except RuntimeError:
            pass
