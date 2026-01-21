# MeshForge

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="64" height="64"/>
</p>

<p align="center">
  <strong>Network Operations Center for the Decentralized Mesh Future</strong>
</p>

<p align="center">
  <a href="https://github.com/Nursedude/meshforge"><img src="https://img.shields.io/badge/version-0.4.7--beta-blue.svg" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-green.svg" alt="License"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.9+-yellow.svg" alt="Python"></a>
</p>

<p align="center">
  <a href="https://nursedude.substack.com">Follow on Substack</a> |
  <a href="https://github.com/Nursedude/meshforge/issues">Report Issues</a> |
  <a href="#contributing">Contribute</a>
</p>

## The Vision

**MeshForge is a Network Operations Center (NOC) that unifies fragmented mesh ecosystems into a single, coherent operating environment.**

The mesh networking landscape is fractured. Meshtastic, Reticulum, MeshCore, AREDN - each powerful in isolation, but unable to interoperate. Emergency responders can't bridge networks. Communities build redundant infrastructure. The promise of resilient, decentralized communication remains unfulfilled.

MeshForge changes this.

```
                         MESHFORGE NOC
    ╔════════════════════════════════════════════════════╗
    ║                                                    ║
    ║   Meshtastic  ──►  ╔══════════════╗  ◄──  RNS     ║
    ║                    ║   GATEWAY    ║               ║
    ║   MeshCore    ──►  ║    BRIDGE    ║  ◄──  AREDN   ║
    ║                    ╚══════════════╝               ║
    ║                          │                        ║
    ║              ╔═══════════╧═══════════╗            ║
    ║              ║  Unified Node View    ║            ║
    ║              ║  AI Diagnostics       ║            ║
    ║              ║  Coverage Analysis    ║            ║
    ║              ║  Health Monitoring    ║            ║
    ║              ╚═══════════════════════╝            ║
    ╚════════════════════════════════════════════════════╝
```

**First open-source tool designed to bridge incompatible mesh protocols.**

## Current Capabilities (v0.4.7-beta)

| Feature | Status | Description |
|---------|--------|-------------|
| **Meshtastic Integration** | Production | Full SPI/USB/TCP support via meshtasticd |
| **Reticulum Bridge** | Production | RNS transport with LXMF messaging |
| **Gateway Bridge** | Production | Bidirectional Meshtastic-RNS message routing |
| **AI Diagnostics** | Production | Natural language troubleshooting |
| **Coverage Maps** | Production | SNR-based link quality visualization |
| **Multi-Interface** | Production | GTK, TUI, Web, CLI, Standalone |
| **Health Monitoring** | Production | Service orchestration with auto-restart |

## Roadmap

MeshForge development follows a milestone-based approach. We ship when quality gates pass, not arbitrary dates.

### Phase 1: Foundation (Current)
**Status: Production Beta**

Core NOC functionality for Meshtastic and Reticulum networks.

```
[####################] Meshtastic Integration
[####################] RNS/Reticulum Bridge
[####################] Gateway Message Routing
[####################] AI Diagnostics Engine
[##################  ] Rich CLI Completion
[################    ] GTK4 Desktop Polish
```

### Phase 2: Protocol Expansion
**Status: Research & Planning**

Integrate additional mesh protocols to expand interoperability.

| Integration | Protocol | Priority | Notes |
|-------------|----------|----------|-------|
| **MeshCore** | Reticulum-based | High | Emerging protocol, RNS-compatible transport |
| **AREDN** | TCP/IP over ham | Medium | Amateur Radio Emergency Data Network |
| **QMesh** | Experimental | Low | Research phase |

**MeshCore Integration:**
MeshCore extends Reticulum with improved routing. Since MeshForge already bridges RNS, MeshCore nodes gain Meshtastic interop through the existing gateway.

**AREDN Integration:**
AREDN provides high-bandwidth TCP/IP mesh over amateur frequencies. MeshForge will offer AREDN node discovery, status monitoring, and message relay for text-based communication.

### Phase 3: Advanced NOC
**Status: Design Phase**

Enterprise-grade network operations capabilities.

| Feature | Description |
|---------|-------------|
| **Wireshark Integration** | Packet capture and protocol analysis |
| **Network Topology Discovery** | Automatic mesh mapping |
| **Predictive Analytics** | Link failure prediction using ML |
| **Multi-Gateway Coordination** | Distributed NOC architecture |
| **Historical Analysis** | Long-term network health trends |

### Phase 4: Ecosystem
**Status: Future**

| Feature | Description |
|---------|-------------|
| **Plugin Architecture** | Third-party protocol adapters |
| **Federation** | NOC-to-NOC communication |
| **Mobile Companion** | iOS/Android status app |

## Quick Start

```bash
# Clone the repository
git clone https://github.com/Nursedude/meshforge.git
cd meshforge

# Option 1: Full NOC stack (recommended)
sudo bash scripts/install_noc.sh

# Option 2: Minimal install
pip3 install rich textual flask --break-system-packages
sudo python3 src/launcher.py

# Option 3: Zero-dependency standalone
python3 src/standalone.py
```

