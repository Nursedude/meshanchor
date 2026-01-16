# MeshForge Branch Strategy

## Overview

MeshForge uses a three-tier branch model for structured releases.

```
alpha (development) → beta (testing) → main (stable)
```

## Branch Definitions

### `main` (Stable)
- **Purpose**: Production-ready releases
- **Protection**: Requires PR review
- **Version format**: `0.4.7`, `0.5.0`, `1.0.0`
- **Who uses it**: End users, HAM operators

### `beta` (Testing)
- **Purpose**: Release candidates for community testing
- **Merges from**: alpha
- **Version format**: `0.4.7-beta`, `0.5.0-rc1`
- **Who uses it**: Beta testers, early adopters

### `alpha` (Development)
- **Purpose**: Active development, feature branches merge here
- **Merges from**: Feature branches (`claude/*`, `feat/*`, `fix/*`)
- **Version format**: `0.4.7-alpha.1`, `0.4.7-alpha.2`
- **Who uses it**: Contributors, developers

## Feature Branch Naming

```
claude/feature-name-sessionId   # AI-generated features
feat/feature-name               # Human-developed features
fix/issue-description           # Bug fixes
security/vulnerability-fix      # Security patches (fast-track to main)
docs/documentation-update       # Documentation only
```

## Merge Process

### Feature → Alpha
1. Feature complete with tests
2. Auto-review passes (0 issues)
3. Syntax check passes
4. Squash merge with descriptive message

### Alpha → Beta
1. All alpha features tested together
2. No critical bugs
3. Documentation updated
4. Changelog updated
5. Version bumped to beta

### Beta → Main
1. Community testing complete
2. No reported critical issues for 1 week
3. Documentation finalized
4. Version bumped to stable
5. GitHub release created

## Hotfix Process

Critical bugs in main:
1. Branch from main: `fix/critical-bug`
2. Fix and test
3. Merge to main immediately
4. Cherry-pick to beta and alpha

## Version Numbering

Following semantic versioning:
- **Major** (1.x.x): Breaking changes, major rewrites
- **Minor** (x.1.x): New features, backward compatible
- **Patch** (x.x.1): Bug fixes, security patches

## Commands

```bash
# Create feature branch
git checkout alpha
git pull origin alpha
git checkout -b feat/my-feature

# Prepare for alpha merge
python3 -m pytest tests/ -v
cd src && python3 -c "from utils.auto_review import ReviewOrchestrator; r = ReviewOrchestrator(); print(r.run_full_review())"

# Update version
# Edit src/__version__.py
```
