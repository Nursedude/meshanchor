# Configuration

Every knob, and where its file lives.

## Configuration

### meshtasticd (Optional — Gateway Profile Only)

MeshAnchor writes hardware config overlays (never overwrites defaults):

```
/etc/meshtasticd/
├── config.yaml                    # Package default (DO NOT EDIT)
└── config.d/
    ├── lora-*.yaml                # Hardware config (SPI pins, module)
    └── meshanchor-overrides.yaml  # Custom overrides
```

LoRa modem presets and frequency slots are applied via the meshtastic
CLI (`--set lora.modem_preset`, `--set lora.channel_num`), not config.d.

### Reticulum

Auto-deploys a working config from `templates/reticulum.conf`:
- AutoInterface (LAN discovery)
- Meshtastic Interface on `127.0.0.1:4403` (if gateway profile)
- RNode LoRa (optional, for dedicated RNS radio)

Gateway-specific templates in `config_templates/`:
- `rns_meshtastic_gateway.conf` — full gateway with Meshtastic interface
- `rns_minimal.conf` — minimal config for MeshCore-only operation

### Ports

| Port | Service | Owner | Notes |
|------|---------|-------|-------|
| 4403 | meshtasticd TCP API | meshtasticd | Optional (gateway only), single client limit |
| 9443 | meshtasticd Web UI | meshtasticd | Optional (gateway only) |
| 1883 | MQTT broker | mosquitto | Optional, multi-consumer |
| 5000 | Map Server | **MeshAnchor** | Live NOC map + REST API |
| 5001 | WebSocket | **MeshAnchor** | Real-time message broadcast |
| 8081 | Config API | **MeshAnchor** | RESTful config management |
| 9090 | Prometheus metrics | **MeshAnchor** | Grafana-compatible JSON API |

---

