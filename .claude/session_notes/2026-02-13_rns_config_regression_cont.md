# Session: RNS Config Regression — Root Cause Analysis & Fix
**Date:** 2026-02-13
**Branch:** `claude/fix-rns-config-regression-P7ikY`
**Prior session:** `2026-02-13_rns_config_autofix_regression.md`

## Context

Continuation of RNS config auto-fix regression work. Prior session made 4 fixes
but the underlying systemic issue persisted. This session traced the full
progression from clean install to broken state and identified 5 design gaps.

## The Full Failure Progression (CRITICAL — read before next session)

This is the sequence that breaks RNS and NomadNet:

1. **Clean template deployed** — AutoInterface only, rnsd works
2. **User configures interfaces** — adds Meshtastic Gateway (tcp_port=127.0.0.1:4403),
   HawaiiNet TCP client (192.168.86.38:4242)
3. **meshtasticd stops** (or isn't running, or host unreachable)
4. **rnsd hangs** — interface init runs BEFORE binding port 37428.
   Meshtastic_Interface blocks trying to connect to dead meshtasticd.
   rnsd appears "active" to systemd but never binds shared instance port.
5. **RNS tools fail** — "no shared instance" because 37428 isn't listening
6. **Auto-fix triggers** — clears auth tokens, restarts rnsd → same hang
7. **NomadNet fails** — can't connect to shared instance either
8. **Cascading breakage** — repeated auto-fix cycles accumulate stale state

## Root Cause: 5 Design Gaps

### Gap 1: Auth token clearing incomplete
Fixed: now clears from all 4 locations (etc, root, user, user-XDG)

### Gap 2: NomadNet config path not explicit
Fixed: `_get_rns_config_for_user()` always returns explicit path, never None

### Gap 3: `chmod -R 755` destroyed world-writable storage
Fixed: targeted `chmod 777` on storage only, auto-fix instead of fallback

### Gap 4: Storage file permissions not fixed before NomadNet launch
Fixed: `_get_rns_config_for_user()` calls `_fix_storage_file_permissions()`

### Gap 5: Blocking interfaces prevent rnsd from starting (NEW)
Fixed: `_find_blocking_interfaces()` detects:
- Meshtastic_Interface with meshtasticd not running
- TCPClientInterface with unreachable host
Runs in diagnostics AND before auto-fix starts rnsd.

## Current System State (User's Pi)

Config at `/etc/reticulum/config` has 3 active interfaces:
- `[[Default Interface]]` — AutoInterface (OK)
- `[[HawaiiNet RNS]]` — TCPClientInterface to 192.168.86.38:4242 (may be unreachable)
- `[[Meshtastic Gateway]]` — Meshtastic_Interface to 127.0.0.1:4403 (needs meshtasticd)

Plugin exists: `/etc/reticulum/interfaces/Meshtastic_Interface.py` (18KB)
Entropy: 256 (rng-tools-debian installed and running)
rnsd: running but NOT listening on 37428 (stuck in interface init)

## What User Needs to Do

After pulling these fixes:
```bash
# First: start meshtasticd so the Meshtastic interface can connect
sudo systemctl start meshtasticd

# Then restart rnsd (it will now detect blocking interfaces)
sudo systemctl restart rnsd

# Verify
rnstatus
```

If meshtasticd isn't needed yet, disable the interface:
```bash
# Edit config: change enabled = yes → enabled = no under [[Meshtastic Gateway]]
sudo nano /etc/reticulum/config
sudo systemctl restart rnsd
```

## Commits (3 total this session)
1. `fix: detect correct entropy service name for Debian/Pi OS`
2. `fix: eliminate RNS config drift between rnsd and NomadNet`
3. `fix: detect blocking RNS interfaces that prevent rnsd from starting`

## Tests
- 4021 passed, 19 skipped — all clean after each commit

## Next Session TODO
- [ ] Verify fixes work on user's Pi after pull
- [ ] Consider: auto-disable blocking interfaces when starting rnsd (with user consent)
- [ ] Consider: add "Interface Dependencies" section to RNS Diagnostics menu
- [ ] Consider: rnsd service file fix — `StartLimitIntervalSec` in wrong section
      (should be in [Unit], not [Service] — systemd warns about this)
- [ ] NomadNet may need its own pre-flight (check rnsd is listening before launch)

## Session Status
- Stopping due to session entropy (crossing live system debug + code fixes)
- All code changes are committed, tested, and pushed
- Session notes complete for handoff
