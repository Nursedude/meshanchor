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
