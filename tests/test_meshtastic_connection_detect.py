"""Tests for utils.meshtastic_connection._detect_usb_device — focused on the
MeshCore-device exclusion that prevents AUTO-detect from grabbing the RAK.

Background: on a MeshCore-primary host with no Meshtastic radio, the
udev rule `scripts/99-meshcore.rules` creates `/dev/ttyMeshCore` as a
symlink to whichever ttyACM* / ttyUSB* the RAK enumerated as. Without
the exclusion below, MeshtasticConnectionManager._detect_usb_device
returns that same device every reconnect cycle, races the MeshCore
handler for the port, and floods the journal with EBUSY.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from utils.meshtastic_connection import (
    ConnectionMode,
    MeshtasticConnectionManager,
    reset_connection_manager,
)


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_connection_manager()
    yield
    reset_connection_manager()


def _make_manager() -> MeshtasticConnectionManager:
    return MeshtasticConnectionManager(mode=ConnectionMode.AUTO)


class TestDetectUsbDeviceExcludesMeshCore:
    """Verifies the AUTO-detect skips devices owned by MeshCore."""

    def test_skips_meshcore_symlink_target(self):
        """When /dev/ttyMeshCore exists and points at /dev/ttyACM0, the
        Meshtastic auto-detect must skip ttyACM0 and not return any
        device (the only candidate is excluded)."""
        mgr = _make_manager()

        def fake_glob(pattern):
            return {
                '/dev/ttyUSB*': [],
                '/dev/ttyACM*': ['/dev/ttyACM0'],
            }.get(pattern, [])

        def fake_exists(path):
            return path == '/dev/ttyMeshCore'

        def fake_realpath(path):
            return {
                '/dev/ttyMeshCore': '/dev/ttyACM0',
                '/dev/ttyACM0': '/dev/ttyACM0',
            }.get(path, path)

        with patch('glob.glob', side_effect=fake_glob), \
             patch('os.path.exists', side_effect=fake_exists), \
             patch('os.path.realpath', side_effect=fake_realpath):
            assert mgr._detect_usb_device() is None

    def test_returns_other_device_when_meshcore_excludes_acm0(self):
        """If both /dev/ttyACM0 (MeshCore) and /dev/ttyACM1 (Meshtastic)
        are present, the auto-detect must return ttyACM1."""
        mgr = _make_manager()

        def fake_glob(pattern):
            return {
                '/dev/ttyUSB*': [],
                '/dev/ttyACM*': ['/dev/ttyACM0', '/dev/ttyACM1'],
            }.get(pattern, [])

        def fake_realpath(path):
            return {
                '/dev/ttyMeshCore': '/dev/ttyACM0',
                '/dev/ttyACM0': '/dev/ttyACM0',
                '/dev/ttyACM1': '/dev/ttyACM1',
            }.get(path, path)

        with patch('glob.glob', side_effect=fake_glob), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.realpath', side_effect=fake_realpath):
            assert mgr._detect_usb_device() == '/dev/ttyACM1'

    def test_returns_first_when_no_meshcore_symlink(self):
        """Without /dev/ttyMeshCore (MeshCore not installed), the auto-detect
        falls back to the original behavior — return the first ACM device."""
        mgr = _make_manager()

        def fake_glob(pattern):
            return {
                '/dev/ttyUSB*': [],
                '/dev/ttyACM*': ['/dev/ttyACM0', '/dev/ttyACM1'],
            }.get(pattern, [])

        with patch('glob.glob', side_effect=fake_glob), \
             patch('os.path.exists', return_value=False):
            assert mgr._detect_usb_device() == '/dev/ttyACM0'

    def test_handles_realpath_oserror_gracefully(self):
        """OSError from a stale symlink mustn't crash the detect path."""
        mgr = _make_manager()

        def fake_glob(pattern):
            return {
                '/dev/ttyUSB*': ['/dev/ttyUSB0'],
                '/dev/ttyACM*': [],
            }.get(pattern, [])

        def fake_realpath(path):
            raise OSError("simulated stale symlink")

        with patch('glob.glob', side_effect=fake_glob), \
             patch('os.path.exists', return_value=True), \
             patch('os.path.realpath', side_effect=fake_realpath):
            # Should not raise; falls back to returning the device
            assert mgr._detect_usb_device() == '/dev/ttyUSB0'

    def test_no_devices_returns_none(self):
        """Sanity: no candidates → None."""
        mgr = _make_manager()
        with patch('glob.glob', return_value=[]), \
             patch('os.path.exists', return_value=False):
            assert mgr._detect_usb_device() is None
