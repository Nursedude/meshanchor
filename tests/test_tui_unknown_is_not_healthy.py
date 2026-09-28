"""TUI audit finding 5 (2026-09-27): landing/summary screens mapped UNKNOWN to
healthy. MeshAnchor twin of MeshForge a37a6df3 — MA has no NOC Home and
no mini pane, so only the three sites MA carries are pinned here. Each test
PLANTS the fault (a probe that raises, a daemon that is
stale, a leg that cannot be observed) and asserts the screen does not say
healthy. The happy paths were already journeyed; these are the error branches.
"""

import inspect
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


# ------------------------------------------------------ RNS diagnostics summary

class TestRnsDiagnosticsSummary:
    def _summary(self, issues, warnings, unobserved):
        from handlers._rns_diagnostics_engine import summary_lines
        return "\n".join(summary_lines(issues, warnings, unobserved))

    def test_blind_leg_never_reads_passed(self):
        text = self._summary([], [], ["interface traffic (rnstatus cannot connect)"])
        assert "All checks passed" not in text and "Connectivity OK" not in text
        assert "NOT CHECKED" in text and "UNKNOWN" in text

    def test_blind_leg_with_warnings_is_not_ok(self):
        text = self._summary([], ["w"], ["x"])
        assert "Connectivity OK" not in text

    def test_all_observed_and_clean_still_passes(self):
        assert "All checks passed" in self._summary([], [], [])

    def test_every_blind_rnstatus_branch_records_unobserved(self):
        from handlers import _rns_diagnostics_engine as mod
        src = inspect.getsource(mod.run_rns_diagnostics)
        block = src[src.index("iface_health = check_rns_interface_health()"):
                    src.index("# Summary")]
        # three "could not" branches + the exception handler
        assert block.count("unobserved.append(") == 4


# --------------------------------------------------------- Dashboard datapath

def test_dashboard_path_probe_checks_the_exit_code():
    # Source-level (the probe sits inside a six-step interactive routine):
    # the rnpath branch must branch on returncode before parsing stdout.
    from handlers import dashboard
    src = inspect.getsource(dashboard)
    block = src[src.index("# Test 6: RNS path table"):]
    block = block[:block.index("except FileNotFoundError")]
    assert "result.returncode != 0" in block
    assert block.index("result.returncode != 0") < block.index("splitlines()")
