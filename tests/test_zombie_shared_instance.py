"""
Tests for zombie shared instance detection in _port_detection.py.

When rnsd is running but the shared instance socket/port is not available,
the diagnostic should identify this as a config issue rather than generic
"not available".

Run: python3 -m pytest tests/test_zombie_shared_instance.py -v
"""

from unittest.mock import patch

from src.utils._port_detection import get_rns_shared_instance_info


class TestZombieSharedInstance:
    """Tests for zombie shared instance detection."""

    @patch('src.utils._port_detection.check_process_running',
           return_value=True)
    @patch('src.utils._port_detection.check_udp_port', return_value=False)
    @patch('src.utils._port_detection.check_port', return_value=False)
    @patch('src.utils._port_detection._check_proc_net_unix',
           return_value=False)
    def test_rnsd_running_no_socket_returns_diagnostic(
            self, mock_unix, mock_tcp, mock_udp, mock_proc):
        """When rnsd runs but no shared instance found, include diagnostic."""
        result = get_rns_shared_instance_info()

        assert result['available'] is False
        assert result['method'] == 'none'
        assert 'diagnostic' in result
        assert 'shared_instance_type' in result['diagnostic']

    @patch('src.utils._port_detection.check_process_running',
           return_value=False)
    @patch('src.utils._port_detection.check_udp_port', return_value=False)
    @patch('src.utils._port_detection.check_port', return_value=False)
    @patch('src.utils._port_detection._check_proc_net_unix',
           return_value=False)
    def test_rnsd_not_running_no_diagnostic(
            self, mock_unix, mock_tcp, mock_udp, mock_proc):
        """When rnsd is not running, no zombie diagnostic needed."""
        result = get_rns_shared_instance_info()

        assert result['available'] is False
        assert result['method'] == 'none'
        assert 'diagnostic' not in result

    @patch('src.utils._port_detection._check_proc_net_unix',
           return_value=True)
    def test_socket_available_no_zombie_check(self, mock_unix):
        """When shared instance is available, skip zombie detection."""
        result = get_rns_shared_instance_info()

        assert result['available'] is True
        assert result['method'] == 'unix_socket'
        assert 'diagnostic' not in result
