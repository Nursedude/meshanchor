# MeshAnchor Deployment Profiles

> **Phase 7 (2026-05-04)**: Authoritative reference for the five deployment profiles MeshAnchor ships with. Profile definitions live in `src/utils/deployment_profiles.py`; this document explains *why* each default is what it is and *which* profile a user should pick.

---

## TL;DR — Which profile should I pick?

```
Do you have a MeshCore radio attached?
├── YES
│   ├── Want coverage maps + topology view?  →  RADIO_MAPS  (most common Pi NOC)
│   └── No, just the radio?                   →  MESHCORE    (most conservative)
│
└── NO
    ├── Want to bridge MeshCore ↔ Meshtastic/RNS via this host?  →  GATEWAY
    ├── Just MQTT packet analysis (no radio at all)?              →  MONITOR
    └── Want everything (RNS messaging + Meshtastic + maps)?      →  FULL
```

Auto-detection (`launcher.py` with no `--profile` flag) picks for you based on what's running:

1. **FULL** if both `rnsd` and `mosquitto` are running.
2. **GATEWAY** if either `meshtasticd` or `rnsd` is available.
3. **RADIO_MAPS** if `folium` is installed.
4. **MONITOR** if `paho` is installed *and* `meshcore` is NOT.
5. **MESHCORE** otherwise (the safe default).

Override the auto-detect with `python3 src/launcher.py --profile <name>`. The selected profile is saved to `~/.config/meshanchor/deployment.json`.

---

## Profile Matrix

The seven feature flags below gate which menu rows are visible in the TUI. A flag set to `False` means the corresponding rows are hidden under that profile. Cross-cutting handlers (HAM, AREDN, Favorites, Service Menu) are always-visible by Phase 3 policy and are not in this matrix.

| Flag        | MESHCORE | RADIO_MAPS | MONITOR | GATEWAY | FULL |
|-------------|:--------:|:----------:|:-------:|:-------:|:----:|
| `meshcore`  |    ✅    |     ✅     |    ❌   |    ✅   |  ✅  |
| `meshtastic`|    ❌    |     ❌     |    ❌   |    ✅   |  ✅  |
| `rns`       |    ❌    |     ❌     |    ❌   |    ✅   |  ✅  |
| `gateway`   |    ❌    |     ❌     |    ❌   |    ✅   |  ✅  |
| `mqtt`      |    ❌    |     ❌     |    ✅   |    ✅   |  ✅  |
| `maps`      |    ❌    |     ✅     |    ❌   |    ✅   |  ✅  |
| `tactical`  |    ❌    |     ❌     |    ❌   |    ❌   |  ✅  |

### Required vs optional services

| Profile     | required_services         | optional_services                      |
|-------------|---------------------------|----------------------------------------|
| MESHCORE    | (none)                    | (none)                                 |
| RADIO_MAPS  | (none)                    | (none)                                 |
| MONITOR     | (none)                    | `mosquitto`                            |
| GATEWAY     | (none)                    | `meshtasticd`, `rnsd`, `mosquitto`     |
| FULL        | `rnsd`, `mosquitto`       | `meshtasticd`                          |

The startup health check (Phase 5) classifies services as **required**, **optional**, or **not_applicable** based on the active profile — the lists above feed directly into that classification.

### Required Python packages

Every profile requires `rich`, `yaml`, `requests`. Profile-specific additions:

| Profile     | additional required packages                                     |
|-------------|------------------------------------------------------------------|
| MESHCORE    | (none beyond the common three)                                   |
| RADIO_MAPS  | `folium`                                                         |
| MONITOR     | `paho`                                                           |
| GATEWAY     | `paho`                                                           |
| FULL        | `RNS`, `LXMF`, `paho`, `folium`, `websockets`, `psutil`, `distro`|

---

## Profile-by-Profile

### MESHCORE — The default

> "MeshCore companion radio — primary MeshAnchor profile."

The most conservative default. Surfaces the MeshCore submenu and nothing else from the radio side. Optional Gateways (Meshtastic / RNS / Gateway Bridge / MQTT) are hidden because the user explicitly hasn't opted into them. Maps and Tactical are also off — pick `RADIO_MAPS` if you want maps.

**Pick this when**: you have a MeshCore radio and want a clean, focused TUI without the cross-protocol complexity.

**Install**:

```bash
pip3 install -r requirements.txt
python3 src/launcher.py --profile meshcore
```

### RADIO_MAPS — The common Pi NOC

> "MeshCore radio with coverage mapping."

MeshCore + Maps & Viz section. This is the typical "Raspberry Pi as a stationary NOC" deployment — you have a fixed-location MeshCore node and you want to see coverage maps, topology, and the meshforge-maps integration (Phase 6 / 6.1 / 6.3).

**Pick this when**: you have MeshCore + folium + want coverage visualisation.

**Install**:

```bash
pip3 install -r requirements.txt folium
python3 src/launcher.py --profile radio_maps
```

### MONITOR — MQTT packet analysis only

> "MQTT packet analysis and traffic inspection (no radio required)."

For users who want to observe the mesh via MQTT without operating any radio. Notably, **`meshcore` defaults to False under MONITOR** — the whole point of this profile is "I don't have a radio attached". A user with a MeshCore radio should pick MESHCORE or RADIO_MAPS instead.

**Pick this when**: you have a mosquitto broker on your network with mesh traffic flowing through it, and you want to inspect packets without operating a radio yourself.

**Install**:

```bash
pip3 install -r requirements.txt paho-mqtt
python3 src/launcher.py --profile monitor
```

