# Session Notes: NomadNet Privilege Fix

**Date**: 2026-01-31
**Branch**: `claude/fix-nomadnet-launch-moBKw`
**Issue**: NomadNet fails with "Permission denied: ~/.reticulum/storage"

## Problem

When launching NomadNet from MeshForge TUI (which runs with sudo), NomadNet fails with:

```
PermissionError: [Errno 13] Permission denied: '/home/wh6gxz/.reticulum/storage'
```

### Root Cause

1. MeshForge TUI runs with `sudo` (required for service management)
2. NomadNet daemon launch (`_launch_nomadnet_daemon`) was running as root
3. Previous root runs created `~/.reticulum/` with root ownership
4. Now even when dropping to user, they can't write to their own directories

## Fix Applied

### 1. Added `_fix_user_directory_ownership()` helper

Detects directories in user home owned by root and offers to fix with `chown -R`:
- `~/.reticulum`
- `~/.nomadnetwork`
- `~/.config/nomadnetwork`

### 2. Fixed daemon launch to drop privileges

Before:
```python
subprocess.Popen([nn_path, '--daemon'], ...)
```

After:
```python
if sudo_user and sudo_user != 'root':
    cmd = ['sudo', '-u', sudo_user, '-i', nn_path, '--daemon']
else:
    cmd = [nn_path, '--daemon']
subprocess.Popen(cmd, ...)
```

### 3. Fixed config generation

The "Edit NomadNet Config" option that briefly runs NomadNet to generate config
now also runs as the real user.

## Files Changed

- `src/launcher_tui/nomadnet_client_mixin.py` (+124 lines)

## Testing

- Syntax check: PASS
- Linter: PASS (only pre-existing warning in main.py)
- Module import: PASS

## Commit

```
fix: Drop privileges when launching NomadNet from sudo context
```

## Related Issues

This is related to Issue #1 (Path.home() returning /root) but is a distinct problem:
- Issue #1: Code using wrong path when looking for config
- This issue: Directories created with wrong ownership by previous root runs

Both stem from running MeshForge with sudo.
