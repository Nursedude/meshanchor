"""HTTP handlers for the daemon's `/radio` endpoint.

Extracted from `utils.config_api` so `config_api.py` stays under the 1500-line
cap (Phase 5 prep). Behaviour is unchanged — the class methods on
`ConfigAPIHandler` are thin delegators to `handle_get` / `handle_put` here.

Routes (all served on :8081 by `ConfigAPIServer`):
    GET  /radio[?refresh=1]        — Phase 4a, LAN-readable
    GET  /radio/firmware           — Session 4, LAN-readable
    PUT  /radio/lora               — Phase 4b, localhost-only
    PUT  /radio/tx_power           — Phase 4b, localhost-only
    PUT  /radio/channel/<idx>      — Phase 4b, localhost-only
    PUT  /radio/preset             — Session 4, localhost-only
    POST /radio/reset              — Session 4, localhost-only

The handler argument is a live `ConfigAPIHandler` instance — we just need its
I/O surface (`path`, `_send_json`, `_send_error_json`, `_read_body`). Typed as
`Any` here so this module has no circular dependency on config_api.
"""

from __future__ import annotations

from typing import Any


def _get_active(handler: Any):
    """Return the active MeshCoreHandler or None (with HTTP error sent)."""
    try:
        from gateway.meshcore_handler import get_active_handler
    except ImportError:
        handler._send_error_json(503, "MeshCore module not loaded")
        return None
    active = get_active_handler()
    if active is None:
        handler._send_error_json(503, "MeshCore handler not active")
        return None
    return active


def handle_get(handler: Any) -> None:
    """Serve GET /radio[?refresh=1] and GET /radio/firmware.

    `?refresh=1` (or `?refresh=true`) forces a live read from the device,
    bounded by the daemon-side timeout. Returns `{"radio": <state>}` on
    success; 503 if the MeshCore daemon isn't loaded or no handler is active;
    500 if the device read raises.
    """
    active = _get_active(handler)
    if active is None:
        return

    path = handler.path.split("?", 1)[0]
    if path == "/radio/firmware":
        try:
            info = active.get_firmware_info()
        except Exception as e:
            handler._send_error_json(500, f"Firmware info read failed: {e}")
            return
        handler._send_json({"firmware": info})
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
    """Serve PUT /radio/* — write LoRa params, TX power, channel slot, or preset.

    Routes (all localhost-only — gated by caller's `do_PUT`):
        PUT /radio/lora           body {freq, bw, sf, cr}
        PUT /radio/tx_power       body {value}
        PUT /radio/channel/<idx>  body {name, secret?}
        PUT /radio/preset         body {region, preset}

    Each setter validates → calls meshcore_py → refreshes the cache so the
    response carries the post-write snapshot. `RadioWriteError` → 400; any
    other exception → 500.
    """
    try:
        from gateway.meshcore_radio_config import RadioWriteError
    except ImportError:
        handler._send_error_json(503, "MeshCore module not loaded")
        return

    active = _get_active(handler)
    if active is None:
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
        elif path == "/radio/preset":
            region = body.get("region")
            preset = body.get("preset")
            if not region or not preset:
                handler._send_error_json(
                    400, "preset requires 'region' and 'preset' fields"
                )
                return
            state = active.apply_preset(region=region, preset=preset)
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


def handle_post(handler: Any) -> None:
    """Serve POST /radio/* — currently just `/radio/reset`.

    Body is ignored — soft-reset takes no parameters. Returns
    ``{"radio": <stale state>}`` to mirror the put response shape; the
    radio is restarting so the snapshot is informational.
    """
    try:
        from gateway.meshcore_radio_config import RadioWriteError
    except ImportError:
        handler._send_error_json(503, "MeshCore module not loaded")
        return

    active = _get_active(handler)
    if active is None:
        return

    path = handler.path.split("?", 1)[0]
    if path != "/radio/reset":
        handler._send_error_json(404, f"Unknown radio path: {handler.path}")
        return

    try:
        state = active.reset_radio()
    except RadioWriteError as e:
        handler._send_error_json(400, str(e))
        return
    except Exception as e:
        handler._send_error_json(500, f"Radio reset failed: {e}")
        return

    handler._send_json({"radio": state})
