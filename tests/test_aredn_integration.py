"""
Integration test: AREDN API response parsing.

Tests AREDNClient and AREDNScanner against mock HTTP API responses
matching the real AREDN /a/sysinfo endpoint format.

Reference: https://docs.arednmesh.org/en/latest/arednHow-toGuides/devtools.html

Run: python3 -m pytest tests/test_aredn_integration.py -v
"""

import json
from unittest.mock import MagicMock, patch, PropertyMock
from io import BytesIO

import pytest

from src.utils.aredn import (
    AREDNClient,
    AREDNNode,
    AREDNLink,
    AREDNService,
    LinkType,
)


# =============================================================================
# REALISTIC AREDN API RESPONSES
# =============================================================================

SYSINFO_BASIC = {
    "api_version": "1.11",
    "node": "WH6GXZ-meshforge",
    "node_details": {
        "firmware_version": "3.24.6.0",
        "model": "MikroTik hAP ac lite",
        "board_id": "0x0000",
        "description": "MeshForge gateway node - Honolulu",
        "lat": "21.3069",
        "lon": "-157.8583",
        "grid_square": "BL11bh",
    },
    "sysinfo": {
        "uptime": "3 days, 12:34:56",
        "loads": [0.12, 0.08, 0.05],
    },
    "meshrf": {
        "ssid": "AREDN-v3",
        "channel": 177,
        "freq": "5885 MHz",
        "chanbw": "10 MHz",
        "status": "on",
    },
    "tunnels": {
        "active_tunnel_count": 2,
    },
}

SYSINFO_WITH_LINKS = {
    **SYSINFO_BASIC,
    "link_info": {
        "10.54.25.1": {
            "hostname": "WH6GXZ-sector1",
            "linkType": "RF",
            "linkQuality": 1.0,
            "neighborLinkQuality": 0.97,
            "signal": -65,
            "noise": -95,
            "tx_rate": 130,
        },
        "10.54.25.5": {
            "hostname": "KH6ABC-relay",
            "linkType": "DTD",
            "linkQuality": 1.0,
            "neighborLinkQuality": 1.0,
            "signal": 0,
            "noise": 0,
            "tx_rate": 1000,
        },
        "10.54.25.9": {
            "hostname": "KH6DEF-tunnel",
            "linkType": "TUN",
            "linkQuality": 0.85,
            "neighborLinkQuality": 0.82,
            "signal": 0,
            "noise": 0,
            "tx_rate": 0,
        },
    },
}

SYSINFO_WITH_SERVICES = {
    **SYSINFO_BASIC,
    "services_local": [
        {
            "name": "MeshForge NOC",
            "protocol": "http",
            "link": "http://WH6GXZ-meshforge:8080",
        },
        {
            "name": "MeshChat",
            "protocol": "http",
            "link": "http://WH6GXZ-meshforge:8081",
        },
    ],
}

SYSINFO_LOCATION_SEPARATE = {
    "api_version": "1.11",
    "node": "KH6XYZ-remote",
    "node_details": {
        "firmware_version": "3.24.6.0",
        "model": "MikroTik hAP ac2",
        "board_id": "0x0001",
    },
    "location": {
        "lat": "19.8968",
        "lon": "-155.5828",
        "gridsquare": "BK49xv",
    },
    "sysinfo": {
        "uptime": "1 day, 02:00:00",
        "loads": [0.05, 0.03, 0.02],
    },
    "meshrf": {
        "ssid": "AREDN-v3",
        "channel": 177,
        "freq": "5885 MHz",
        "chanbw": "10 MHz",
        "status": "on",
    },
    "tunnels": {"active_tunnel_count": 0},
}


# =============================================================================
# HELPER: Mock HTTP response
# =============================================================================

def mock_urlopen_response(data: dict):
    """Create a mock urllib response from a dict."""
    response = MagicMock()
    encoded = json.dumps(data).encode("utf-8")
    response.read.return_value = encoded
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)
    return response


# =============================================================================
# TEST: AREDN CLIENT API PARSING
# =============================================================================

