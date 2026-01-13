# MeshForge Release Management

## Release Channels

MeshForge uses a three-channel release system to ensure stability:

| Channel | Branch | Purpose | Stability |
|---------|--------|---------|-----------|
| **Alpha** | `alpha` | Experimental features, bleeding edge | May break |
| **Beta** | `beta` | Testing before production | Should work |
| **Stable** | `main` | Production releases | Verified stable |

## Version Numbering

```
X.Y.Z-channel

Examples:
  0.4.7-alpha    # Experimental
  0.4.7-beta     # Testing
  0.4.7          # Stable release
```

## Development Workflow

### For New Features

```bash
# 1. Create feature branch from alpha
git checkout alpha
git pull origin alpha
git checkout -b feature/my-feature

# 2. Develop and test
# ... make changes ...
python3 -m pytest tests/ -v

# 3. Create PR to alpha branch
gh pr create --base alpha --title "feat: My new feature"

# 4. After testing in alpha, cherry-pick to beta
git checkout beta
git cherry-pick <commit-hash>

# 5. After beta testing, merge to main
git checkout main
git merge beta
```

### For Bug Fixes

```bash
# Hotfixes go directly to main, then backport
git checkout main
git checkout -b fix/critical-bug
# ... fix ...
gh pr create --base main --title "fix: Critical bug"

# Backport to beta and alpha
git checkout beta && git cherry-pick <commit>
git checkout alpha && git cherry-pick <commit>
```

## Release Process

### Alpha Release
1. Merge feature PRs to `alpha` branch
2. Update `src/__version__.py`:
   ```python
   __version__ = "0.4.7-alpha"
   __status__ = "alpha"
   ```
3. Tag: `git tag v0.4.7-alpha`
4. Announce on GitHub Discussions (optional)

### Beta Release
1. Test alpha thoroughly (minimum 48 hours)
2. Cherry-pick stable features to `beta`
3. Update version:
   ```python
   __version__ = "0.4.7-beta"
   __status__ = "beta"
   ```
4. Tag: `git tag v0.4.7-beta`
5. Request community testing

### Stable Release
1. Beta must be stable for minimum 1 week
2. Run full test suite: `python3 -m pytest tests/ -v`
3. Run stability test: `sudo bash -c 'ulimit -n 65536 && timeout 3600 python3 src/launcher.py'`
4. Merge `beta` to `main`
5. Update version:
   ```python
   __version__ = "0.4.7"
   __status__ = "stable"
   ```
6. Tag: `git tag v0.4.7`
7. Create GitHub Release with changelog

## Branch Protection Rules

### main (Stable)
- Require PR reviews (1 minimum)
- Require status checks to pass
- No direct pushes
- Include administrators

### beta (Testing)
- Require PR reviews (1 minimum)
- Require status checks to pass
- No direct pushes

### alpha (Experimental)
- No required reviews (fast iteration)
- Status checks recommended
- Direct pushes allowed for maintainers

## Stability Testing Checklist

Before promoting to stable:

- [ ] Clean startup (no errors in logs)
- [ ] Connect to mesh network (250+ nodes)
- [ ] Run for 1 hour without crashes
- [ ] `ulimit -n 65536` verified
- [ ] All panels open without crash
- [ ] Log viewer works
- [ ] RNS panel functional
- [ ] No socket leaks (`ls /proc/PID/fd | wc -l` stable)

## Quick Setup

Run this to create the branch structure:

```bash
# Create branches from current stable (main)
git checkout main
git pull origin main

# Create alpha branch
git checkout -b alpha
git push -u origin alpha

# Create beta branch
git checkout main
git checkout -b beta
git push -u origin beta

# Return to main
git checkout main
```

## Installing Specific Channels

Users can install from specific channels:

```bash
# Stable (default)
git clone https://github.com/Nursedude/meshforge.git
cd meshforge && git checkout main

# Beta testing
git clone https://github.com/Nursedude/meshforge.git
cd meshforge && git checkout beta

# Alpha (bleeding edge)
git clone https://github.com/Nursedude/meshforge.git
cd meshforge && git checkout alpha
```

## Emergency Rollback

If a release breaks production:

```bash
# Find last good version
git log --oneline main | head -20

# Create hotfix branch
git checkout -b hotfix/rollback <last-good-commit>

# Or revert specific commit
git revert <bad-commit>

# Push emergency fix
gh pr create --base main --title "fix: Emergency rollback"
```

---
*Release management added after errno 24/22 stability crisis (Jan 2026)*
