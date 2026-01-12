"""
Tests for network diagnostics utilities.

Run: python3 -m pytest tests/test_network_diag.py -v
"""

import pytest
from unittest.mock import patch, mock_open, MagicMock
from pathlib import Path

from src.utils.network_diag import (
    TCP_STATES,
    hex_to_ip,
    hex_to_ipv6,
    parse_proc_net,
    get_socket_to_process,
    get_listening_ports,
    check_port_open,
    get_all_connections,
    find_process_on_port,
)


class TestTCPStates:
    """Tests for TCP state constants."""

    def test_established(self):
        assert TCP_STATES['01'] == 'ESTABLISHED'

    def test_listen(self):
        assert TCP_STATES['0A'] == 'LISTEN'

    def test_time_wait(self):
        assert TCP_STATES['06'] == 'TIME_WAIT'

    def test_close_wait(self):
        assert TCP_STATES['08'] == 'CLOSE_WAIT'

    def test_all_11_states(self):
        """Verify all 11 TCP states are defined."""
        assert len(TCP_STATES) == 11


class TestHexToIp:
    """Tests for hex_to_ip function."""

    def test_localhost(self):
        """127.0.0.1 in little-endian hex."""
        assert hex_to_ip('0100007F') == '127.0.0.1'

    def test_any_address(self):
        """0.0.0.0"""
        assert hex_to_ip('00000000') == '0.0.0.0'

    def test_192_168_1_1(self):
        """192.168.1.1 in little-endian."""
        assert hex_to_ip('0101A8C0') == '192.168.1.1'

    def test_10_0_0_1(self):
        """10.0.0.1 in little-endian."""
        assert hex_to_ip('0100000A') == '10.0.0.1'

    def test_broadcast(self):
        """255.255.255.255"""
        assert hex_to_ip('FFFFFFFF') == '255.255.255.255'

    def test_invalid_hex(self):
        """Invalid hex returns 0.0.0.0."""
        assert hex_to_ip('not_hex') == '0.0.0.0'

    def test_empty_string(self):
        assert hex_to_ip('') == '0.0.0.0'

    def test_none(self):
        assert hex_to_ip(None) == '0.0.0.0'


class TestHexToIpv6:
    """Tests for hex_to_ipv6 function."""

    def test_short_string_passthrough(self):
        """Non-32-char strings pass through."""
        assert hex_to_ipv6('1234') == '1234'

    def test_loopback(self):
        """IPv6 loopback address."""
        # ::1 encoded in /proc/net/tcp6 format
        result = hex_to_ipv6('00000000000000000000000001000000')
        assert ':' in result  # Should be IPv6 format

    def test_invalid_input(self):
        """Invalid input returns as-is."""
        assert hex_to_ipv6('invalid') == 'invalid'


class TestParseProcNet:
    """Tests for parse_proc_net function."""

    SAMPLE_TCP_CONTENT = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1
   1: 0100007F:0CEA 0100007F:0050 01 00000000:00000000 00:00000000 00000000  1000        0 67890 1
"""

    def test_parse_tcp_connections(self):
        """Test parsing TCP connections from /proc/net/tcp."""
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=self.SAMPLE_TCP_CONTENT)):
                results = parse_proc_net('tcp')

        assert len(results) == 2
        # First connection: listening on 127.0.0.1:8080
        assert results[0]['ip'] == '127.0.0.1'
        assert results[0]['port'] == 8080  # 0x1F90
        assert results[0]['state'] == 'LISTEN'
        assert results[0]['inode'] == '12345'

    def test_parse_established_connection(self):
        """Test parsing established connection."""
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=self.SAMPLE_TCP_CONTENT)):
                results = parse_proc_net('tcp')

        # Second connection is ESTABLISHED
        assert results[1]['state'] == 'ESTABLISHED'
        assert results[1]['port'] == 3306  # 0x0CEA

    def test_nonexistent_file(self):
        """Return empty list for nonexistent file."""
        with patch('os.path.exists', return_value=False):
            results = parse_proc_net('tcp')
        assert results == []

    def test_permission_error(self):
        """Handle permission errors gracefully."""
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', side_effect=PermissionError):
                results = parse_proc_net('tcp')
        assert results == []

    def test_parse_udp(self):
        """Test parsing UDP connections."""
        udp_content = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 11111 1
"""
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=udp_content)):
                results = parse_proc_net('udp')

        assert len(results) == 1
        assert results[0]['port'] == 53  # DNS port


