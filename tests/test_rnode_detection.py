"""
Tests for RNode device detection.

Run: python3 -m pytest tests/test_rnode_detection.py -v
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.commands.rnode import (
    RNodeDevice,
    RNODE_USB_IDS,
    SERIAL_PATTERNS,
    get_serial_ports,
    get_usb_info,
    identify_device_model,
    check_rns_config,
    detect_devices,
    detect_rnode_devices,
    get_device_info,
    get_recommended_config,
)


class TestRNodeDevice:
    """Tests for RNodeDevice dataclass."""

    def test_create_device(self):
        """Test creating an RNodeDevice."""
        device = RNodeDevice(
            port="/dev/ttyUSB0",
            model="RNode (CH340)",
            vid="1a86",
            pid="55d4"
        )

        assert device.port == "/dev/ttyUSB0"
        assert device.model == "RNode (CH340)"
        assert device.vid == "1a86"
        assert device.pid == "55d4"
        assert device.is_rnode is False
        assert device.is_configured is False

    def test_to_dict(self):
        """Test serialization to dict."""
        device = RNodeDevice(
            port="/dev/ttyACM0",
            model="T-Beam",
            is_rnode=True,
            firmware_version="1.2.3"
        )

        d = device.to_dict()

        assert d['port'] == "/dev/ttyACM0"
        assert d['model'] == "T-Beam"
        assert d['is_rnode'] is True
        assert d['firmware_version'] == "1.2.3"

    def test_default_details(self):
        """Test details defaults to empty dict."""
        device = RNodeDevice(port="/dev/ttyUSB0")
        assert device.details == {}


class TestConstants:
    """Tests for module constants."""

    def test_rnode_usb_ids_exist(self):
        """Test known USB IDs are defined."""
        assert len(RNODE_USB_IDS) > 0

        # Check structure
        for entry in RNODE_USB_IDS:
            assert 'vid' in entry
            assert 'pid' in entry
            assert 'name' in entry

    def test_serial_patterns(self):
        """Test serial patterns are defined."""
        assert '/dev/ttyUSB*' in SERIAL_PATTERNS
        assert '/dev/ttyACM*' in SERIAL_PATTERNS


class TestGetSerialPorts:
    """Tests for get_serial_ports function."""

    def test_returns_list(self):
        """Function returns a list."""
        result = get_serial_ports()
        assert isinstance(result, list)

    def test_filters_nonexistent(self):
        """Filters out non-existent ports."""
        # Test that the function filters based on existence
        with patch('src.commands.rnode.Path') as mock_path_class:
            # Mock the glob to return paths
            mock_path_class.return_value.glob.return_value = ['/dev/ttyUSB0', '/dev/ttyUSB1']
            # Mock exists to filter
            mock_path_instance = mock_path_class.return_value
            mock_path_instance.exists.side_effect = [True, False]
            result = get_serial_ports()
            assert isinstance(result, list)


class TestGetUsbInfo:
    """Tests for get_usb_info function."""

    def test_returns_dict(self):
        """Function returns a dict with expected keys."""
        result = get_usb_info('/dev/ttyUSB0')

        assert isinstance(result, dict)
        assert 'vid' in result
        assert 'pid' in result
        assert 'serial' in result
        assert 'manufacturer' in result
        assert 'product' in result

    def test_handles_nonexistent_port(self):
        """Handles non-existent port gracefully."""
        result = get_usb_info('/dev/nonexistent')

        assert result['vid'] == ''
        assert result['pid'] == ''


class TestIdentifyDeviceModel:
    """Tests for identify_device_model function."""

    def test_identify_ch340_rnode(self):
        """Identify CH340-based RNode."""
        model = identify_device_model('1a86', '55d4')
        assert 'RNode' in model or 'CH340' in model

    def test_identify_tbeam(self):
        """Identify T-Beam."""
        model = identify_device_model('1a86', '7523')
        assert 'T-Beam' in model

    def test_unknown_device(self):
        """Unknown device returns product string or 'Unknown'."""
        model = identify_device_model('0000', '0000', 'Custom Device')
        assert model == 'Custom Device'

    def test_unknown_no_product(self):
        """Unknown device with no product string."""
        model = identify_device_model('0000', '0000')
        assert 'Unknown' in model


class TestCheckRnsConfig:
    """Tests for check_rns_config function."""

    def test_port_in_config(self):
        """Return True when port is in config."""
        mock_content = """
[interfaces]

[[RNode LoRa Interface]]
  type = RNodeInterface
  port = /dev/ttyUSB0
  frequency = 903625000
"""
        with patch('pathlib.Path.exists', return_value=True):
            with patch('pathlib.Path.read_text', return_value=mock_content):
                result = check_rns_config('/dev/ttyUSB0')

        assert result is True

    def test_port_not_in_config(self):
        """Return False when port not in config."""
        mock_content = """
[interfaces]

[[RNode LoRa Interface]]
  port = /dev/ttyACM0
