# MeshForge Knowledge Healthcheck

Run a comprehensive audit of the MeshForge knowledge base to prevent memory degradation.

---

## Healthcheck Protocol

### 1. Version & State Check
```bash
# Current version
python3 -c "from src.__version__ import __version__; print(__version__)"

# Git state
git status --short
git log --oneline -5
```

### 2. Continuity Check
Cross-reference these critical files:
- `CLAUDE.md` - Main instructions
- `.claude/foundations/persistent_issues.md` - Known gotchas
- `.claude/foundations/domain_architecture.md` - Core vs plugin model
- `.claude/TODO_PRIORITIES.md` - Current priorities
- `src/__version__.py` - Version and changelog

Look for:
- Contradictions between files
- Outdated paths/imports
- Version mismatches
- Stale TODO items

### 3. Codebase Sync
```bash
# Verify documented paths exist
ls -la src/gateway/
ls -la src/gtk_ui/panels/
ls -la tests/

# Check for large files needing split
find src -name "*.py" -exec wc -l {} \; | sort -rn | head -10
```

Compare documented features vs actual `src/` implementation.

### 4. Auto-Review Integration
```bash
cd src && python3 -c "
from utils.auto_review import ReviewOrchestrator
r = ReviewOrchestrator()
report = r.run_full_review()
for cat, result in report.agent_results.items():
    print(f'{cat.value.title()}: {result.total_issues}')
print(f'Total: {report.total_issues}')
"
```

### 5. Test Health
```bash
python3 -m pytest tests/ -v --tb=no -q 2>&1 | tail -20
```

### 6. Fragmentation Analysis
- Find duplicated information across `.claude/` files
- Identify orphaned docs (referenced nowhere)
- Check for circular references
- Map information dependencies

### 7. File Size Audit
Flag files over 1,500 lines (run: `find src -name "*.py" -exec wc -l {} \; | sort -rn | head -10`):

| File | Lines | Status |
|------|-------|--------|
| launcher_tui/main.py | 1507 | 33 mixins, borderline — monitor |
| service_menu_mixin.py | 1575 | OpenHamClock/MQTT extraction candidates |
| rns_bridge.py | 1570 | MeshCoreBridgeMixin + MessageRouter extracted |
| knowledge_content.py | 1993 | Content file by design |

*Note: GTK files (gtk_ui/, main_web.py) were removed in v0.5.x. TUI is the only interface.*

---

## Output Format

Produce:
1. **Health Score** - 0-100 based on issues found
2. **Critical Issues** - Must fix immediately
3. **Warnings** - Should fix soon
4. **Suggestions** - Nice to have
5. **Actions Taken** - What was fixed during audit

---

## Completion Signal

When audit is complete and documented:

`<promise>HEALTHCHECK COMPLETE</promise>`

---

*"My cat's breath smells like cat food."* - Ralph Wiggum