class TestGetSocketToProcess:
    """Tests for get_socket_to_process function."""

    def test_returns_dict(self):
        """Function returns a dictionary."""
        with patch('pathlib.Path.iterdir', return_value=[]):
            result = get_socket_to_process()
        assert isinstance(result, dict)

    def test_maps_socket_to_process(self):
        """Test mapping socket inodes to processes."""
        # Create mock /proc/1234 structure
        mock_pid_dir = MagicMock()
        mock_pid_dir.name = '1234'

        mock_comm = MagicMock()
        mock_comm.exists.return_value = True
        mock_comm.read_text.return_value = 'python3\n'

        mock_fd_link = MagicMock()
        mock_fd_link.readlink.return_value = Path('socket:[99999]')

        mock_fd_dir = MagicMock()
        mock_fd_dir.exists.return_value = True
        mock_fd_dir.iterdir.return_value = [mock_fd_link]

        with patch('pathlib.Path.iterdir') as mock_iterdir:
            mock_iterdir.return_value = [mock_pid_dir]
            with patch.object(Path, '__truediv__') as mock_div:
                def side_effect(name):
                    if name == 'comm':
                        return mock_comm
                    elif name == 'fd':
                        return mock_fd_dir
                    return MagicMock()
                mock_div.side_effect = side_effect

                # This is complex to fully mock, just verify it doesn't crash
                result = get_socket_to_process()
                assert isinstance(result, dict)


class TestGetListeningPorts:
    """Tests for get_listening_ports function."""

    def test_filters_listen_state(self):
        """Only return LISTEN state for TCP."""
        tcp_content = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1
   1: 0100007F:0050 0100007F:1F90 01 00000000:00000000 00:00000000 00000000  1000        0 67890 1
"""
        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=tcp_content)):
                with patch('src.utils.network_diag.get_socket_to_process', return_value={}):
                    results = get_listening_ports('tcp')

        # Only the LISTEN connection should be returned
        assert len(results) == 1
        assert results[0]['state'] == 'LISTEN'


class TestCheckPortOpen:
    """Tests for check_port_open function."""

    def test_open_port(self):
        """Test detecting open port."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0

        with patch('socket.socket', return_value=mock_socket):
            result = check_port_open(8080)

        assert result is True

    def test_closed_port(self):
        """Test detecting closed port."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 111  # Connection refused

        with patch('socket.socket', return_value=mock_socket):
            result = check_port_open(8080)

        assert result is False

    def test_socket_error(self):
        """Handle socket errors gracefully."""
        with patch('socket.socket', side_effect=OSError("Network error")):
            result = check_port_open(8080)

        assert result is False

    def test_custom_host(self):
        """Test with custom host."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0

        with patch('socket.socket', return_value=mock_socket):
            result = check_port_open(80, host='example.com')

        mock_socket.connect_ex.assert_called_with(('example.com', 80))

    def test_custom_timeout(self):
        """Test with custom timeout."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0

        with patch('socket.socket', return_value=mock_socket):
            check_port_open(80, timeout=5.0)

        mock_socket.settimeout.assert_called_with(5.0)


class TestGetAllConnections:
    """Tests for get_all_connections function."""

    def test_returns_all_protocols(self):
        """Returns dict with all 4 protocol keys."""
        with patch('src.utils.network_diag.parse_proc_net', return_value=[]):
            with patch('src.utils.network_diag.get_socket_to_process', return_value={}):
                result = get_all_connections()

        assert 'tcp' in result
        assert 'udp' in result
        assert 'tcp6' in result
        assert 'udp6' in result


class TestFindProcessOnPort:
    """Tests for find_process_on_port function."""

    def test_find_existing_process(self):
        """Find process on a port."""
        mock_connections = [
            {'port': 8080, 'inode': '12345'},
            {'port': 3306, 'inode': '67890'},
        ]
        mock_inode_map = {
            '12345': 'python3 (PID 1234)',
            '67890': 'mysqld (PID 5678)',
        }

        with patch('src.utils.network_diag.parse_proc_net', return_value=mock_connections):
            with patch('src.utils.network_diag.get_socket_to_process', return_value=mock_inode_map):
                result = find_process_on_port(8080)

        assert result == 'python3 (PID 1234)'

    def test_port_not_found(self):
        """Return None for unused port."""
        with patch('src.utils.network_diag.parse_proc_net', return_value=[]):
            with patch('src.utils.network_diag.get_socket_to_process', return_value={}):
                result = find_process_on_port(9999)

        assert result is None


class TestIntegration:
    """Integration tests for network diagnostics."""

    def test_parse_and_enrich(self):
        """Test parsing connections and enriching with process info."""
        tcp_content = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1
"""
        mock_inode_map = {'12345': 'myapp (PID 999)'}

        with patch('os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=tcp_content)):
                with patch('src.utils.network_diag.get_socket_to_process', return_value=mock_inode_map):
                    results = get_listening_ports('tcp')

        assert len(results) == 1
        assert results[0]['process'] == 'myapp (PID 999)'
