# Session Status: RNS Address-in-Use & Gateway Bridge Fixes

> **Branch**: `claude/fix-address-in-use-FktgY`
> **Date**: 2026-01-27
> **Status**: PAUSED - Need deeper RNS understanding before continuing

---

## What Was Fixed (committed & pushed)

### 1. TUI: Better error messages for rnpath/rnstatus
- Captures both stdout AND stderr (RNS logs errors to stdout)
- Detects "Address already in use" specifically, shows port conflict diagnostics
- Detects "No shared instance" specifically, suggests `share_instance = Yes`
- Handles FileNotFoundError (RNS not installed) and TimeoutExpired separately
- Removed invalid `-s` flag from rnstatus (it means `--sort`, not summary)

### 2. Bridge: RNS initialization from main thread
- Added `_init_rns_main_thread()` called from `start()` before spawning threads
- Fixes "signal only works in main thread" error that permanently blocked RNS
- When rnsd running: connects as client with `share_instance = Yes` config
- Handles `OSError("reinitialise")` when node_tracker already initialized the singleton
- Sets `_rns_pre_initialized` flag so `_connect_rns()` skips to LXMF setup

### 3. Bridge: errno 98 handling
- No longer falls through to identity/LXMF setup with `_reticulum = None`
- Re-checks for rnsd (race condition between initial check and bind)
- If rnsd found: marks permanent (defer to rnsd)
- If stale socket: marks transient (allows retry with backoff)

---

## What Is Still Broken

### The Real Problem: Incomplete /root/.reticulum/config

The user's rnsd config at `/root/.reticulum/config` only has:
```ini
[[Default Interface]]
    type = AutoInterface
    enabled = Yes
```

**Missing**:
1. `[reticulum]` section with `share_instance = Yes` - without this, rnstatus
   and client apps can't connect to rnsd's shared instance
2. `[[Meshtastic Interface]]` with `type = Meshtastic_Interface` and
   `tcp_port = 127.0.0.1:4403` - without this, RNS has NO path to
   Meshtastic/LongFast

The complete working template exists at `templates/reticulum.conf` and has
both sections. It needs to be deployed to `/root/.reticulum/config`.

### Environment Facts
- `.reticulum` config lives in `/root/` (rnsd runs as root)
- meshtasticd is running on port 4403
- rnsd is running (PID 2543) but without shared instance or Meshtastic interface
- The `Meshtastic_Interface` plugin from github.com/landandair/RNS_Over_Meshtastic
  must be installed in RNS's interfaces directory
- NomadNet config exists (`[node]` section with `enable_node = yes`)

---

## Wrong Assumptions Made This Session

1. **Assumed .reticulum was in user home** - It's in /root. Wasted time adding
   `--config` overrides and `sudo -u $SUDO_USER` privilege drops that were wrong.
   Both were reverted.

2. **Didn't understand shared instance** - Treated "rnsd running" as sufficient.
   Without `share_instance = Yes` in config, rnsd doesn't expose the shared
   instance socket that rnstatus/rnpath/client apps need.

3. **Didn't understand Meshtastic_Interface** - Assumed the MeshForge bridge
   was purely application-layer translation. Actually, RNS needs the
   `Meshtastic_Interface` plugin configured as an interface in
   `~/.reticulum/config` to bridge RNS packets over Meshtastic's LoRa.

---

## Knowledge Gaps to Fill Before Next Session

### RNS Architecture (MUST UNDERSTAND)
- **Shared Instance model**: How rnsd exposes its instance to other programs
  (port 37428, instance_control_port 37429). How clients connect. What config
  controls this.
- **Interface types**: AutoInterface (UDP multicast), TCPServer/Client,
  RNodeInterface, SerialInterface, Meshtastic_Interface (plugin). How they
  compose.
- **Transport mode**: `enable_transport = Yes/No` - what it means for routing
- **RNS singleton**: One Reticulum instance per process. Can't reinitialize.
  How shared instance works across processes.

### RNS Ecosystem Apps
- **NomadNet**: Terminal mesh messenger. Uses LXMF. Has its own config at
  `~/.nomadnetwork/config`. The user has a `[node]` section.
- **Sideband**: Mobile/desktop RNS messenger
- **MeshChat**: Another mesh chat app
- **rnsh**: SSH over RNS
- All connect to rnsd's shared instance

### Meshtastic_Interface Plugin
- Source: https://github.com/landandair/RNS_Over_Meshtastic
- Installs as `Meshtastic_Interface.py` in RNS's Interfaces directory
- Config options: `tcp_port`, `port` (serial), `ble_port`, `data_speed`,
  `mode` (gateway/client), `hop_limit`
- `data_speed` values: 0=LONG_FAST, 6=SHORT_FAST, 8=SHORT_TURBO
- This is how RNS talks to Meshtastic - NOT through the MeshForge bridge

### MeshForge Bridge vs Meshtastic_Interface
- **Meshtastic_Interface**: RNS interface plugin. RNS sends/receives RNS
  packets THROUGH Meshtastic's LoRa as a transport. Native RNS protocol.
- **MeshForge Bridge**: Application-layer translator. Converts between
  Meshtastic text messages and LXMF messages. Different purpose.
- Both can coexist. The Meshtastic_Interface is the foundation layer.

---

## Key Files for Reference

| File | Purpose |
|------|---------|
| `templates/reticulum.conf` | Complete working RNS config with Meshtastic_Interface |
| `config_templates/rns_meshtastic_gateway.conf` | Detailed gateway config with comments |
| `src/gateway/templates/rns/meshtastic_bridge.conf` | Bridge-specific RNS config |
| `.claude/research/rns_complete.md` | RNS API reference and architecture |
| `.claude/research/rns_integration.md` | Integration patterns |
| `.claude/research/rns_comprehensive.md` | Comprehensive RNS docs |
| `.claude/foundations/persistent_issues.md` | Known issues including Path.home() |

---

## Next Steps When Resuming

1. **Read RNS documentation thoroughly** - https://reticulum.network/manual/
   Focus on: shared instance, interface types, transport mode, config format
2. **Verify Meshtastic_Interface is installed** - Check if the plugin exists
   in RNS's Interfaces directory (`/usr/local/lib/python3.13/dist-packages/RNS/Interfaces/`)
3. **Deploy correct config** - Copy `templates/reticulum.conf` to
   `/root/.reticulum/config` (after backup)
4. **Restart rnsd** and verify `rnstatus` works
5. **Test bridge** with proper RNS config in place
6. **Consider**: Should the TUI setup wizard check for and offer to fix
   the RNS config?