class TestAREDNClientParsing:
    """Test AREDNClient parsing of /a/sysinfo responses."""

    def test_basic_sysinfo_parsing(self):
        """Test parsing of basic sysinfo response."""
        client = AREDNClient("10.54.25.2")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(SYSINFO_BASIC)):
            result = client.get_sysinfo()

        assert result is not None
        assert result["api_version"] == "1.11"
        assert result["node"] == "WH6GXZ-meshforge"
        assert result["node_details"]["model"] == "MikroTik hAP ac lite"

    def test_get_node_info_parses_all_fields(self):
        """Test full node info parsing with all fields populated."""
        client = AREDNClient("10.54.25.2")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(SYSINFO_WITH_LINKS)):
            node = client.get_node_info()

        assert node is not None
        assert isinstance(node, AREDNNode)

        # Basic info
        assert node.firmware_version == "3.24.6.0"
        assert node.model == "MikroTik hAP ac lite"
        assert node.description == "MeshForge gateway node - Honolulu"

        # Location
        assert abs(node.latitude - 21.3069) < 0.001
        assert abs(node.longitude - (-157.8583)) < 0.001
        assert node.grid_square == "BL11bh"
        assert node.has_location() is True

        # System info
        assert "3 days" in node.uptime
        assert len(node.loads) == 3

        # Mesh RF
        assert node.ssid == "AREDN-v3"
        assert node.channel == 177
        assert node.frequency == "5885 MHz"
        assert node.channel_width == "10 MHz"

        # Tunnels
        assert node.tunnel_count == 2

        # Links
        assert len(node.links) == 3

    def test_link_type_parsing(self):
        """Test that RF, DTD, and TUN link types are parsed correctly."""
        client = AREDNClient("10.54.25.2")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(SYSINFO_WITH_LINKS)):
            node = client.get_node_info()

        link_types = {link.hostname: link.link_type for link in node.links}

        assert link_types["WH6GXZ-sector1"] == LinkType.RF
        assert link_types["KH6ABC-relay"] == LinkType.DTD
        assert link_types["KH6DEF-tunnel"] == LinkType.TUN

    def test_rf_link_snr_calculation(self):
        """Test that SNR is calculated from signal - noise for RF links."""
        client = AREDNClient("10.54.25.2")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(SYSINFO_WITH_LINKS)):
            node = client.get_node_info()

        rf_link = next(l for l in node.links if l.link_type == LinkType.RF)
        assert rf_link.signal == -65
        assert rf_link.noise == -95
        assert rf_link.snr == 30  # -65 - (-95)

    def test_link_quality_values(self):
        """Test that link quality values are parsed correctly."""
        client = AREDNClient("10.54.25.2")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(SYSINFO_WITH_LINKS)):
            node = client.get_node_info()

        rf_link = next(l for l in node.links if l.hostname == "WH6GXZ-sector1")
        assert rf_link.link_quality == 1.0
        assert rf_link.neighbor_link_quality == 0.97

        tun_link = next(l for l in node.links if l.hostname == "KH6DEF-tunnel")
        assert tun_link.link_quality == 0.85

    def test_location_from_separate_field(self):
        """Test parsing location from 'location' field (different format)."""
        client = AREDNClient("10.54.25.10")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(SYSINFO_LOCATION_SEPARATE)):
            node = client.get_node_info()

        assert node is not None
        assert abs(node.latitude - 19.8968) < 0.001
        assert abs(node.longitude - (-155.5828)) < 0.001
        assert node.grid_square == "BK49xv"

    def test_node_without_location(self):
        """Test parsing node that has no location data."""
        data = {
            "api_version": "1.11",
            "node": "no-location-node",
            "node_details": {
                "firmware_version": "3.24.6.0",
                "model": "Ubiquiti NanoStation",
            },
            "sysinfo": {"uptime": "1:00:00", "loads": [0.01]},
            "meshrf": {"status": "on"},
            "tunnels": {"active_tunnel_count": 0},
        }

        client = AREDNClient("10.54.25.20")

        with patch("urllib.request.urlopen", return_value=mock_urlopen_response(data)):
            node = client.get_node_info()

        assert node is not None
        assert node.has_location() is False
        assert node.latitude is None


class TestAREDNClientErrors:
    """Test AREDNClient error handling."""

    def test_connection_refused(self):
        """Test handling of connection refused."""
        import urllib.error

        client = AREDNClient("10.54.25.99")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            result = client.get_sysinfo()

        assert result is None

    def test_http_404(self):
        """Test handling of HTTP 404 (node doesn't have API)."""
        import urllib.error

        client = AREDNClient("10.54.25.99")
        error = urllib.error.HTTPError(
            url="http://10.54.25.99/a/sysinfo",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=error):
            result = client.get_sysinfo()

        assert result is None

    def test_invalid_json_response(self):
        """Test handling of non-JSON response."""
        response = MagicMock()
        response.read.return_value = b"<html>Not JSON</html>"
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)

        client = AREDNClient("10.54.25.99")

        with patch("urllib.request.urlopen", return_value=response):
            result = client.get_sysinfo()

        assert result is None

    def test_timeout(self):
        """Test handling of connection timeout."""
        import socket

        client = AREDNClient("10.54.25.99", timeout=1)

        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            result = client.get_sysinfo()

        assert result is None


