# TDD Feature Implementation

**Feature:** $ARGUMENTS

---

## MeshForge TDD Protocol

### Phase 1: Understand
1. Read relevant source files in `src/`
2. Check existing tests in `tests/`
3. Review `.claude/foundations/persistent_issues.md` for gotchas
4. Identify the module(s) to modify

### Phase 2: Red (Failing Tests)
```bash
# Create/update test file
tests/test_<module>.py

# Run to confirm tests fail
python3 -m pytest tests/test_<module>.py -v
```

Write tests that:
- Define expected behavior clearly
- Cover edge cases
- Test error conditions
- Use existing fixtures from `tests/conftest.py`

### Phase 3: Green (Implementation)
- Write **minimum code** to pass tests
- Follow MeshForge security rules (no shell=True, no bare except)
- Use `get_real_user_home()` not `Path.home()`
- Add timeouts to all subprocess calls

### Phase 4: Verify
```bash
# Run specific tests
python3 -m pytest tests/test_<module>.py -v

# Run full suite
python3 -m pytest tests/ -v

# Run auto_review
cd src && python3 -c "from utils.auto_review import ReviewOrchestrator; r=ReviewOrchestrator(); print(f'Issues: {r.run_full_review().total_issues}')"
```

### Phase 5: Refactor
- Clean up while keeping tests green
- Extract helpers if patterns repeat
- Add type hints where helpful
- Update docstrings

### Phase 6: Commit
```bash
git add -A
git commit -m "feat: <description>"
```

---

## Gateway Focus Areas
If working on `src/gateway/`:
- Message passing (RNS ↔ Meshtastic)
- Position/telemetry bridging
- Identity mapping (RNS hash ↔ node ID)
- Reconnection with exponential backoff
- Queue management (maxsize=1000)

---

## Completion Signal

When ALL of the following are true:
- [ ] Tests written and passing
- [ ] Implementation complete
- [ ] auto_review shows 0 issues (or documented exceptions)
- [ ] Code committed

Output: `<promise>COMPLETE</promise>`

---

*"Me fail English? That's unpossible!"* - Ralph Wiggum
