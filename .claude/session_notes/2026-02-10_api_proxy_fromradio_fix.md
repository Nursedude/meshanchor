# Session Notes: API Proxy fromradio Packet Drain Fix

**Date**: 2026-02-10
**Branch**: `claude/meshforge-meshtastic-integration-WjzT5`
**Commit**: `b65959f`

---

## Architecture Decision: Let meshtasticd Own Its Web Client

After multiple sessions fighting the `/mesh/` subpath approach (HTML rewriting, CSS injection, base tag manipulation, regex path stripping, API proxy ownership), the user made a clear call:

> "meshforge needs meshtasticd's telemetry, metrics etc. It doesn't need the webclient."
> "meshtastic webclient: meshtastic is going to get updated - we integrate the web-ui and we need to maintain the changes."

**The web client at ip:9443 is meshtasticd's responsibility. MeshForge is a gateway/NOC, not a web client host.**

---

## ROOT CAUSE FOUND: API Proxy Stealing fromradio Packets (Issue #28)

`MeshtasticApiProxy` was **enabled by default** in `MapServer.__init__` (`enable_api_proxy=True`). It ran a background poller that continuously drained `GET /api/v1/fromradio` from meshtasticd's HTTP API on port 9443. This is a queue-based endpoint — once consumed, packets are gone. The native web client at `:9443` got nothing.

**The gateway was always fine** — it uses TCP port 4403 (`TCPInterface`), a completely separate channel. NomadNet works via RNS. Neither touches port 9443.

### Connection Architecture (Now Correct)
```
Port 4403 (TCP protobuf) ── Gateway Bridge ── Works independently
Port 9443 (HTTP/Web)     ── meshtasticd owns this ── Native web client works
Port 5000 (HTTP)         ── MeshForge NOC map/APIs ── No interference
```

---

## Fix Applied

1. Default `enable_api_proxy` to **`False`** in `MapServer.__init__`
2. Added `--enable-api-proxy` CLI flag for explicit opt-in
3. `/mesh/` redirects to native `https://host:9443/` when proxy off
4. Clear logging when proxy disabled explaining coexistence
5. Updated startup messages to show native web client URL
6. Fixed test mock (bound `_rewrite_mesh_html`), added redirect test
7. Documented as **Issue #28** in `persistent_issues.md`

### Files Changed
- `src/utils/map_data_service.py` — `enable_api_proxy` default → False, CLI flag, startup messages
- `src/utils/map_http_handler.py` — `/mesh/` redirects to native `:9443`
- `tests/test_web_client_ownership.py` — mock fix, redirect test
- `.claude/foundations/persistent_issues.md` — Issue #28

### Tests
- 23/23 web client ownership tests pass
- Full suite: 3987 pass, 0 fail, 19 skipped

---

## What the User Told Us About Current State

- **Gateway is GREEN** — getting RX (not TX yet)
- **NomadNet talks to other RNS nodes** — RNS stack works
- **Lots of things work** — monitoring, wireshark tools, etc.
- **TX not working yet** — gateway receives but doesn't transmit
- **Web client at :9443 showed no data** — now fixed (API proxy was draining packets)

---

## NEXT SESSION PRIORITIES (ordered)

### P0: Gateway TX — Green RX but No TX
User confirmed gateway is receiving (RX green) but not transmitting. This is the #1 functional issue now that the web client contention is fixed. Investigate:
- Is `meshtastic_handler.py` calling `sendText()` / `sendData()` on the TCPInterface?
- Check if the bridge is properly translating RNS messages → Meshtastic packets
- Look at gateway logs for TX attempts vs failures
- Check channel/PSK configuration — does the gateway have the right keys to transmit?

### P1: Verify Web Client at :9443 Now Works
With the API proxy disabled, the native web client should work. User should test:
- Open `https://ip:9443/` — does it show data now?
- Check telemetry, node list, metrics
- If it still shows no data, the issue is meshtasticd-side (not MeshForge)

### P2: Meshtastic Web Client Updates
User mentioned: "meshtastic is going to get updated - we integrate the web-ui and we need to maintain the changes." This means:
- The Meshtastic web client will get upstream updates
- MeshForge should NOT fork/own the web client
- If MeshForge needs to augment the web client, use the API proxy opt-in (`--enable-api-proxy`)
- Consider contributing phantom node fixes upstream to meshtastic/web

### P3: rnsd Permission Fix — UNTESTED
Committed two sessions ago. Needs hardware verification on MOC2.

### P4: Grafana Metrics
Needs gateway running with metrics server on port 9090.
