# Changelog

All notable changes to **MeshAnchor** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Rewritten 2026-07-07 (audit): this file previously carried a **verbatim copy of
> MeshForge's changelog** (top entry `0.5.4-beta`), which contradicted MeshAnchor's
> own version SSOT `0.1.0-alpha` (`src/__version__.py`, released 2026-04-01).
> MeshAnchor was **extracted from MeshForge** and its version reset to
> `0.1.0-alpha`; the pre-extraction lineage (≤ `0.5.5-beta`) is **shared with
> MeshForge** and lives in MeshForge's `CHANGELOG.md` and in this repo's
> `src/__version__.py` `VERSION_HISTORY` (kept for provenance). Only
> MeshAnchor-specific history is recorded here.

## [Unreleased]

## [0.4.0-beta] - 2026-11-19

182 commits since 0.3.0-beta (to `1ccf730f`). The theme: **a message either arrives or the
operator is told why** — on every leg, in numbers, without leaving the app.
Everything below is deployed on the canonical NOC; "field-validated" is claimed
only where it says so.

### Added
- **Delivery truth per leg (BIC-2).** Delivery is counted per leg (MeshCore,
  Meshtastic, RNS) over 15 min / 1 h / 24 h. The persistent queue and the direct
  sends name the leg a message was for; a refused RNS ingress is recorded as
  *refused*, not silence; every Mesh→RNS message ends *delivered* or *dropped*.
- **RNS outage behaviour.** When RNS is down, Mesh→RNS messages wait (up to
  24 h) instead of being counted as failures or lost; a partial fan-out retries
  only the legs that failed.
- **CS-1 observer (OFF by default).** The MeshCore companion's received on-air
  packets (RX_LOG_DATA) go to the local MQTT broker in the meshcoretomqtt
  packet shape, for a CoreScope analyzer. MeshCore > Daemon Control shows its
  status as one line — `[OK]` / `[OFF]` / `[FAULT]` / `[UNKNOWN]` — and a
  detail view. It reads UNKNOWN (and says why) whenever the daemon, the bridge
  or the status file can't be trusted; `ok` means *queued*, not *stored*.
- **Companion clock.** The MeshCore radio's clock is measured and shown; it is
  synced only if you opt in.
- **Known Blind Spots** page: what the fleet cannot see, with since-dates.
- **MeshChatX** listen address is a setting (`MESHCHATX_HOST`), not a hand edit.

### Fixed
- **MeshCore reconnect:** a lost companion session is torn down one way, so the
  handler reconnects; the advert heartbeat keeps its cadence across reconnects.
- **Bridge correctness:** a private message never leaves on a MeshCore channel;
  a Meshtastic DM is never fanned out to the LXMF broadcast list; an oversize
  Meshtastic send is refused, never reported as sent; a DM gets a DM-sized byte
  budget; a requeued channel message airs on its own slot or not at all; a
  message lost to a full internal queue is counted and said.
- **Fresh MeshCore-primary install (#151):** six silent blockers fixed;
  meshtasticd is opt-in on a fresh box and never demoted on a re-run.
- **systemd units:** one rule for every user-unit template — an update never
  copies a placeholder over an installed unit.
- **Bot replies** no longer miss MeshCore for ~10 min after a restart, and
  are no longer answered twice.
- **MQTT settings:** the TUI edits what you meant (intent), keeps a saved TLS
  choice, and one writer at a time.
- **Web:** the operator login no longer expires 30 days after pairing.

### Security
- The rnsd storage permission walk is symlink-safe, leaves hardlinks alone and
  never widens a root-owned key.
- RNS→MeshCore ingress allowlist (open until you declare a list).
- Grafana removed (dashboards, TUI submenu, exporter endpoints).
- RNS fork pin `rns 1.3.9+mf.0` / `lxmf 1.0.1+mf.14` (includes the rnsh
  auth-bypass fix).

### Known limits
- Not yet validated: coverage maps with live GPS, sustained concurrent 3-way
  traffic, the CS-1 observer switched ON (it needs CoreScope units first).

## [0.3.0-beta] - 2026-10-15

### Added
- **NOC read grant**: MeshAnchor can be a CENTER — the same grant CLI and spool writer as MeshForge (one file each), a target's host key pinned from fleet.json peers[].ssh.expect_hostkey (an alias two peers claim pins nothing), and a fleet-truth spool leg that builds watchdog / mini / radio blocks for a map-less box from its exported raw state. TUI: Dashboard > NOC Read Grants.

### Security
- an unverified LXMF sender is never treated as an identity (no node, re-emit hook only on a validated signature, RF tag [RNS:?xxxx]); the LXMF broadcast bridge REFUSES an unreadable identity instead of starting with none (a corrupt file started it with identity None); rnsd's private key is never widened by the storage permission walk; web fixes shared with MeshForge (GET-with-body radio write, concurrent pairing burn).

### Fixed
- the mini status block now reads not-ok on a missing or future tick timestamp (this repo's private copy had drifted; now the shared utils.status_blocks).

## [0.2.0-beta] - 2026-08-09

**alpha → beta.** The `0.1.0-alpha` tag was set at extraction (2026-04-01) and
never revisited — 130 days and 616 commits. It described the age of the repo,
not the maturity of the software: 210 test files, 6,244 passing tests, 438
source modules, and continuous field deployment on the canonical NOC since
2026-05-02. For calibration, MeshForge first called itself beta with 1 test file
and 297 commits. The tag was also working against its own goal — the README asks
for field testers, and "alpha" conventionally reads as "do not rely on this".

**Unchanged by this bump:** coverage maps with live GPS position data, and full
3-way (MeshCore ↔ Meshtastic ↔ RNS) concurrent traffic remain unvalidated.
Independent testing on other MeshCore hardware is still the most valuable
contribution.

Work landed since extraction. This is a **representative** list — `git log` is
the authoritative record:

### Added
- Gateway: `content_id` carried through Canonical→Bridged (dedup/identity arc, MeshAnchor twin of the MeshForge work) — STEP 2a/2b-ii/3.

### Fixed / Security
- Maps QA parity: ported MeshForge maps-QA HTTP hardening (CORS tail-anchor + LAN-trust gate on fleet endpoints), DoS/clamp findings, collector-layer + fleet-observability + prometheus review fixes; moved the RNS nodes cache off world-writable `/tmp`.
- `honest_status`: watchdog leg reads `/fleet/blackouts` (not a phantom `watchdog.json`).
- TUI: duplicate menu tags refuse-loud at registration + orphan-handler guard.

### Notes
- RNS-reliability substrate is **led by MeshForge** and ported here (chokepoint, fork pin, wedge probes) — see MeshForge `CLAUDE.md` and `scripts/parity_check.py`.

## [0.1.0-alpha] - 2026-04-01

### Added
- **Extracted from MeshForge main** (source commit `7e4fa02`; extraction commit `e9a52f3e`) as a **MeshCore-primary NOC** — same architecture, MeshCore as the home radio (MeshForge stays Meshtastic-primary).
- Version reset to `0.1.0-alpha` under the MeshCore-first charter.
- Inherited the shared NOC foundation: gateway/bridge, TUI, RF tools, diagnostics, maps, monitoring.

---

_Pre-extraction lineage (MeshChat removal at 0.5.5-beta, MeshCore bridge at
0.6.0-alpha, the MQTT-transport gateway rewrite at 0.5.4-beta, and earlier) is
shared with MeshForge and is not duplicated here — see MeshForge's `CHANGELOG.md`._
