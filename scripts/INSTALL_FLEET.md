# Fleet Observability — Production Install (S5b)

Operator-driven deploy of the long-haul observability platform shipped
in S5a (PR #108). The Python code is already on `meshanchor-server`;
this is the systemd plumbing that turns "code that exists" into
"process that runs forever."

## Files in this drop

| File | Goes to | Purpose |
|---|---|---|
| `meshanchor-fleet-collector.service` | `/etc/systemd/system/` | Always-on writer. Polls `/fleet/*` every 60s, persists to `fleet_history.db`. |
| `meshanchor-fleet-watchdog.service` | `/etc/systemd/system/` | Separate process. Detects silence (no_data / http_dead / frozen) and opens `blackout_events` rows. |
| `meshanchor-fleet.sudoers` | `/etc/sudoers.d/011_meshanchor-fleet` | NOPASSWD `systemctl` lifecycle for the new units. Without this, the TUI can't restart them. |

## Install (5 commands)

Run these on `meshanchor-server` (the canonical NOC):

```bash
# 1. Drop sudoers (validated by visudo before activation)
sudo install -m 0440 -o root -g root \
  /opt/meshanchor/scripts/meshanchor-fleet.sudoers \
  /etc/sudoers.d/011_meshanchor-fleet
sudo visudo -c   # must report "parsed OK"

# 2. Drop the unit files
sudo install -m 0644 -o root -g root \
  /opt/meshanchor/scripts/meshanchor-fleet-collector.service \
  /etc/systemd/system/

sudo install -m 0644 -o root -g root \
  /opt/meshanchor/scripts/meshanchor-fleet-watchdog.service \
  /etc/systemd/system/

# 3. Reload systemd so it sees the new units
sudo systemctl daemon-reload

# 4. Enable + start (collector first, watchdog second)
sudo systemctl enable --now meshanchor-fleet-collector.service
sudo systemctl enable --now meshanchor-fleet-watchdog.service

# 5. Verify both are running
systemctl status meshanchor-fleet-collector meshanchor-fleet-watchdog --no-pager
```

You should see both `Active: active (running)`. The collector logs a
cycle every 60s; the watchdog logs every 30s.

## Verify the data

```bash
# Heartbeats accumulating
journalctl -u meshanchor-fleet-collector -n 5 --no-pager

# Watchdog ticking, no blackouts
journalctl -u meshanchor-fleet-watchdog -n 5 --no-pager

# Dashboard endpoint
curl -s http://127.0.0.1:5000/fleet/blackouts | python3 -m json.tool
# → {"active": [], "history": [...]}

# Sparklines now have a clean source — open the dashboard tab and
# the boundary trend column should fill in within ~30s.
```

## Smoke the BLACKOUT detection (recommended before declaring done)

This is the operationally important test. **Schedule it for a quiet
window** since you'll deliberately stop the daemon for a couple minutes.

The expected detection here is **`daemon_dead`** (not `frozen`). The
first BLACKOUT smoke on 2026-05-09 surfaced a gap: `slo.uptime_s` is
the map's uptime, so the `frozen` rule doesn't fire when the map is
healthy but the daemon is down. The post-smoke fix added a
`daemon_dead` kind that polls `meshanchor-daemon` directly via
`check_service` with 2-cycle hysteresis (≥ 60s before fire).

```bash
# 1. Confirm clean state
curl -s 'http://127.0.0.1:5000/fleet/blackouts?active_only=1' \
    | python3 -m json.tool
# → {"active": []}

# 2. Stop the daemon. The map service stays up — this is exactly
#    the "healthy front door over a dead back end" failure mode.
sudo systemctl stop meshanchor-daemon.service

# 3. Wait ≥ 70s for the 2-cycle hysteresis to fire (watchdog runs
#    every 30s; first inactive read sets streak=1, second fires at
#    streak=2). 90s gives a comfortable margin.
sleep 90
curl -s 'http://127.0.0.1:5000/fleet/blackouts?active_only=1' \
    | python3 -m json.tool
# → should show an active "daemon_dead" blackout

# 4. The dashboard banner should now be visible. Refresh the
#    browser tab if it's been idle.

# 5. Restore service
sudo systemctl start meshanchor-daemon.service

# 6. Within ~1 cycle, the streak resets and reconcile closes the row.
sleep 45
curl -s 'http://127.0.0.1:5000/fleet/blackouts?active_only=1' \
    | python3 -m json.tool
# → {"active": []} again

# 7. The history endpoint shows the closed event
curl -s http://127.0.0.1:5000/fleet/blackouts | python3 -m json.tool
# → {"active": [], "history": [{kind: "daemon_dead", ts_started: ..., ts_ended: ...}]}
```

If steps 3 & 4 do NOT fire a `daemon_dead` BLACKOUT, the silence
detection is broken and that's a P0 — file an issue with the
journalctl output of both units during the stop window.

## Uninstall

```bash
sudo systemctl disable --now meshanchor-fleet-watchdog.service
sudo systemctl disable --now meshanchor-fleet-collector.service
sudo rm /etc/systemd/system/meshanchor-fleet-collector.service
sudo rm /etc/systemd/system/meshanchor-fleet-watchdog.service
sudo rm /etc/sudoers.d/011_meshanchor-fleet
sudo systemctl daemon-reload
```

The history DB at `~/.local/share/meshanchor/fleet_history.db` is
left intact — it's data, not config. Remove it manually if you want
a clean slate.

## After 24h of clean operation

Once the collector has run for >24h with no `Restart=always` events
visible in journald, the bootstrap-record path baked into
`_serve_fleet_slo` (S4 carry-over) can be removed. Open a small PR
that strips the bootstrap block from `src/utils/_map_fleet.py` —
look for the comment `# S4 bootstrap (remove when S5's collector
ships)`. Tests already cover the post-removal shape; lint will catch
nothing because the bootstrap is just an opportunistic write.

## Tuning notes

- **Collector --interval**: default 60s. Raising it loses sparkline
  fidelity; lowering it costs more systemd shell-outs (the dashboard
  cache helps, but the collector always misses the 6s TTL). 60s is
  the sweet spot.
- **Watchdog --stale**: default 120s = 2× collector cadence. Raising
  it delays incident detection; lowering it false-fires on a single
  collector cycle slip.
- **Watchdog --frozen-window**: default 3 heartbeats = ~3 min of
  "no advancement" before flagging frozen. Per S5a's smoke notes,
  the rule is "uptime stuck at IDENTICAL value across the window" —
  a daemon restart (uptime drops to 0) is intentionally NOT frozen
  because the HTTP-dead check covers the actual outage gap.
