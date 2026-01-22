# MeshForge Install Reliability Triage - 2026-01-22

> **Triage Session**: Post-failure analysis following overnight installation issues
> **Problem Statement**: MeshForge takes MORE time to install and configure than manual setup
> **Goal**: Make installation reliable out-of-the-box

---

## Executive Summary

**The Core Problem**: MeshForge's installation is unreliable because there's:
1. No verification that installation succeeded
2. No single source of truth for service status
3. Code divergence between UIs means bugs exist in one but not another
4. Configuration management is fragmented across scripts

**Time comparison** (User's observation):
- Manual install + configure + test: ~15 minutes
- MeshForge install + troubleshoot failures: 45+ minutes

This is unacceptable. The tool should save time, not cost it.

---

## Root Cause Analysis

### 1. No Post-Install Verification (Critical)

**Symptom**: Install script completes but nothing works
**Why**: After `install_noc.sh` runs, there's no automated check that:
- meshtasticd actually starts
- Web client (port 9443) responds
- config.yaml is valid
- HAT config is selected (for SPI radios)

**Evidence**: Scripts exist for runtime health (`startup_health.py`) but they're:
- Not called automatically after install
- Not blocking - failures just display, don't halt
- Not comprehensive - miss config.yaml validation

**Fix Required**: `meshforge --verify-install` command + automatic call after install_noc.sh

---

### 2. Fragmented Service Checking (Critical)

**Symptom**: Service shows "running" in one UI, "stopped" in another
**Why**: FOUR different service checking methods exist:

| Method | Location | Used By |
|--------|----------|---------|
| `utils/service_check.py` | Centralized | startup_health.py |
| Direct `systemctl` calls | Rich CLI main.py | Diagnostics menu |
| Socket + import tests | GTK diagnostics.py | Health panel |
| Port + pgrep | Various | Ad-hoc checks |

Each method can return DIFFERENT results for the same service.

**Example - RNS checking**:
```python
# Rich CLI: Uses rnstatus command
subprocess.run(['rnstatus', '-j'], timeout=5)

# GTK: Uses Python import test (WRONG - tests module, not daemon)
try:
    import RNS
except: pass

# service_check.py: Uses systemctl (CORRECT)
systemctl is-active rnsd
```

**Fix Required**: All code paths MUST use `utils/service_check.py`

---

### 3. config.yaml Management Chaos (Critical)

**Symptom**: Web client doesn't work after install (Issue #22)
**Why**: Multiple places can modify `/etc/meshtasticd/config.yaml`:
- `install_noc.sh` (lines 508-524)
- TUI `_fix_spi_config()`
- TUI `_install_native_meshtasticd()`
- Manual user edits

**Problem patterns**:
1. Install script creates minimal config
2. User runs TUI function that overwrites with different template
3. Web client breaks because "Webserver:" section removed

**Fix Required**:
- NEVER overwrite existing valid config.yaml
- Always CHECK for "Webserver:" before any modification
- Document the contract: what sections are REQUIRED

**Required config.yaml sections**:
```yaml
Lora:
  Module: auto  # Or specific HAT
Webserver:
  Port: 9443
  RootPath: /usr/share/meshtasticd/web
Logging:
  LogLevel: info
General:
  MaxNodes: 200
  ConfigDirectory: /etc/meshtasticd/config.d/
```

---

### 4. TUI vs Rich CLI Code Divergence (High)

**Symptom**: User reports "I was in Rich CLI but you kept fixing TUI"
**Why**: Diagnostic tools implemented SEPARATELY in both UIs:

| Feature | Rich CLI Location | GTK Location | Shared? |
|---------|-------------------|--------------|---------|
| Service status | main.py:446-453 | diagnostics.py:1370-1450 | NO |
| Network tests | main.py:457-495 | diagnostics.py:1452-1486 | NO |
| Log viewing | main.py:523-795 | diagnostics.py:555-755 | NO |
| System stats | main.py:797-845 | diagnostics.py:960-1001 | NO |

**Impact**:
- Bug fixed in one UI doesn't fix the other
- Features added to one UI don't appear in other
- Testing effort doubled
- User confusion about which UI they're using

**Fix Required**: Refactor to shared `commands/` layer:
1. `commands/diagnostics.py` - Already exists, not used
2. `commands/service.py` - Already exists, partially used
3. Both UIs call commands layer, not implement inline

---

### 5. Installation Script Complexity (Medium)

**Symptom**: install_noc.sh has many branches, hard to test all paths
**Why**: Script handles 9+ distinct scenarios:

| Radio Type | Daemon Type | OS Type | Result |
|------------|-------------|---------|--------|
| SPI | Native | Debian 12 | apt install meshtasticd |
| SPI | Native | Debian 13 | apt install meshtasticd |
| SPI | Native | Raspbian | apt install meshtasticd |
| SPI | Native | Ubuntu | apt install meshtasticd |
| SPI | Python fallback | Any | pip install meshtastic |
| USB | Native available | Any | Use native daemon |
| USB | Python only | Any | No daemon needed |
| None | Native available | Any | Create config manually |
| None | Python only | Any | Placeholder service |

**Fix Required**:
1. Add end-to-end tests for each matrix combination
2. Simplify where possible (fewer special cases)
3. Fail fast with clear error messages, don't continue degraded

---

### 6. Logging Improvements Not Applied Everywhere (Medium)

**Symptom**: Some errors only appear in DEBUG logs, user never sees them
**Why**: Issue #4 documents this but fixes are incomplete

**Files still with `logger.debug()` for errors**:
- Need audit to find remaining instances

**Fix Required**: Grep for `logger.debug.*error\|fail\|exception` and elevate to INFO/ERROR

---

## Commit History Pattern Analysis

Looking at last 30 commits, pattern of churn:

```
935ffcb fix: Stop TUI from creating HAT templates
fd6d7b9 fix: Stop overwriting meshtasticd's config.yaml during install
f57c5b2 fix: Stop overwriting meshtasticd templates
2da2b27 fix: Use official meshtasticd config.yaml template format
05a4018 feat: Add all common HAT config templates to available.d/
e9ade16 fix: Use Module: auto in config.yaml for proper LoRa detection
3ede587 fix: Detect conflicting SPI+USB configs on startup
527ab66 fix: Auto-detect and fix SPI HAT misconfiguration on startup
```

**Pattern**: 8 commits on same topic (config.yaml handling) in 3 days
- First: Added HAT templates
- Then: Fixed template format
- Then: Stopped creating templates
- Then: Stopped overwriting config.yaml

**Root cause**: No clear architecture decision about WHO owns config.yaml
- Answer should be: meshtasticd package owns it, MeshForge only READS it
- MeshForge can SUGGEST copying HAT configs to config.d/
- MeshForge should NEVER modify config.yaml directly

---

## Action Plan: Reliability Improvements

### Phase 1: Verification (This Week)

**1.1 Create post-install verification command**
```bash
meshforge --verify-install
```
Checks:
- [ ] meshtasticd service exists and can start
- [ ] config.yaml has Webserver section
- [ ] Port 9443 responds to HTTPS
- [ ] At least one radio detected OR config.d/ has HAT config
- [ ] RNS installed and rnsd can start
- [ ] udev rules loaded (via `udevadm info`)

**1.2 Add verification call to install_noc.sh**
After install completes, automatically run verification:
```bash
echo "Verifying installation..."
/usr/local/bin/meshforge --verify-install
if [[ $? -ne 0 ]]; then
    echo "WARNING: Installation verification failed"
    echo "Run 'meshforge --verify-install' for details"
fi
```

**1.3 Create quick-test script for CI**
Test the 4 most common paths:
- Fresh Raspberry Pi with SPI HAT
- Fresh Debian with USB radio
- Existing meshtasticd (client mode)
- No radio (monitoring only)

### Phase 2: Unification (Next Week)

**2.1 Enforce commands layer usage**
All service checks MUST go through `utils/service_check.py`:
- [ ] Rich CLI diagnostics menu
- [ ] GTK diagnostics panel
- [ ] startup_health.py
- [ ] orchestrator.py

**2.2 Remove inline diagnostic implementations**
Replace inline code with calls to `commands/diagnostics.py`:
- [ ] main.py network tests → `commands.diagnostics.run_network_tests()`
- [ ] diagnostics.py health cards → `commands.diagnostics.get_system_health()`

**2.3 Create diagnostic test suite**
Unit tests that verify:
- Same input → same output across all code paths
- No service checking code outside approved modules

### Phase 3: Simplification (Following Week)

**3.1 Reduce install script branches**
Simplify to 3 primary modes:
1. **Full NOC**: meshtasticd + rnsd + meshforge (default)
2. **Client**: meshforge only, connect to existing services
3. **Monitor**: meshforge only, MQTT monitoring, no local services

**3.2 Remove dead code paths**
- Python meshtasticd wrapper (USB radios don't need daemon)
- Placeholder services (confusing, don't help)
- Multiple config templates (let meshtasticd provide them)

**3.3 Clear error messages**
Every failure should print:
1. What failed
2. Why it failed (best guess)
3. How to fix it

---

## Success Metrics

After implementing Phase 1:
- [ ] `meshforge --verify-install` passes on fresh install
- [ ] Install script prints clear pass/fail at end
- [ ] Zero "silent failures" - every error visible

After implementing Phase 2:
- [ ] Service status identical in Rich CLI and GTK
- [ ] No inline service checking code outside approved modules
- [ ] Test coverage for diagnostic commands

After implementing Phase 3:
- [ ] Install time comparable to manual (< 20 minutes)
- [ ] Fewer than 3 support questions per week about install
- [ ] CI passes all 4 primary install paths

---

## File Reference

Key files for reliability improvements:

| Purpose | File | Status |
|---------|------|--------|
| Install script | `scripts/install_noc.sh` | Needs verification call |
| Post-install verify | `scripts/verify_post_install.sh` | **CREATE** |
| Startup health | `src/utils/startup_health.py` | Expand checks |
| Service checker | `src/utils/service_check.py` | Make authoritative |
| Rich CLI diag | `src/launcher_tui/main.py` | Refactor to commands |
| GTK diag | `src/gtk_ui/panels/diagnostics.py` | Refactor to commands |
| Commands layer | `src/commands/diagnostics.py` | Make canonical |

---

## Immediate Actions

1. **Fix linter issue** - Path.home() in system_tools_mixin.py ✓ DONE
2. **Document this triage** - Create this file ✓ DONE
3. **Add Issue #23** to persistent_issues.md - "No post-install verification"
4. **Create verify_post_install.sh** - Basic version this session

---

*Triage by Dude AI - 2026-01-22*
