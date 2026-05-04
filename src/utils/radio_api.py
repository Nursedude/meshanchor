"""HTTP handlers for the daemon's `/radio` endpoint.

Extracted from `utils.config_api` so `config_api.py` stays under the 1500-line
cap (Phase 5 prep). Behaviour is unchanged — the class methods on
`ConfigAPIHandler` are thin delegators to `handle_get` / `handle_put` here.

Routes (all served on :8081 by `ConfigAPIServer`):
    GET  /radio[?refresh=1]        — Phase 4a, LAN-readable
    PUT  /radio/lora               — Phase 4b, localhost-only
    PUT  /radio/tx_power           — Phase 4b, localhost-only
    PUT  /radio/channel/<idx>      — Phase 4b, localhost-only

The handler argument is a live `ConfigAPIHandler` instance — we just need its
I/O surface (`path`, `_send_json`, `_send_error_json`, `_read_body`). Typed as
`Any` here so this module has no circular dependency on config_api.
"""

from __future__ import annotations

from typing import Any


def handle_get(handler: Any) -> None:
    """Serve GET /radio — read-only snapshot of MeshCore radio state.

    `?refresh=1` (or `?refresh=true`) forces a live read from the device,
    bounded by the daemon-side timeout. Returns `{"radio": <state>}` on
    success; 503 if the MeshCore daemon isn't loaded or no handler is active;
    500 if the device read raises.
    """
    try:
        from gateway.meshcore_handler import get_active_handler
    except ImportError:
        handler._send_error_json(503, "MeshCore module not loaded")
        return

    active = get_active_handler()
    if active is None:
        handler._send_error_json(503, "MeshCore handler not active")
        return

    refresh = False
    if "?" in handler.path:
        query = handler.path.split("?", 1)[1]
        for param in query.split("&"):
            if param == "refresh=1" or param == "refresh=true":
                refresh = True
                break

    try:
        state = active.get_radio_state(refresh=refresh)
    except Exception as e:
        handler._send_error_json(500, f"Radio state read failed: {e}")
        return

    handler._send_json({"radio": state})


def handle_put(handler: Any) -> None:
    """Serve PUT /radio/* — write LoRa params, TX power, or channel slot.

    Three routes, all localhost-only (gated by the caller's `do_PUT`):
        PUT /radio/lora           body {freq, bw, sf, cr}
        PUT /radio/tx_power       body {value}
        PUT /radio/channel/<idx>  body {name, secret?}

    Each setter validates → calls meshcore_py → refreshes the cache so the
    response carries the post-write snapshot. `RadioWriteError` → 400; any
    other exception → 500.
    """
    try:
        from gateway.meshcore_handler import get_active_handler
        from gateway.meshcore_radio_config import RadioWriteError
    except ImportError:
        handler._send_error_json(503, "MeshCore module not loaded")
        return

    active = get_active_handler()
    if active is None:
        handler._send_error_json(503, "MeshCore handler not active")
        return

    body = handler._read_body()
    if body is None or not isinstance(body, dict):
        handler._send_error_json(400, "PUT requires a JSON object body")
        return

    path = handler.path.split("?", 1)[0]
    try:
        if path == "/radio/lora":
            state = active.set_radio_lora(
                freq_mhz=body.get("freq"),
                bw_khz=body.get("bw"),
                sf=body.get("sf"),
                cr=body.get("cr"),
            )
        elif path == "/radio/tx_power":
            state = active.set_radio_tx_power(dbm=body.get("value"))
        elif path.startswith("/radio/channel/"):
            idx_str = path[len("/radio/channel/"):]
            try:
                idx = int(idx_str)
            except ValueError:
                handler._send_error_json(400, f"Invalid channel idx: {idx_str!r}")
                return
            state = active.set_radio_channel(
                idx=idx,
                name=body.get("name", ""),
                secret_hex=body.get("secret"),
            )
        else:
            handler._send_error_json(404, f"Unknown radio path: {handler.path}")
            return
    except RadioWriteError as e:
        handler._send_error_json(400, str(e))
        return
    except Exception as e:
        handler._send_error_json(500, f"Radio write failed: {e}")
        return

    handler._send_json({"radio": state})