**First Launch:**
```
MeshForge v0.4.7-beta

Services:
  [OK] meshtasticd: running (port 4403)
  [OK] Hardware: SX1262 detected
  [--] rnsd: not configured (optional)

Network:
  [OK] Nodes visible: 3

Ready! [Continue] [Configure] [Troubleshoot]
```

## Architecture

MeshForge owns the complete stack from radio hardware to user interface:

```
╔══════════════════════════════════════════════════════════╗
║                     USER INTERFACES                       ║
║    GTK Desktop  |  Rich TUI  |  Web UI  |  Standalone    ║
╠══════════════════════════════════════════════════════════╣
║                     CORE SERVICES                         ║
║  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  ║
║  │ meshtasticd  │  │    rnsd      │  │  Orchestrator  │  ║
║  │  (radio)     │  │    (RNS)     │  │   (manager)    │  ║
║  └──────────────┘  └──────────────┘  └────────────────┘  ║
╠══════════════════════════════════════════════════════════╣
║                    GATEWAY BRIDGE                         ║
║         Meshtastic <──> Protocol Translation <──> RNS    ║
╠══════════════════════════════════════════════════════════╣
║                    INTELLIGENCE                           ║
║    Diagnostics Engine  |  Knowledge Base  |  Claude AI   ║
╚══════════════════════════════════════════════════════════╝
```

**Supported Hardware:**

| Category | Devices |
|----------|---------|
| **SPI HATs** | Meshtoad, MeshAdv-Pi-Hat, RAK WisLink, Waveshare SX126x |
| **USB Radios** | T-Beam, Heltec V3/V4, RAK4631, MeshStick, T-Deck |
| **Platforms** | Raspberry Pi 5/4/3/Zero 2W, Debian/Ubuntu x86_64 |

## Interface Options

| Interface | Command | Use Case |
|-----------|---------|----------|
| **Auto-detect** | `sudo python3 src/launcher.py` | Recommended |
| **Rich TUI** | `sudo python3 src/launcher_tui/main.py` | SSH, headless |
| **Web UI** | `sudo python3 src/main_web.py` | Browser (port 8880) |
| **GTK Desktop** | `sudo python3 src/main_gtk.py` | Full graphical |
| **Standalone** | `python3 src/standalone.py` | Zero dependencies |

## AI Diagnostics

MeshForge includes intelligent troubleshooting that works offline or with Claude AI:

```
SYMPTOM: Connection refused to meshtasticd

ANALYSIS:
  [FAIL] Port 4403 not responding
  [FAIL] systemctl shows inactive

LIKELY CAUSE: Service not running (85% confidence)

SUGGESTED FIXES:
  1. sudo systemctl start meshtasticd
  2. Check logs: journalctl -u meshtasticd -n 50
  3. Verify USB/SPI device connected
```

| Mode | Capability |
|------|------------|
| **Standalone** | Rule-based diagnostics with knowledge base (offline) |
| **PRO** | Claude AI for natural language queries |

## Who Uses MeshForge?

| User | Use Case |
|------|----------|
| **HAM Operators** | Building resilient off-grid networks |
| **Emergency Services** | ARES/RACES mesh interoperability |
| **Off-Grid Communities** | Connecting isolated mesh systems |
| **Network Engineers** | LoRa protocol research and bridging |
| **Researchers** | Mesh network behavior analysis |

## Contributing

MeshForge is built by the mesh community, for the mesh community.

**Development Setup:**
```bash
# Install pre-commit hooks
cp scripts/hooks/pre-commit .git/hooks/
chmod +x .git/hooks/pre-commit

# Run tests
python3 -m pytest tests/ -v

# Run linter
python3 scripts/lint.py --all
```

**Key Guidelines:**
- Use `get_real_user_home()` instead of `Path.home()` (MF001)
- No `shell=True` in subprocess calls (MF002)
- Explicit exception handling (MF003)
- Include timeouts on subprocess calls (MF004)

See `CLAUDE.md` for complete development patterns.

**Get Involved:**
- Follow development on [Substack](https://nursedude.substack.com)
- Report issues on [GitHub](https://github.com/Nursedude/meshforge/issues)
- Join [Discussions](https://github.com/Nursedude/meshforge/discussions)

## Resources

| Resource | Link |
|----------|------|
| Meshtastic | [meshtastic.org](https://meshtastic.org/docs/) |
| Reticulum | [reticulum.network](https://reticulum.network/) |
| MeshCore | [meshcore.co](https://meshcore.co/) |
| AREDN | [arednmesh.org](https://www.arednmesh.org/) |
| NomadNet | [github.com/markqvist/NomadNet](https://github.com/markqvist/NomadNet) |

## License

GPL-3.0 - See [LICENSE](LICENSE)

---

<p align="center">
  <img src="assets/shaka-simple.svg" alt="Shaka" width="32" height="32"/><br>
  <strong>MeshForge</strong><br>
  Network Operations Center for the Decentralized Mesh Future<br>
  <em>Made with aloha for the mesh community</em><br>
  WH6GXZ | Hawaii
</p>
