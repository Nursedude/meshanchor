"""Unit tests for supervisor.protocol.

Wire format is small but it's the contract between the supervisor and
every consumer (gateway bridge, future TUI, future CLI). Drift here
silently breaks all of them; tests exist mainly to lock the schema.
"""

from __future__ import annotations

import json

import pytest

from supervisor import protocol as p


class TestEncodeDecode:
    def test_request_round_trip(self):
        wire = p.make_request(7, "get_radio_info")
        assert wire.endswith(b"\n")
        frame = p.decode(wire)
        assert frame == {
            "type": "request",
            "id": 7,
            "method": "get_radio_info",
            "args": {},
        }

    def test_response_round_trip(self):
        wire = p.make_response(7, {"node_num": 1234})
        frame = p.decode(wire)
        assert frame["type"] == "response"
        assert frame["id"] == 7
        assert frame["result"] == {"node_num": 1234}

    def test_error_round_trip(self):
        wire = p.make_error(8, "radio not connected")
        frame = p.decode(wire)
        assert frame["type"] == "error"
        assert frame["id"] == 8
        assert frame["error"] == "radio not connected"

    def test_event_round_trip(self):
        wire = p.make_event("channel_message", {"text": "hi", "channel": 0})
        frame = p.decode(wire)
        assert frame["type"] == "event"
        assert frame["event"] == "channel_message"
        assert frame["data"] == {"text": "hi", "channel": 0}

    def test_hello_serializes(self):
        hello = p.Hello(owner="meshcore-radio", mode="serial",
                        device="/dev/ttyMeshCore", connected=True)
        frame = p.decode(p.encode(hello))
        assert frame["type"] == "hello"
        assert frame["version"] == p.PROTOCOL_VERSION
        assert frame["owner"] == "meshcore-radio"
        assert frame["device"] == "/dev/ttyMeshCore"
        assert frame["connected"] is True


class TestValidation:
    def test_unknown_method_rejected(self):
        with pytest.raises(p.ProtocolError):
            p.make_request(1, "definitely_not_a_method")

    def test_unknown_event_kind_rejected(self):
        with pytest.raises(p.ProtocolError):
            p.make_event("not_an_event", {})

    def test_decode_empty_line_rejected(self):
        with pytest.raises(p.ProtocolError):
            p.decode(b"")

    def test_decode_invalid_json_rejected(self):
        with pytest.raises(p.ProtocolError):
            p.decode(b"{not json}")

    def test_decode_missing_type_rejected(self):
        with pytest.raises(p.ProtocolError):
            p.decode(json.dumps({"id": 1}).encode())


class TestJsonDefaultCoercion:
    def test_bytes_become_hex(self):
        line = p.encode({"type": "event", "event": "ack",
                         "data": {"key": b"\x01\x02"}})
        frame = p.decode(line)
        assert frame["data"]["key"] == "0102"

    def test_set_becomes_sorted_list(self):
        line = p.encode({"type": "event", "event": "ack",
                         "data": {"channels": {2, 0, 1}}})
        frame = p.decode(line)
        assert frame["data"]["channels"] == [0, 1, 2]


class TestStableSurface:
    """Locks the public METHOD / EVENT_KIND sets so any drift goes
    through code review. Adding new methods is fine — but the
    pre-existing names must keep working for older clients."""

    def test_methods_include_mvp_set(self):
        required = {
            "status", "get_radio_info", "get_contacts",
            "get_channels", "send_message", "ping",
        }
        assert required.issubset(p.METHODS)

    def test_event_kinds_include_mvp_set(self):
        required = {
            "contact_message", "channel_message",
            "advertisement", "ack", "connection_state",
        }
        assert required.issubset(p.EVENT_KINDS)

    def test_default_socket_path_under_meshcore_radio_runtime_dir(self):
        # The systemd unit declares RuntimeDirectory=meshcore-radio; the
        # default path must live under that or the supervisor cannot bind.
        assert p.DEFAULT_SOCKET_PATH.startswith("/run/meshcore-radio/")
