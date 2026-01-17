# Changelog

All notable changes to MeshForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.6-beta] - 2026-01-17

### Added
- AI Tools integration in TUI (intelligent diagnostics, knowledge base, Claude assistant)
- Coverage map generation with Folium
- Example configuration files in `examples/`
- Visual documentation guide (`docs/VISUAL_GUIDE.md`)
- GitHub Actions CI pipeline
- Pre-commit hooks configuration
- Systemd service file for running as a service
- Docker container support
- Man page documentation

### Changed
- README redesigned with clearer problem/solution structure
- Simplified ASCII diagrams for cross-platform rendering

### Fixed
- Added REGION_ENUM_MAP for proper region integer-to-string conversion

## [0.4.5-beta] - 2026-01-16

### Added
- Intelligent diagnostics system (standalone + PRO mode)
- Knowledge base for mesh networking concepts
- Claude assistant integration for natural language queries
- Coverage map generator using Folium
- Auto-review system for code quality

### Changed
- Refactored launcher_tui/main.py using mixin pattern
- Refactored hamclock.py using mixin pattern
- Improved GTK panel organization

## [0.4.4-beta] - 2026-01-15

### Added
- Full radio configuration panel in GTK
- Channel configuration with 8-channel support
- Frequency slot calculator (djb2 hash)
- Gateway templates (Standard, Turbo, MtnMesh)

### Fixed
- WebKit root sandbox issue (added browser fallback)
- Path.home() issues in multiple files

## [0.4.3-beta] - 2026-01-14

### Added
- MQTT dashboard panel
- Node tracker with unified view
- Position sharing between networks

### Changed
- Improved service management UI
- Better error messages for service failures

## [0.4.2-beta] - 2026-01-13

### Added
- RF tools (FSPL, Fresnel zone, link budget calculator)
- Site planner with range estimation
- Hardware detection improvements

### Fixed
- SPI HAT detection on Raspberry Pi 5

## [0.4.1-beta] - 2026-01-12

### Added
- TUI interface (raspi-config style)
- Web monitor dashboard
- Basic gateway bridge functionality

### Changed
- Reorganized project structure
- Moved commands to dedicated layer

## [0.4.0-beta] - 2026-01-10

### Added
- Initial public release
- GTK4 desktop interface
- Meshtastic-RNS gateway bridge
- Service management (meshtasticd)
- Hardware configuration

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 0.4.6-beta | 2026-01-17 | AI tools in TUI, Docker, CI/CD |
| 0.4.5-beta | 2026-01-16 | AI diagnostics system |
| 0.4.4-beta | 2026-01-15 | Radio configuration |
| 0.4.3-beta | 2026-01-14 | MQTT dashboard |
| 0.4.2-beta | 2026-01-13 | RF tools |
| 0.4.1-beta | 2026-01-12 | TUI interface |
| 0.4.0-beta | 2026-01-10 | Initial release |
