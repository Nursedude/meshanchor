"""`lint.py --all` must actually cover what its name claims.

Until 2026-09-18 ``--all`` walked ``src/`` only. ``--staged`` covered
``scripts/``, so lint rules were enforced there ONLY on files someone
happened to touch. CLAUDE.md's pre-push check names ``lint.py --all`` as
*the* lint gate, so a scope narrower than the name is the detector-blind
class aimed at our own gate: it reported "clean" over ground it never
looked at.

Ported from MeshForge ``eed28145``, where the same narrow walk hid a raw
``RNS.Reticulum()`` (MF019) in ``scripts/validate_rns_to_mesh.py`` for a
full session — including inside that fix's own lint measurements. This
repo's hidden ground held exactly one finding, and it was a FALSE
positive: ``scripts/db_audit.py`` opens each DB read-only to read back the
PRAGMAs ``connect_tuned`` sets, so routing it through the helper would make
the checker apply what it measures. It is allowlisted for MF013 in the same
commit that widened the walk — the widening's first act was to be *correct*
about what it newly saw, not merely louder.

⚠️ These tests PLANT a violation and require the linter to find it. Reading
``get_all_python_files('scripts')`` back out of the module would just be the
implementation agreeing with itself — the failure mode was that the gate
could not SEE, so the test has to make it look.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_LINT = _REPO / "scripts" / "lint.py"

# MF001: Path.home() outside the sanctioned fallback shapes.
# ⚠️ It must be an ASSIGNMENT. `return Path.home()` and `else Path.home()`
# are DELIBERATE exemptions (the get_real_user_home fallback), so a plant
# using `return` would report "no violation found" and look exactly like a
# blind gate. A drill whose plant is not actually a violation proves
# nothing, in the friendliest possible way.
_VIOLATION = (
    '"""Temporary lint-scope probe — deleted by the test that wrote it."""\n'
    "from pathlib import Path\n"
    "\n"
    "\n"
    "def _probe():\n"
    "    config = Path.home() / '.config' / 'meshanchor'\n"
    "    return config\n"
)


def _run_lint_all():
    return subprocess.run(
        [sys.executable, str(_LINT), "--all"],
        cwd=str(_REPO), capture_output=True, text=True, timeout=300,
    )


@pytest.fixture
def planted(request):
    """Write a violating .py into a directory, always clean it up."""
    written = []

    def _plant(subdir: str):
        p = _REPO / subdir / "_lint_scope_probe_tmp.py"
        assert not p.exists(), f"{p} already exists — refusing to overwrite"
        p.write_text(_VIOLATION)
        written.append(p)
        return p

    yield _plant
    for p in written:
        p.unlink(missing_ok=True)


class TestLintAllCoversScripts:

    def test_baseline_all_is_green(self):
        """If this fails, a REAL violation exists and the planted-violation
        tests below cannot distinguish it from the plant."""
        r = _run_lint_all()
        assert r.returncode == 0, (
            f"lint --all is not green before planting:\n{r.stdout}\n{r.stderr}")

    def test_all_sees_a_violation_planted_in_scripts(self, planted):
        """THE regression. Pre-2026-09-18 this passed lint clean."""
        p = planted("scripts")
        r = _run_lint_all()
        assert r.returncode != 0, (
            "lint --all reported clean with a planted MF001 violation in "
            "scripts/ — the gate cannot see scripts/, which is the exact "
            "blindness this test exists for")
        assert p.name in r.stdout, (
            f"violation not attributed to {p.name}:\n{r.stdout}")

    def test_all_still_sees_a_violation_planted_in_src(self, planted):
        """The widening must not have replaced the original scope."""
        p = planted("src")
        r = _run_lint_all()
        assert r.returncode != 0, "lint --all no longer covers src/"
        assert p.name in r.stdout

    def test_cleanup_actually_happened(self):
        """The plants above are written INTO the repo; a leaked probe file
        would fail the tree's own stat sentinels later and be attributed to
        whoever ran next. Assert the fixture's finalizer did its job."""
        for subdir in ("scripts", "src"):
            leaked = _REPO / subdir / "_lint_scope_probe_tmp.py"
            assert not leaked.exists(), f"probe file leaked: {leaked}"


class TestDbAuditStaysTheAuditor:
    """MF013's db_audit.py exemption is load-bearing, not a silencer.

    It is sanctioned ONLY because the auditor opens read-only. If a future
    edit makes it open writable, the exemption would hide a real writer —
    so pin the read-only URI, not just the allowlist entry.
    """

    def test_db_audit_opens_read_only(self):
        src = (_REPO / "scripts" / "db_audit.py").read_text()
        assert "sqlite3.connect(" in src, "db_audit.py no longer connects — re-check the MF013 allowlist"
        assert "?mode=ro" in src, (
            "db_audit.py is MF013-allowlisted because it opens read-only; "
            "it no longer does, so the exemption now hides a real writer")
