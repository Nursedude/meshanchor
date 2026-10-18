"""Run the honest_status shell harnesses under pytest.

WHY THIS FILE EXISTS (ported from the MF twin 2026-07-29): pytest collects
``test_*.py``, and a ``.sh`` file is invisible to it. ``test_honest_status_
preserve.sh`` has therefore never been executed by this repo's suite or CI —
nothing referenced it. A test that cannot fail and a test that nobody runs are
the same artifact.

This wrapper makes the shell harnesses part of the suite, so the next time one
of them stops working, CI says so.
"""
from __future__ import annotations

import glob
import os
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
# Every tests/test_*.sh is a harness by naming convention — no allowlist to
# forget to update (MF twin's fix, ported 2026-10-18: the list here had left
# test_honest_status_interrupt.sh unrun since 09-07, and would have done the
# same to test_ensure_prune_cron.sh).
_HARNESSES = sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(_HERE, "test_*.sh"))
)


@pytest.mark.parametrize("script", _HARNESSES)
def test_shell_harness_passes(script):
    path = os.path.join(_HERE, script)
    assert os.path.isfile(path), f"{script} missing"
    proc = subprocess.run(
        ["bash", path], capture_output=True, text=True, timeout=300,
        cwd=_HERE,
    )
    # The harnesses print PASS:/FAIL: per assertion and exit non-zero on any
    # failure. Surface their own output — a bare exit code would make a
    # regression here as opaque as the silence this file exists to end.
    assert proc.returncode == 0, (
        f"{script} failed (exit {proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert "ALL PASS" in proc.stdout, f"{script}: no ALL PASS line\n{proc.stdout}"
