"""IPC protocol for the MeshCore radio supervisor.

NDJSON over Unix-domain socket. Each line is one JSON object. The
protocol is small on purpose — easy to inspect with
``socat - UNIX-CONNECT:/run/meshanchor/meshcore-radio.sock`` when
something is wedged.

Three message types share the wire:

* **request** — client → supervisor. Has a numeric ``id``; client expects
  exactly one ``response`` or ``error`` with the same id.
* **response** / **error** — supervisor → client. Always replies to a
  request id.
* **event** — supervisor → client. Spontaneous notifications (RX
  message, advertisement, connection state change). No id; broadcast
  to every connected client.

Versioning: the protocol carries a ``version`` integer in the initial
hello frame the supervisor sends on accept. Clients compare and
disconnect on mismatch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 1

# Reserved methods the supervisor exposes. The list is stable; new ops
# go at the bottom. Keeping the set small in MVP — anything not here
# (bulk firmware ops, etc.) lives in Session 3+ work.
METHODS = frozenset({
    "status",            # → connection state, owner, mode, device, uptime
    "get_radio_info",    # → radio identity, region, preset, firmware
    "get_contacts",      # → list of contacts known to the radio
    "get_channels",      # → channel definitions
    "send_message",      # ← {kind: 'channel'|'contact', target, text}
    "ping",              # → liveness probe; cheap and never blocks on radio
})

# Event kinds broadcast spontaneously to every connected client.
EVENT_KINDS = frozenset({
    "contact_message",   # incoming DM
    "channel_message",   # incoming channel/broadcast
    "advertisement",     # node advertisement seen
    "ack",               # delivery confirmation
    "connection_state",  # supervisor connection up/down
})


class ProtocolError(Exception):
    """Raised when a frame cannot be parsed or violates the contract."""


@dataclass
class Hello:
    """First frame the supervisor sends after accept.

    Lets the client check protocol compatibility and learn the
    supervisor's identity (which radio it owns) before issuing
    requests.
    """
    type: str = "hello"
    version: int = PROTOCOL_VERSION
    owner: str = ""
    mode: Optional[str] = None
    device: Optional[str] = None
    connected: bool = False


@dataclass
class Request:
    id: int
    method: str
    args: Dict[str, Any] = field(default_factory=dict)
    type: str = "request"


@dataclass
class Response:
    id: int
    result: Any = None
    type: str = "response"


@dataclass
class ErrorReply:
    id: int
    error: str
    type: str = "error"


@dataclass
class Event:
    event: str
    data: Dict[str, Any] = field(default_factory=dict)
    type: str = "event"


def encode(message: Any) -> bytes:
    """Serialize a protocol dataclass to a single NDJSON line (with \\n)."""
    if hasattr(message, "__dataclass_fields__"):
        payload = asdict(message)
    elif isinstance(message, dict):
        payload = message
    else:
        raise ProtocolError(f"cannot encode {type(message).__name__}")
    line = json.dumps(payload, separators=(",", ":"), default=_json_default)
    return (line + "\n").encode("utf-8")


def decode(line: bytes | str) -> Dict[str, Any]:
    """Parse one NDJSON line into a plain dict.

    Caller is responsible for dispatching on ``type``. Returns the raw
    dict so callers can pluck whatever fields they need without having
    to reconstruct the dataclass on every receive.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        raise ProtocolError("empty frame")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict) or "type" not in obj:
        raise ProtocolError("frame missing 'type'")
    return obj


def _json_default(value: Any) -> Any:
    """Best-effort coercion for things meshcore_py hands us.

    Bytes (public keys) → hex; datetimes → ISO; sets → list; anything
    with ``__dict__`` → its dict. Don't be clever — opaque objects
    just become their repr so the wire never fails on a foreign type.
    """
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, set):
        return sorted(value, key=str)
    if hasattr(value, "__dict__"):
        return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
    return repr(value)


# ---- helpers used by both server and client --------------------------------


def make_response(req_id: int, result: Any) -> bytes:
    return encode(Response(id=req_id, result=result))


def make_error(req_id: int, error: str) -> bytes:
    return encode(ErrorReply(id=req_id, error=error))


def make_event(kind: str, data: Dict[str, Any]) -> bytes:
    if kind not in EVENT_KINDS:
        raise ProtocolError(f"unknown event kind: {kind}")
    return encode(Event(event=kind, data=data))


def make_request(req_id: int, method: str, **args: Any) -> bytes:
    if method not in METHODS:
        raise ProtocolError(f"unknown method: {method}")
    return encode(Request(id=req_id, method=method, args=args))


# ---- conventional socket location -----------------------------------------

# The supervisor's systemd unit declares ``RuntimeDirectory=meshcore-radio``
# (separate from meshanchor-daemon's ``meshanchor`` runtime dir, so the two
# services don't fight over ownership of /run/meshanchor).
DEFAULT_SOCKET_PATH = "/run/meshcore-radio/meshcore-radio.sock"
