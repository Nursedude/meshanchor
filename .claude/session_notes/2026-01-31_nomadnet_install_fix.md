# Session Notes: 2026-01-31 NomadNet Install Fix

## Branch: `claude/fix-fresh-install-issues-YNtgD`

## Issue

NomadNet was not available on fresh installs. Users reported NomadNet "not found" even though they expected it to be installed.

## Root Cause

The `install_noc.sh` script:
- Installed python3, pip, venv
- Installed meshtastic via pip
- Installed rns via pip
- **Did NOT install pipx**
- **Did NOT install nomadnet**

NomadNet was only installed if users explicitly went to RNS > NomadNet > Install in the TUI.

## Fix

Modified `scripts/install_noc.sh` to:
1. Install `pipx` via apt before RNS installation
2. Install NomadNet via pipx as part of RNS setup
3. Install as real user (not root) when running via sudo
4. Verify NomadNet installation and show path

## Commit

| Commit | Description |
|--------|-------------|
| `3ece3c2` | fix: Install pipx and NomadNet during fresh install |

## Verification

```bash
which nomadnet
# /root/.local/bin/nomadnet

nomadnet --version
# Nomad Network Client 0.9.8
```

## Additional Fix: COLORMODE_16 Bug

NomadNet 0.9.8 has urwid version corruption issue causing:
```
module 'nomadnet.ui' has no attribute 'COLORMODE_16'
```

**Fix**: Clean reinstall - uninstall before install.

| Commit | Description |
|--------|-------------|
| `04d0bcc` | fix: Clean reinstall NomadNet to avoid urwid COLORMODE_16 bug |

Manual fix:
```bash
pipx uninstall nomadnet && pipx install nomadnet
```

## Next Steps

- Test on fresh Pi install
- Consider adding NomadNet config setup (shared instance mode)