class TestAREDNClientIPDetection:
    """Test IP vs hostname detection."""

    def test_ip_detection(self):
        """Test that IP addresses are correctly detected."""
        client = AREDNClient("10.54.25.2")
        assert client.ip == "10.54.25.2"
        assert client.base_url == "http://10.54.25.2"

    def test_hostname_detection(self):
        """Test that hostnames use .local.mesh suffix."""
        client = AREDNClient("WH6GXZ-meshforge")
        assert client.ip is None
        assert client.base_url == "http://WH6GXZ-meshforge.local.mesh"


class TestAREDNNodeDataclass:
    """Test AREDNNode dataclass methods."""

    def test_has_location_valid(self):
        """Test has_location with valid coordinates."""
        node = AREDNNode(hostname="test", latitude=21.3, longitude=-157.8)
        assert node.has_location() is True

    def test_has_location_none(self):
        """Test has_location with None coordinates."""
        node = AREDNNode(hostname="test")
        assert node.has_location() is False

    def test_has_location_zero(self):
        """Test has_location rejects (0,0)."""
        node = AREDNNode(hostname="test", latitude=0.0, longitude=0.0)
        assert node.has_location() is False

    def test_base_url_with_ip(self):
        """Test base_url uses IP when available."""
        node = AREDNNode(hostname="test", ip="10.54.25.2")
        assert node.base_url == "http://10.54.25.2"

    def test_base_url_without_ip(self):
        """Test base_url uses .local.mesh when no IP."""
        node = AREDNNode(hostname="WH6GXZ-node")
        assert node.base_url == "http://WH6GXZ-node.local.mesh"

    def test_to_dict(self):
        """Test serialization to dict."""
        node = AREDNNode(
            hostname="test",
            ip="10.54.25.2",
            firmware_version="3.24.6.0",
            latitude=21.3,
            longitude=-157.8,
        )
        d = node.to_dict()

        assert d["hostname"] == "test"
        assert d["ip"] == "10.54.25.2"
        assert d["latitude"] == 21.3


class TestAREDNLinkDataclass:
    """Test AREDNLink dataclass."""

    def test_snr_auto_calculation(self):
        """Test that SNR is auto-calculated from signal and noise."""
        link = AREDNLink(
            ip="10.54.25.1",
            hostname="test",
            link_type=LinkType.RF,
            signal=-60,
            noise=-95,
        )
        assert link.snr == 35  # -60 - (-95)

    def test_to_dict(self):
        """Test link serialization."""
        link = AREDNLink(
            ip="10.54.25.1",
            hostname="test",
            link_type=LinkType.RF,
            link_quality=0.95,
            signal=-65,
            noise=-95,
        )
        d = link.to_dict()
        assert d["link_type"] == "RF"
        assert d["link_quality"] == 0.95


# =============================================================================
# TEST: AREDN SCANNER (subnet scanning)
# =============================================================================

class TestAREDNScannerImport:
    """Test AREDNScanner can be imported and configured."""

    def test_scanner_import(self):
        """Test that AREDNScanner can be imported."""
        from src.utils.aredn import AREDNScanner
        assert AREDNScanner is not None

    def test_scanner_initialization(self):
        """Test scanner with default timeout."""
        from src.utils.aredn import AREDNScanner

        scanner = AREDNScanner(timeout=2)
        assert scanner is not None
        assert scanner.timeout == 2

    def test_scanner_scan_subnet_mocked(self):
        """Test scanner scan_subnet with mocked responses."""
        from src.utils.aredn import AREDNScanner

        scanner = AREDNScanner(timeout=1)

        # Mock the client to avoid actual network calls
        with patch.object(AREDNClient, "get_node_info") as mock_info:
            mock_info.return_value = AREDNNode(
                hostname="test-node",
                ip="10.54.25.1",
                firmware_version="3.24.6.0",
                model="MikroTik",
            )

            nodes = scanner.scan_subnet("10.54.25.0/30")

        # Scanner should have attempted to scan IPs in range
        assert isinstance(nodes, list)
