"""Tests for the MF027 health-check fail-dark guard in scripts/lint.py.

MA shape of the MF twin's MF027 (build:fix doctrine 2026-07-29; the #80
class — degraded state mapped to a valid-looking value). ``HealthResult``
is binary, so MA's documented self-guard is HEALTHY-WITH-REASON: an
unobservable channel must name its blindness in ``reason`` so "observed
clean" and "could not look" stay distinguishable. An except-handler
returning ``HealthResult(healthy=True)`` with no reason — or ``None`` —
is fail-dark: the failed observation reads as healthy at every consumer,
forever.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import importlib.util
_lint_path = Path(__file__).parent.parent / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint_mf027", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


DARK_NO_REASON = '''\
def check_example_dark(self):
    try:
        observe()
    except OSError:
        return HealthResult(healthy=True)
    return HealthResult(healthy=True, reason="ok_observed")
'''

DARK_EMPTY_REASON = '''\
def check_example_empty(self):
    try:
        observe()
    except Exception:
        return HealthResult(healthy=True, reason="")
    return HealthResult(healthy=True, reason="ok_observed")
'''

DARK_NONE = '''\
def check_example_none(self):
    try:
        observe()
    except OSError:
        return None
    return HealthResult(healthy=True, reason="ok_observed")
'''

WITNESSED = '''\
def check_example_ok(self):
    try:
        observe()
    except OSError:
        return HealthResult(healthy=True, reason="journal_unobservable")
    return HealthResult(healthy=True, reason="ok_observed")
'''

FIRING = '''\
def check_example_fires(self):
    try:
        observe()
    except TimeoutError:
        return HealthResult(healthy=False, reason="probe timed out — wedge")
    return HealthResult(healthy=True, reason="ok_observed")
'''

DYNAMIC_HEALTHY = '''\
def check_example_dynamic(self):
    try:
        val = observe()
    except OSError as e:
        return HealthResult(healthy=bool(cached), reason=str(e))
    return HealthResult(healthy=True, reason="ok_observed")
'''

HELPER_EXEMPT = '''\
def _load_example_state(path):
    try:
        return open(path).read()
    except OSError:
        return None
'''


def _issues(tmp_path, source, name="active_health_probe_fixture.py"):
    p = tmp_path / name
    p.write_text(source, encoding="utf-8")
    return lint.check_probe_fail_dark([str(p)])


def test_healthy_true_without_reason_is_an_error(tmp_path):
    got = _issues(tmp_path, DARK_NO_REASON)
    assert len(got) == 1
    assert got[0].code == "MF027"
    assert "reason" in got[0].message


def test_empty_reason_is_still_dark(tmp_path):
    assert len(_issues(tmp_path, DARK_EMPTY_REASON)) == 1


def test_return_none_in_except_is_an_error(tmp_path):
    """Callers expect a HealthResult — None is dark AND shape-broken."""
    assert len(_issues(tmp_path, DARK_NONE)) == 1


def test_healthy_with_reason_passes(tmp_path):
    """The journal_unobservable convention — blindness named, greppable."""
    assert _issues(tmp_path, WITNESSED) == []


def test_healthy_false_is_the_check_firing_not_dark(tmp_path):
    assert _issues(tmp_path, FIRING) == []


def test_dynamic_healthy_expression_is_not_flagged(tmp_path):
    """A computed healthy= is a judgment the lint cannot see — allowed."""
    assert _issues(tmp_path, DYNAMIC_HEALTHY) == []


def test_helpers_are_exempt(tmp_path):
    assert _issues(tmp_path, HELPER_EXEMPT) == []


def test_only_active_health_probe_files_are_scanned(tmp_path):
    assert _issues(tmp_path, DARK_NO_REASON, name="some_other_module.py") == []


def test_syntax_error_is_skipped_not_crashed(tmp_path):
    assert _issues(tmp_path, "def broken(:\n") == []


def test_live_tree_is_clean():
    """Born on a surveyed-clean tree (0 dark / 14 witnessed, 2026-07-29) —
    this pins that it STAYS clean."""
    root = Path(__file__).parent.parent
    files = [str(p) for p in (root / "src" / "utils").glob("active_health_probe*.py")]
    assert files, "active_health_probe module missing?"
    assert lint.check_probe_fail_dark(files) == []
