"""
Tests for MeshtasticApiProxy node sanitization.

The Meshtastic web client crashes when clicking nodes with incomplete
data (phantom nodes from MQTT). The proxy sanitizes /json/nodes responses
to ensure all nodes have required fields.

See: https://github.com/meshtastic/web/issues/862
"""

import json
import pytest


# Import the static method directly
from gateway.meshtastic_api_proxy import MeshtasticApiProxy


class TestSanitizeNodesJson:
    """Tests for _sanitize_nodes_json static method."""

    def test_healthy_nodes_unchanged(self):
        """Nodes with complete data should pass through unmodified."""
        nodes = {
            "!aabbccdd": {
                "num": 2864434397,
                "user": {
                    "id": "!aabbccdd",
                    "longName": "Hilltop-1",
                    "shortName": "HT1",
                    "hwModel": "RAK4631",
                },
                "role": "CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert parsed["!aabbccdd"]["user"]["longName"] == "Hilltop-1"
        assert parsed["!aabbccdd"]["user"]["shortName"] == "HT1"
        assert parsed["!aabbccdd"]["role"] == "CLIENT"

    def test_phantom_node_no_user(self):
        """Phantom node with no 'user' object gets defaults."""
        nodes = {
            "!deadbeef": {
                "num": 3735928559,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        node = parsed["!deadbeef"]
        assert "user" in node
        assert node["user"]["longName"]  # Not empty
        assert node["user"]["shortName"] == "????"
        assert node["user"]["hwModel"] == "UNSET"
        assert node["role"] == "CLIENT"

    def test_phantom_node_empty_user(self):
        """Node with empty user fields gets defaults filled in."""
        nodes = {
            "!11223344": {
                "num": 287454020,
                "user": {
                    "id": "!11223344",
                    "longName": "",
                    "shortName": "",
                    "hwModel": "",
                },
                "role": "ROUTER",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        user = parsed["!11223344"]["user"]
        assert user["longName"]  # Should be filled with default
        assert user["shortName"] == "????"
        assert user["hwModel"] == "UNSET"
        # Existing role should be preserved
        assert parsed["!11223344"]["role"] == "ROUTER"

    def test_missing_role_gets_default(self):
        """Node with missing 'role' field gets CLIENT default."""
        nodes = {
            "!aabb0011": {
                "num": 2864054289,
                "user": {
                    "id": "!aabb0011",
                    "longName": "M3GO",
                    "shortName": "M3GO",
                    "hwModel": "HELTEC_V3",
                },
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert parsed["!aabb0011"]["role"] == "CLIENT"
        # User data should be preserved
        assert parsed["!aabb0011"]["user"]["longName"] == "M3GO"

    def test_mixed_healthy_and_phantom(self):
        """Mix of healthy and phantom nodes — only phantoms get patched."""
        nodes = {
            "!healthy01": {
                "num": 1,
                "user": {
                    "id": "!healthy01",
                    "longName": "Good Node",
                    "shortName": "GOOD",
                    "hwModel": "RAK4631",
                },
                "role": "CLIENT",
            },
            "!phantom01": {
                "num": 2,
                # No user object at all
            },
            "!phantom02": {
                "num": 3,
                "user": {
                    "id": "!phantom02",
                    "longName": "",
                    "shortName": "",
                },
            },
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Healthy node preserved exactly
        assert parsed["!healthy01"]["user"]["longName"] == "Good Node"
        assert parsed["!healthy01"]["role"] == "CLIENT"

        # Phantom nodes patched
        assert parsed["!phantom01"]["user"]["longName"]  # Has a default
        assert parsed["!phantom01"]["role"] == "CLIENT"
        assert parsed["!phantom02"]["user"]["shortName"] == "????"

    def test_invalid_json_passes_through(self):
        """Non-JSON data passes through unchanged."""
        data = b"this is not json"
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        assert result == data

    def test_non_dict_json_passes_through(self):
        """JSON that isn't a dict (e.g., list) passes through."""
        data = json.dumps([1, 2, 3]).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        assert result == data

    def test_empty_dict_passes_through(self):
        """Empty node dict passes through."""
        data = json.dumps({}).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        assert json.loads(result) == {}

    def test_user_is_none_gets_replaced(self):
        """Node where 'user' is null/None gets a proper user object."""
        nodes = {
            "!nulluser": {
                "num": 99,
                "user": None,
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        assert isinstance(parsed["!nulluser"]["user"], dict)
        assert parsed["!nulluser"]["user"]["longName"]
        assert parsed["!nulluser"]["user"]["shortName"] == "????"

    def test_partial_user_preserves_existing(self):
        """Node with some user fields keeps existing data."""
        nodes = {
            "!partial": {
                "num": 50,
                "user": {
                    "id": "!partial",
                    "longName": "M3shGO",
                    # shortName missing
                    # hwModel missing
                },
                "role": "ROUTER_CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Existing fields preserved
        assert parsed["!partial"]["user"]["longName"] == "M3shGO"
        assert parsed["!partial"]["role"] == "ROUTER_CLIENT"
        # Missing fields filled
        assert parsed["!partial"]["user"]["shortName"] == "????"
        assert parsed["!partial"]["user"]["hwModel"] == "UNSET"

    def test_long_name_default_uses_last_4_chars(self):
        """Default longName uses last 4 chars of node key."""
        nodes = {
            "!aabbccdd": {
                "num": 2864434397,
                "user": {
                    "id": "!aabbccdd",
                    "longName": "",
                    "shortName": "TEST",
                    "hwModel": "HELTEC_V3",
                },
                "role": "CLIENT",
            }
        }
        data = json.dumps(nodes).encode()
        result = MeshtasticApiProxy._sanitize_nodes_json(data)
        parsed = json.loads(result)

        # Should use last 4 chars of key for default name
        assert "ccdd" in parsed["!aabbccdd"]["user"]["longName"]
