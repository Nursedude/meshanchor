# MeshCore High-Integration Charter

> **Goal**: Make the MeshCore companion radio first-class in MeshAnchor — same caliber of integration MeshForge has with Meshtastic. Without this floor, every later MeshCore feature (config push, status panels, firmware OTA, fleet sync) reinvents its own ad-hoc port-sharing and lifecycle handling.
> **Author**: WH6GXZ + Claude Code
> **Created**: 2026-05-05
> **Status**: Session 1 SHIPPED (PR #67, merged 2026-05-05). Sessions 2–4 pending.
> **Related**: `.claude/foundations/persistent_issues.md` #17 (Meshtastic connection contention) and #33 (MeshCore connection contract); `project_meshcore_high_integration_charter.md` in repo memory.

---

## Why now

The RAK4631 (`/dev/ttyMeshCore`) on meshanchor-server has been live since 2026-05-02, but the integration is shallow:

- The gateway bridge handler is the **only** consumer; there's no shared infrastructure for a second TUI/CLI consumer to talk to the radio without thrashing the port.
- The radio's lifecycle is **tied to the bridge daemon** — restarting the bridge restarts the radio session, and there's no surface between them.
- MeshAnchor passively reads the radio's region/preset; it doesn't **own** the configuration the way MeshForge owns Meshtastic's via `meshtasticd` + the connection manager.
- Beyond chat, **the TUI has no first-class controls** for the radio (no status panel, no reset, no preset switch, no firmware update).

The Meshtastic side hit this exact wall in Issue #17 — ~100 hours of circular regressions cured by the connection-manager pattern + the regression-prevention system from Issue #29. Applying the same pattern up-front for MeshCore avoids the same cost.

---

## The four gaps (operator ask, 2026-05-05)

1. **Radio-level service / supervisor** — RAK has its own lifecycle independent of the bridge daemon.
2. **Connection-manager pattern** — `MeshCoreConnection` / `MESHCORE_CONNECTION_LOCK` mirroring `meshtastic_connection.py`, so future TUI/CLI consumers share the link.
3. **Config-ownership model** — MeshAnchor selects firmware/region/preset and pushes with verification (parallels Issues #21/#22/#23 for Meshtastic).
4. **First-class TUI flows** — radio status / reset / firmware / preset switch.

Sessions 2–4 all depend on Session 1's foundation (#2). Without it, each later session would invent its own port-sharing scheme. The dependency graph is recorded in the local task list with `addBlockedBy`.

---

## Session 1 — connection-manager pattern (SHIPPED 2026-05-05, PR #67)

### What's on disk

**`src/utils/meshcore_connection.py` (new, ~390 lines)**

| Surface | Contract |
|---|---|
| `MESHCORE_CONNECTION_LOCK` | Module-level `threading.Lock`. Held during device open/close. Anyone touching the OS device must hold this — see MF014. |
| `class MeshCoreConnectionManager` (singleton via `get_connection_manager()`) | Tracks the live MeshCore instance + the asyncio loop that owns it. |
| `register_persistent(meshcore, loop, *, owner, mode, device)` | Long-running owner publishes itself. Caller already holds the lock. |
| `unregister_persistent()` | Called on disconnect / failure. |
| `get_meshcore()` / `get_loop()` / `get_persistent_owner()` / `status()` | Shared read access — no lock needed (meshcore_py serializes its own command queue). |
| `run_in_radio_loop(coro, timeout)` | Sync→async handoff. Schedules `coro` on the persistent owner's loop, blocks for the result. **The way TUI/CLI consumers talk to the live radio.** |
| `acquire_for_connect(owner, lock_timeout)` | Context manager wrapping a connect bring-up. Yields `True` if lock acquired AND no incumbent persistent owner. The handler wraps its `MeshCore.create_*` call in this. |
| `MeshCoreConnection(device_path, baud_rate, *, lock_timeout, respect_persistent)` | Short-lived sync ctxmgr for one-shot ops (probes, future CLI helpers). Returns `None` from `__enter__` if the persistent owner is active or the lock is busy. `respect_persistent=False` is the explicit-admin escape hatch (force reset). |
| `validate_meshcore_device(...)` / `detect_meshcore_devices()` | Moved here from `gateway/meshcore_handler.py`. The validate path skips raw open while a persistent owner is active and returns synthetic OK with an `error="persistent owner '...' active"` note — so `Detect Devices` flows from the TUI no longer race the bridge. |

**`src/gateway/meshcore_handler.py` retrofit:**
- Connect path wrapped in `acquire_for_connect(owner="gateway-bridge", lock_timeout=30.0)`.
- After `MeshCore.create_serial / create_tcp` returns, calls `register_persistent(...)` BEFORE the lock context exits — short-lived consumers see the link the moment the lock releases, with no race window.
- `unregister_persistent()` on `disconnect()` and on the connect-failure path.

### Enforcement

- **Lint MF014** (`scripts/lint.py`): direct `MeshCore.create_serial` / `create_tcp` and raw `serial.Serial(...)` on MeshCore-class files outside the connection infrastructure fail the lint. Allowlist: `meshcore_connection.py` (the infra) and `meshcore_handler.py` (the persistent owner — uses `acquire_for_connect`).
- **Issue #33** in `.claude/foundations/persistent_issues.md` — full contract documented.
- **Regression guard**: `tests/test_regression_guards.py::TestMeshCoreConnectionContract` (3 tests). Direct-create ratchet at 0; raw-serial ratchet at 0; handler invariants (acquire / register / unregister all present).
- **Unit tests**: `tests/test_meshcore_connection.py` (22 tests). Singleton behavior, registration, lock semantics, lock-timeout, real asyncio-loop round-trip via `run_in_radio_loop`, validate-skip when persistent owner active, detect-ordering.
- **MF001 lint rule** also picked up an f-string false-positive fix in passing.

### Field state

**Pre-deploy → deployed 2026-05-05 → confirmed reachable, blocked by env drift.**

Restart of `meshanchor-daemon.service` exercised the new connect path. Journal shows the new code error path firing cleanly (`MeshCore device not found: /dev/ttyUSB0`) but with the **wrong device path** because the systemd unit doesn't propagate `SUDO_USER` / `HOME`, so `get_real_user_home()` resolves to `/root` and the operator's `gateway.json` at `/home/wh6gxz/.config/meshanchor/` is invisible. This is **deploy drift #6** — orthogonal to Session 1's code, same drift class as PRs #61–#65. See `project_deploy_drift_6_systemd_env.md` in memory. **Fix is a 2-line operator paste** (Environment override + restart) recorded there.

---

## Session 2 — radio supervisor (lifecycle separation)

### What gap #1 is asking for

A surface where the radio has its own lifecycle independent of the bridge daemon. Today the bridge daemon ALSO owns the serial port; restarting the bridge restarts the radio session. We want:

- The radio's connection persists across bridge restarts.
- A clear surface for "is the radio up?" that's not "is the daemon up?"
- Health monitoring + exponential backoff like `gateway/rns_bridge.py` does for RNS.

### Two design choices (decide first thing in Session 2)

**Option A — thin Python supervisor service:**
- New unit `meshcore-radio.service` runs `src/supervisor/meshcore_radio.py`.
- The supervisor is the persistent owner (registers via `register_persistent`).
- Exposes a local Unix socket / loopback HTTP for the daemon and TUI to talk through.
- Daemon and TUI become `run_in_radio_loop` consumers via that socket → in-process forwarding.
- **Pros:** Cleanest separation. Restarting the daemon doesn't touch the radio. The supervisor can be tiny and very stable.
- **Cons:** New IPC surface to design + secure. New unit to install + monitor.

**Option B — elevate `meshcore_handler` into a standalone service:**
- New unit `meshcore-radio.service` runs a shim that imports the existing handler and runs it standalone.
- Daemon stops creating its own handler; instead it consumes via `run_in_radio_loop` against the standalone instance.
- **Pros:** Reuses existing handler code as-is. Smaller diff.
- **Cons:** The handler isn't currently designed for cross-process consumers — `run_in_radio_loop` works in-process via the singleton, but the daemon and TUI run in different processes. We'd still need an IPC layer; this option just hides it.

**Recommendation going into Session 2:** **A**. The IPC design is the actual hard part either way; option B's "smaller diff" is illusory because it ends up needing the same IPC. Option A makes the boundary explicit.

### Concrete deliverables for Session 2

- `templates/systemd/meshcore-radio.service` (or `scripts/meshcore-radio.service` to match the daemon's location — check Issue #61's deploy drift fix).
- `src/supervisor/meshcore_radio.py` — owns the asyncio loop, holds `register_persistent`, exposes a Unix socket protocol.
- `src/utils/meshcore_supervisor_client.py` — daemon/TUI side of the IPC; conforms to the existing `MeshCoreConnectionManager.run_in_radio_loop` contract so consumers don't notice the cross-process boundary.
- Health monitoring + exponential backoff (1s → 30s) modeled on `rns_bridge.py`.
- Update `utils.service_check.KNOWN_SERVICES` and the regression guard.
- **Pin `Environment=SUDO_USER=wh6gxz`** in the unit so deploy drift #6 doesn't recur for this service. Same pin should be added to `meshanchor-daemon.service` while we're in there.

### Test plan

- Unit tests for the supervisor's asyncio loop + IPC framing.
- Integration test: start supervisor, start a fake daemon-side consumer, send a `get_contacts` round-trip, kill the daemon, verify supervisor stays up + radio session intact, restart daemon, verify reconnect.

---

## Session 3 — config-ownership (region / preset / firmware)

### What gap #3 is asking for

Today MeshAnchor reads but doesn't OWN the radio's config. Operator changes preset out-of-band → MeshAnchor never notices. Operator's saved preferences in `gateway.json` aren't pushed to the radio on connect. Firmware version is invisible.

This parallels three Meshtastic-side issues:
- **#21** — verify after CLI commands (Meshtastic CLI is unreliable about preset changes). MeshCore-py has its own command surface; same verification discipline applies.
- **#22** — don't overwrite vendor configs we don't own. For MeshCore, the radio's internal config is owned by the firmware, NOT by MeshAnchor. We push *desired values* via the wire protocol; we never write firmware files.
- **#23** — post-install verification before declaring success.

### Concrete deliverables for Session 3

- `src/utils/meshcore_config.py` — read current state via `run_in_radio_loop(meshcore.commands.get_radio_info(), ...)`; push desired values; verify with a re-read.
- Define what we own (preset, region, name, frequency offset) vs. what the firmware owns (PHY internals, MAC layer, BLE settings).
- A `meshcore_config_doctor()` parallel to MeshForge's Config Doctor (deferred backport — see `project_meshforge_deferred_backports.md`).
- Surface drift: if the radio's actual preset doesn't match our desired preset, log a WARNING with a fix hint.

### Open questions

- Should we cache "last seen radio state" in `~/.config/meshanchor/meshcore_state.json` to detect drift across restarts? (Yes.)
- Where does region come from? Operator selects in TUI, persisted in `gateway.json[meshcore]`, pushed on connect. (Same as preset.)
- Firmware update flow: meshcore-cli has `flash` semantics, but mid-flash the connection manager has a hostile environment. **Defer firmware OTA** to its own follow-up — Session 3 does *detect* current firmware, push *operational* config, and surface "your radio is on firmware X, latest is Y" as info only.

---

## Session 4 — TUI radio control flows

### What gap #4 is asking for

First-class TUI surfaces beyond chat. Operator should be able to:
- See radio status (link state, RSSI, battery, firmware, region, preset, last RX/TX time).
- Reset the radio (soft reset via wire protocol; hard reset via instructions to power-cycle).
- Switch preset with verification.
- See / initiate firmware update (info-only in this session; flash is its own follow-up).

### Concrete deliverables for Session 4

- New handlers under `section=meshcore` in the registry. Probable file layout:
  - `launcher_tui/handlers/meshcore_status.py` — status panel.
  - `launcher_tui/handlers/meshcore_radio_control.py` — reset / preset switch.
  - `launcher_tui/handlers/meshcore_firmware.py` — version info + update prompt.
- All read paths use `get_connection_manager().run_in_radio_loop(...)` — no second connection ever opened.
- Reset / preset switch uses `MeshCoreConnection(respect_persistent=False)` — explicit admin override that briefly evicts the bridge handler, performs the operation, re-registers.
- Tests focus on the handler's logic separately from whiptail UI (per `.claude/rules/testing.md` testing guidelines).

### UX

The MeshChatX direction memory (`project_chat_ux_direction.md`) applies: inline whiptail menus stay load-bearing for operator verification; tmux pane is parked dead code; no curses replacement except on explicit ask.

---

## API contract for future sessions to consume

Sessions 2/3/4 all consume the Session-1 contract. The minimum surface they need is documented here so they can be scoped without reopening Session 1.

**For long-running owners** (supervisor in Session 2):
```python
from utils.meshcore_connection import (
    acquire_for_connect, get_connection_manager,
    ConnectionMode,
)

with acquire_for_connect(owner="meshcore-radio", lock_timeout=30.0) as got_lock:
    if not got_lock:
        return  # someone else holds it
    meshcore = await MeshCore.create_serial(device_path, baud_rate)
    # subscribe / start_auto_message_fetching here
    get_connection_manager().register_persistent(
        meshcore, loop,
        owner="meshcore-radio",
        mode=ConnectionMode.SERIAL,
        device=device_path,
    )
# lock released; persistent owner observable
```

**For sync consumers** (TUI status panels in Session 4, config doctor in Session 3):
```python
from utils.meshcore_connection import get_connection_manager

mgr = get_connection_manager()
if not mgr.has_persistent():
    return "radio not connected"

# Talk to the live radio — no second connection
info = mgr.run_in_radio_loop(
    mgr.get_meshcore().commands.get_radio_info(),
    timeout=5.0,
)
```

**For exclusive ops** (reset / preset switch in Session 4):
```python
from utils.meshcore_connection import MeshCoreConnection

# respect_persistent=False = explicit operator override; current owner
# is briefly evicted. Caller must coordinate with the supervisor to
# re-register afterwards.
with MeshCoreConnection(device_path, respect_persistent=False) as conn:
    if conn is None:
        return "could not acquire serial lock"
    # ... raw probe / reset sequence ...
```

---

## Invariants future sessions must preserve

1. **Only `meshcore_connection.py` and `meshcore_handler.py` may call `MeshCore.create_serial / create_tcp` directly.** New persistent owners (e.g., the supervisor) need to be added to both the lint allowlist (`scripts/lint.py` MF014) AND the regression guard allowlist (`TestMeshCoreConnectionContract.ALLOWLISTED`).
2. **Raw `serial.Serial(...)` on MeshCore devices is forbidden outside the connection infrastructure.** MF014 enforces this.
3. **`register_persistent` and `unregister_persistent` always come in pairs.** If a future owner crashes mid-connect, it must catch + unregister (see `meshcore_handler._connect`'s except block as the pattern).
4. **Sync→async handoffs go through `run_in_radio_loop`.** Direct `asyncio.run_coroutine_threadsafe(coro, mgr.get_loop())` works but bypasses the timeout and error normalization — don't.
5. **Lint clean + regression guards green before any merge.** Pre-commit hook enforces; Session 1's PR #67 demonstrates the workflow.

---

## Deploy notes

- **Deploy drift #6 is open**: `meshanchor-daemon.service` doesn't pin `Environment=SUDO_USER=wh6gxz`. Sessions 2 and 3 should bake `Environment=SUDO_USER=wh6gxz` into ALL meshanchor unit files (the existing daemon, the new meshcore-radio, the map service) so that `get_real_user_home()` resolves consistently across clean restarts. See `project_deploy_drift_6_systemd_env.md` in memory.
- **NOPASSWD scope on meshanchor-server is narrow** — covers `/usr/bin/systemctl {restart,start,stop,status,reload,daemon-reload}` on the three meshanchor units (with `.service` suffix; absolute path required) plus `launcher.py *`. Does NOT cover `cp`, `install`, `tee`, or unit-file edits. New service additions in Session 2 need a sudoers update or operator-interactive install. See `reference_meshanchor_server_sudo.md`.

---

## Why this ordering

| Session | Blocked by | Why |
|---|---|---|
| 1 (connection manager) | — | Foundation. Everything else needs port-sharing. |
| 2 (supervisor) | 1 | Supervisor is a persistent owner; can't write it without `register_persistent`. |
| 3 (config-ownership) | 1 | Config push needs `MeshCoreConnection` for exclusive writes without fighting the bridge. |
| 4 (TUI) | 1, 3 | Status panels read via `run_in_radio_loop`; preset switch needs the config-doctor surface. |

Independent of ordering, the regression-prevention system (MF014 + `TestMeshCoreConnectionContract`) is the durable artifact — even if a future session lands code that violates the contract, lint + tests catch it before it merges. That's the same insurance Issue #29's prevention system provides for the Meshtastic side, and it's why Session 1's lint+test investment was disproportionate to the LoC: the rules outlive the code.
