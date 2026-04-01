# Cross-Repo Topology — MeshAnchor Ecosystem

> **Purpose**: Define task delegation, shared rules, and communication patterns across the three active repos.
> **Scope**: meshanchor (NOC), meshing_around_meshanchor (Bot/Alerting), meshanchor-maps (Visualization)
> **Owner**: WH6GXZ (Nursedude)

---

## Three-Repo Architecture

```
┌─────────────────────────────────────────────────────────┐
│               MeshAnchor NOC (Core Hub)                  │
│           /opt/meshanchor · v0.5.5-beta                  │
│    Gateway · TUI · RF Tools · Service Management        │
│    99 .claude/ files · 2,954 tests · Custom linter      │
└───────────┬─────────────────────────┬───────────────────┘
            │                         │
   Plugin discovery          safe_import bridge
   (manifest.json)           (/opt/ co-location)
            │                         │
  ┌─────────▼──────────┐    ┌────────▼──────────────────┐
  │  meshanchor-maps    │    │  meshing_around_meshanchor  │
  │  /opt/meshanchor-   │    │  /opt/meshing_around_      │
  │  maps · v0.7.0     │    │  meshanchor · v0.5.0        │
  │  982 tests         │    │  743 tests                 │
  │                    │    │                             │
  │  Visualization:    │    │  Bot Alerting:              │
  │  • Leaflet.js map  │    │  • 12 alert types           │
  │  • REST API        │    │  • AES-256-CTR crypto       │
  │  • Node health     │    │  • MockMeshtasticAPI        │
  │  • Topology graphs │    │  • MQTT client              │
  │  • NOAA/EAS alerts │    │  • Rich TUI                 │
  │  • WebSocket live  │    │  • INI config               │
  └────────────────────┘    └─────────────────────────────┘
```

---

## Task Delegation Matrix

| Task Domain | Primary Repo | Why |
|-------------|-------------|-----|
| Protocol bridging (Meshtastic ↔ RNS) | **meshanchor** | Gateway architecture owns routing |
| TUI menus / NOC interface | **meshanchor** | Handler registry pattern |
| Service management (systemd) | **meshanchor** | `service_check.py` is SSOT |
| RF calculations / link budgets | **meshanchor** | `utils/rf.py` |
| Node tracking / discovery | **meshanchor** | `gateway/node_tracker.py` |
| Static coverage maps (Folium) | **meshanchor** | `utils/coverage_map.py` |
| Interactive web maps (Leaflet) | **meshanchor-maps** | Map server + collectors |
| Multi-source data aggregation | **meshanchor-maps** | BaseCollector pattern |
| Node health scoring (map layer) | **meshanchor-maps** | `utils/health_scoring.py` |
| Topology visualization | **meshanchor-maps** | SNR-colored mesh links |
| NOAA/EAS weather alert polygons | **meshanchor-maps** | `noaa_alert_collector.py` |
| REST API for node/health data | **meshanchor-maps** | `map_server.py` |
| Bot alert types (12 categories) | **meshing_around** | `core/models.py` AlertType enum |
| AES-256-CTR packet decryption | **meshing_around** | `core/mesh_crypto.py` |
| MockMeshtasticAPI (demo traffic) | **meshing_around** | `core/meshtastic_api.py` |
| MQTT client (standalone) | **meshing_around** | `core/mqtt_client.py` |
| Alert cooldown/callback logic | **meshing_around** | `core/callbacks.py` |
| INI-based alert configuration | **meshing_around** | `core/config.py` |

---

## Dependency Direction

```
meshanchor-maps ──optional──▶ MeshAnchor NOC (plugin discovery, can run standalone)
meshanchor NOC  ──safe_import──▶ meshing_around (alert types, crypto, MockAPI)
meshing_around ──independent── (no dependency on NOC or maps)
```

**Rule**: The NOC may import from meshing_around via `safe_import` (graceful degradation).
Satellite repos never depend on the NOC for core functionality.

---

## Shared Security Rules (Apply to ALL repos)

| Rule | Pattern | Fix |
|------|---------|-----|
| MF001 | `Path.home()` | Use `get_real_user_home()` / `get_real_home()` |
| MF002 | `shell=True` | Use list args: `subprocess.run(["cmd", arg])` |
| MF003 | bare `except:` | Catch specific: `except Exception as e:` |
| MF004 | missing timeout | Always: `subprocess.run(..., timeout=30)` |

---

## Integration Points

### NOC ← meshing_around (safe_import bridge)
```python
# In meshanchor NOC (src/utils/mesh_alert_engine.py):
sys.path.insert(0, "/opt/meshing_around_meshanchor")
AlertType, Alert, _HAS = safe_import('meshing_around_clients.core.models', ...)
```

### NOC ← meshanchor-maps (plugin discovery)
```json
// meshanchor-maps/manifest.json
{"id": "org.meshanchor.extension.maps", "ports": {"http": 8808, "ws": 8809}}
```

### Shared MQTT Topics
```
msh/#                    Meshtastic protobuf (all repos)
meshanchor/alerts         Alert feed (maps → NOC)
meshanchor/alerts/{sev}   Severity-filtered alerts
```

---

## Config Locations

| Repo | Config Path | Format |
|------|------------|--------|
| meshanchor | `~/.config/meshanchor/` | JSON (SettingsManager) |
| meshanchor-maps | `~/.config/meshanchor/plugins/org.meshanchor.extension.maps/` | JSON |
| meshing_around | `./mesh_client.ini` (local) | INI |

---

## Testing Standards

| Repo | Tests | Framework | CI |
|------|-------|-----------|-----|
| meshanchor | 2,954 | pytest + regression guards + custom linter | Pre-commit hooks |
| meshanchor-maps | 982 | pytest + ruff | GitHub Actions |
| meshing_around | 768 | pytest + flake8/black/isort | GitHub Actions |

---

*Made with aloha for the mesh community — WH6GXZ*
