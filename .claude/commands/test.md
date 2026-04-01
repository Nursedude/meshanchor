# Run Tests

Execute the MeshAnchor test suite and report results.

## Instructions

1. Run all test files:
```bash
cd /opt/meshanchor
python3 -m pytest tests/ -v --tb=short 2>&1 | head -100
```

2. If pytest not available, run individually:
```bash
python3 tests/test_security.py
python3 tests/test_rf.py
python3 tests/test_rns_bridge.py
```

3. Report pass/fail counts and any failures
