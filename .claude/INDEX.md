# MeshForge Documentation Index

Quick navigation for AI assistants. Load only what you need.

## Priority Order (Read First)

1. **CLAUDE.md** (root) - Primary instructions, ALWAYS read
2. **foundations/persistent_issues.md** - Known bugs & fixes
3. **foundations/domain_architecture.md** - Core vs Plugin model

## By Topic

### Architecture & Foundations
| File | Purpose |
|------|---------|
| `foundations/domain_architecture.md` | Core vs Plugin, privilege separation |
| `foundations/meshforge_ecosystem.md` | All 5 repos, boundaries, APIs |
| `foundations/tui_architecture.md` | Mixin pattern, DialogBackend, adding features |
| `foundations/ui_design_decisions.md` | TUI design principles |
| `foundations/persistent_issues.md` | Recurring bugs & resolution patterns |
| `foundations/persistent_issues_archive.md` | Resolved issues (historical) |

### AI & Development Practices
| File | Purpose |
|------|---------|
| `foundations/ai_principles.md` | Human-centered design philosophy + AI interface guidelines |
| `foundations/ai_development_practices.md` | AI coding guidelines |
| `foundations/auto_review_principles.md` | Auto-review system principles |

### Active Development
| File | Purpose |
|------|---------|
| `TODO_PRIORITIES.md` | Current task priorities & branch strategy |
| `foundations/missing_features.md` | TUI features gap tracker |

### RNS/Gateway Research
| File | Purpose |
|------|---------|
| `research/rns_comprehensive.md` | RNS protocol deep dive |
| `research/rns_complete.md` | RNS configuration guide |
| `research/rns_integration.md` | RNS integration patterns |
| `research/rns_gateway_windows.md` | Windows RNS gateway setup |
| `research/gateway_setup_guide.md` | Gateway configuration guide |

### MeshCore (Alpha Branch)
| File | Purpose |
|------|---------|
| `research/dual_protocol_meshcore.md` | MeshCore bridge research |
| `research/meshcore_proxy_analysis.md` | MeshCore reliability patterns |

### RF & Physical Layer
| File | Purpose |
|------|---------|
| `research/lora_physical_layer.md` | LoRa PHY deep-dive |
| `research/semtech_official_reference.md` | Official Semtech LoRa reference |

### Integration & Infrastructure
| File | Purpose |
|------|---------|
| `research/hamclock_complete.md` | HamClock/NOAA integration |
| `research/local_mqtt_architecture.md` | MQTT bridging design |
| `research/aredn_integration.md` | AREDN mesh integration |
| `research/nginx_reliability_patterns.md` | Reliability patterns |
| `export/MESHFORGE_INTEGRATION.md` | MQTT integration guide (ecosystem) |

### Hardware
| File | Purpose |
|------|---------|
| `hardware/clockworkpi.md` | ClockworkPi DevTerm/uConsole |
| `research/uconsole_portable_noc.md` | Portable NOC design |

### Plans
| File | Purpose |
|------|---------|
| `plans/v1.0_roadmap.md` | v1.0 definition & criteria |
| `plans/strategic_improvements.md` | Strategic roadmap items |
| `plans/noc_test_plan.md` | Lab infrastructure & testing |
| `plans/qth_test_checklist.md` | Field testing checklist |

### Knowledge & Context
| File | Purpose |
|------|---------|
| `dude_ai_university.md` | Complete project knowledge base |
| `memory_timeline.md` | Project history & institutional memory |

## Directories

- **agents/** - Agent definitions (3 files)
- **articles/** - Development stories & completed plans (5 files)
- **assessments/** - Reliability assessments (1 file)
- **audits/** - Code review reports (4 files)
- **commands/** - Slash command definitions (6 files)
- **foundations/** - Core principles (10 files)
- **hardware/** - Device-specific docs (1 file)
- **plans/** - Implementation plans (4 files)
- **postmortems/** - Session retrospectives (7 files)
- **publications/** - Whitepaper (1 file)
- **research/** - Technical research (21 files)
- **rules/** - Security & testing rules (2 files)
- **session-notes/** - Session notes, naming convention: `YYYY-MM-DD-topic.md` (3 files)
- **tasks/** - Active task plans (0 files — completed plans moved to articles/)

## Quick Lookups

**Path.home() bug?** -> `foundations/persistent_issues.md#issue-1` (RESOLVED)
**Service not starting?** -> `foundations/persistent_issues.md#issue-3`
**Core vs Plugin?** -> `foundations/domain_architecture.md`
**Large file guidelines?** -> `foundations/persistent_issues.md#issue-6`

---
*Updated: 2026-02-21. ~78 files after cleanup. Session notes standardized to YYYY-MM-DD naming. Stale GTK roadmap and duplicate audit deleted. Healthcheck skill updated with doc freshness audit.*