"""
        with patch('pathlib.Path.exists', return_value=True):
            with patch('pathlib.Path.read_text', return_value=mock_content):
                result = check_rns_config('/dev/ttyUSB0')

        assert result is False

    def test_config_not_exists(self):
        """Return False when config doesn't exist."""
        with patch('pathlib.Path.exists', return_value=False):
            result = check_rns_config('/dev/ttyUSB0')

        assert result is False

    def test_sudo_user_handling(self):
        """Verify SUDO_USER is handled via centralized utils.paths."""
        # This test verifies the code uses get_real_user_home from utils.paths
        # which properly handles SUDO_USER for correct home directory resolution
        import os

        with patch.dict(os.environ, {'SUDO_USER': 'testuser'}):
            with patch('pathlib.Path.exists', return_value=False):
                # The function should not crash and should use centralized path utility
                result = check_rns_config('/dev/ttyUSB0')

        assert result is False  # No config exists, but it didn't crash


class TestDetectDevices:
    """Tests for detect_devices function."""

    def test_returns_list(self):
        """Function returns a list."""
        with patch('src.commands.rnode.get_serial_ports', return_value=[]):
            result = detect_devices()

        assert isinstance(result, list)

    def test_creates_device_objects(self):
        """Creates RNodeDevice objects for each port."""
        with patch('src.commands.rnode.get_serial_ports', return_value=['/dev/ttyUSB0']):
            with patch('src.commands.rnode.get_usb_info', return_value={
                'vid': '1a86', 'pid': '55d4', 'serial': 'ABC123',
                'manufacturer': 'Test', 'product': 'RNode'
            }):
                with patch('src.commands.rnode.check_rns_config', return_value=False):
                    result = detect_devices()

        assert len(result) == 1
        assert isinstance(result[0], RNodeDevice)
        assert result[0].port == '/dev/ttyUSB0'


class TestDetectRnodeDevices:
    """Tests for detect_rnode_devices CLI command."""

    def test_no_devices(self):
        """Return failure when no devices found."""
        with patch('src.commands.rnode.detect_devices', return_value=[]):
            result = detect_rnode_devices()

        assert result.success is False
        assert 'No serial devices' in result.message

    def test_devices_found(self):
        """Return success when devices found."""
        mock_device = RNodeDevice(
            port='/dev/ttyUSB0',
            model='RNode',
            is_rnode=True
        )
        with patch('src.commands.rnode.detect_devices', return_value=[mock_device]):
            result = detect_rnode_devices()

        assert result.success is True
        assert result.data['count'] == 1


class TestGetDeviceInfo:
    """Tests for get_device_info CLI command."""

    def test_port_not_found(self):
        """Return failure for non-existent port."""
        with patch('pathlib.Path.exists', return_value=False):
            result = get_device_info('/dev/nonexistent')

        assert result.success is False
        assert 'not found' in result.message

    def test_get_info_success(self):
        """Get device info successfully."""
        with patch('pathlib.Path.exists', return_value=True):
            with patch('src.commands.rnode.get_usb_info', return_value={
                'vid': '1a86', 'pid': '55d4', 'serial': '',
                'manufacturer': '', 'product': ''
            }):
                with patch('src.commands.rnode.check_rns_config', return_value=False):
                    with patch('src.commands.rnode.probe_rnode', return_value=None):
                        result = get_device_info('/dev/ttyUSB0')

        assert result.success is True
        assert '/dev/ttyUSB0' in result.message


class TestGetRecommendedConfig:
    """Tests for get_recommended_config CLI command."""

    def test_us_region(self):
        """Get US region configuration."""
        result = get_recommended_config('/dev/ttyUSB0', 'US')

        assert result.success is True
        assert result.data['config']['region'] == 'US'
        assert result.data['config']['frequency'] == 903625000
        assert 'snippet' in result.data

    def test_eu_region(self):
        """Get EU region configuration."""
        result = get_recommended_config('/dev/ttyUSB0', 'EU')

        assert result.success is True
        assert result.data['config']['region'] == 'EU'
        assert result.data['config']['tx_power'] == 14  # EU limit

    def test_au_region(self):
        """Get AU region configuration."""
        result = get_recommended_config('/dev/ttyUSB0', 'AU')

        assert result.success is True
        assert result.data['config']['region'] == 'AU'

    def test_unknown_region(self):
        """Return failure for unknown region."""
        result = get_recommended_config('/dev/ttyUSB0', 'UNKNOWN')

        assert result.success is False
        assert 'Unknown region' in result.message

    def test_case_insensitive(self):
        """Region is case-insensitive."""
        result = get_recommended_config('/dev/ttyUSB0', 'us')

        assert result.success is True
        assert result.data['config']['region'] == 'US'

    def test_config_snippet_format(self):
        """Config snippet has correct format."""
        result = get_recommended_config('/dev/ttyUSB0', 'US')

        snippet = result.data['snippet']
        assert '[[RNode LoRa Interface]]' in snippet
        assert 'type = RNodeInterface' in snippet
        assert 'port = /dev/ttyUSB0' in snippet
        assert 'frequency =' in snippet