### GATEWAY — MeshCore ↔ Meshtastic/RNS bridge

> "MeshCore <> Meshtastic/RNS bridge with message routing."

Surfaces all the cross-protocol bridging features: Meshtastic radio config, RNS / Reticulum, gateway message routing, MQTT broker, plus maps for topology visualisation. **`tactical` defaults to False** under GATEWAY (corrected in Phase 7) — bridge users don't necessarily want military-coded SITREP/zones/QR/ATAK menus by default; flip it on via Settings if you want them.

`required_services` is empty because the bridge can be MeshCore↔Meshtastic OR MeshCore↔RNS — neither service is singularly required. Health detection still warns when none of the three (`meshtasticd`, `rnsd`, `mosquitto`) are running.

**Pick this when**: you're operating a host that needs to bridge MeshCore traffic to Meshtastic and/or RNS for cross-protocol messaging.

**Install**:

```bash
pip3 install -r requirements.txt rns lxmf paho-mqtt
sudo apt install meshtasticd rnsd mosquitto    # whichever radios you have
python3 src/launcher.py --profile gateway
```

### FULL — Everything enabled

> "All features enabled — MeshCore + Meshtastic + RNS."

The kitchen-sink profile. Every flag on, every section visible, every feature surfaced. Tactical Ops menu is included. Used for the dev box, comprehensive demos, and users who genuinely run a full multi-protocol stack.

`required_services` includes `rnsd` and `mosquitto` — without these, the FULL profile reports degraded health on startup. **`meshtasticd` is `optional`, not `required`**, even under FULL: this is a deliberate Phase 5 decision. A user may run FULL with only RNS+MQTT (no Meshtastic radio attached) without the health check screaming at them.

**Pick this when**: you want every feature available, you have all three services running, and you're comfortable navigating a deeper menu hierarchy.

**Install**:

```bash
pip3 install -r requirements.txt rns lxmf paho-mqtt folium websockets psutil distro
sudo apt install meshtasticd rnsd mosquitto
python3 src/launcher.py --profile full
```

---

## Intentionally counterintuitive choices

These look wrong on first read — they're not. Phase 7 documented them so future audits don't "fix" them.

1. **MONITOR has `meshcore: False`**. Whole point of the profile is "no radio required". A user with MeshCore hardware should pick MESHCORE/RADIO_MAPS, not MONITOR. If you find yourself thinking "but what if a MONITOR user *also* has MeshCore?" — they should be on RADIO_MAPS or MESHCORE. MONITOR exists for the dedicated observer use case.

2. **FULL has `meshtasticd` only in `optional_services`, not `required`**. Phase 5 made services profile-aware. A user running FULL with RNS+MQTT but no Meshtastic radio is a legitimate deployment (e.g. an off-grid LXMF + MeshCore install). Marking meshtasticd as required would re-introduce the original Phase 5 user-visible bug.

3. **GATEWAY has `required_services=[]`**. The bridge can be MeshCore↔Meshtastic OR MeshCore↔RNS. Neither service is singularly required at the profile level; health detection still warns when none of the three are running.

4. **GATEWAY now has `tactical: False`** (Phase 7 correction; was True). Tactical Ops (SITREP, zones, QR, ATAK) is unrelated to bridging. Flip on via Settings if you want it.

5. **list_profiles() returns MESHCORE first, not RADIO_MAPS** (Phase 7 correction; was RADIO_MAPS first). The Settings TUI uses this order verbatim — the recommended default sits at the top.

---

## How feature flags reach the TUI

```
deployment.json → load_or_detect_profile() → profile.feature_flags
                                                    │
                                                    ▼
                          MeshAnchorLauncher._feature_flags  (main.py)
                                                    │
                            ┌───────────────────────┼───────────────┐
                            ▼                       ▼               ▼
            _feature_enabled('maps')      ctx.feature_flags    handler_registry
                  (top-level                       │           dispatch (per-row
                  menu gating)                     ▼            flag check)
                                          handler menu_items
                                          rows with flag=...
```

- **Top-level menu items** (Maps & Viz, Tactical Ops) gate via `MeshAnchorLauncher._feature_enabled()` directly.
- **Submenu rows** gate via the `(tag, label, flag)` tuple in each handler's `menu_items()`. The registry's `dispatch()` filter drops rows whose flag isn't enabled in `ctx.feature_flags`.
- **Cross-cutting handlers** (HAM, AREDN, Favorites, Service Menu) have `flag=None` on every row — always visible regardless of profile.

---

## Switching profiles at runtime

```bash
# CLI override (per-launch):
python3 src/launcher.py --profile gateway

# TUI: Configuration → Settings → Deployment Profile → pick

# Force a re-detect (clears the saved profile):
rm ~/.config/meshanchor/deployment.json
python3 src/launcher.py    # auto-detects
```

After switching, the Phase 5.5 process-lifetime cache is auto-invalidated by `save_profile()`, so the next service event sees the new profile without a daemon restart.

---

## Related docs

- `CLAUDE.md` — Quick Context + Deployment Profiles section (this doc is referenced from there).
- `docs/USAGE.md` — User-facing CLI usage including the `--profile` flag.
- `.claude/foundations/persistent_issues.md` — Issue #3 (services advisory vs blocking) explains why `required_services` lists are short.
- `.claude/plans/tui_rework_tracker.md` — Phase 5 (health flip), Phase 5.5 (profile-aware services), Phase 7 (this doc + defaults audit) decisions.
