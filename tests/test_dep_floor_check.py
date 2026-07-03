"""Dep version-floor health check — check_dep_version_floor + the shared
requirements_floor parser (MeshForge probe_dep_version_drift parity,
2026-07-03).

The class under guard: a box whose own interpreter imports a pip dependency
BELOW the requirements/core.txt fleet floor — a missed or failed update.
Found live the day this shipped: meshanchor-server on meshtastic 2.7.8 while
the fleet pin was 2.7.9, with nothing watching.

Honest-failure-modes pins: an unreadable floor or a not-importable package is
INDETERMINATE (healthy-with-reason, this codebase's self-guard idiom) — it
must never read as drift (false alarm) and its reason string must never claim
compliance.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.active_health_probe import ActiveHealthProbe, create_gateway_health_probe
from utils.requirements_floor import (
    read_requirement_floors,
    read_floor,
    version_below,
)


@pytest.fixture
def probe():
    return ActiveHealthProbe(interval=3600)


@pytest.fixture
def floor_file(tmp_path):
    """A requirements file carrying a meshtastic floor of 2.7.9."""
    req = tmp_path / "core.txt"
    req.write_text(
        "# comment\n"
        "rich>=13.0.0\n"
        "meshtastic>=2.7.9\n"
    )
    return req


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------

class TestCheckDepVersionFloor:

    def test_below_floor_unhealthy(self, probe, floor_file):
        r = probe.check_dep_version_floor(
            requirements_path=floor_file, installed={"meshtastic": "2.7.8"})
        assert not r.healthy
        assert "meshtastic installed=2.7.8 floor>=2.7.9" in r.reason
        assert "restart meshanchor" in r.reason  # actionable fix pointer

    def test_at_floor_healthy(self, probe, floor_file):
        r = probe.check_dep_version_floor(
            requirements_path=floor_file, installed={"meshtastic": "2.7.9"})
        assert r.healthy
        assert r.reason.startswith("dep_floor_ok")

    def test_above_floor_healthy_including_two_digit_patch(self, probe, floor_file):
        # 2.7.10 > 2.7.9 — the compare must be numeric, not lexicographic.
        r = probe.check_dep_version_floor(
            requirements_path=floor_file, installed={"meshtastic": "2.7.10"})
        assert r.healthy

    def test_missing_floor_file_indeterminate_not_alarm(self, probe, tmp_path):
        r = probe.check_dep_version_floor(
            requirements_path=tmp_path / "nope.txt",
            installed={"meshtastic": "0.0.1"})
        assert r.healthy  # unreadable SSOT is indeterminate, never a page
        assert "indeterminate" in r.reason
        assert "dep_floor_ok" not in r.reason  # and never claims compliance

    def test_floor_line_absent_indeterminate(self, probe, tmp_path):
        req = tmp_path / "core.txt"
        req.write_text("rich>=13.0.0\n")  # no meshtastic line at all
        r = probe.check_dep_version_floor(
            requirements_path=req, installed={"meshtastic": "0.0.1"})
        assert r.healthy
        assert "indeterminate" in r.reason

    def test_package_not_importable_indeterminate(self, probe, floor_file):
        # installed={} models importlib.metadata finding nothing watched —
        # a venv elsewhere may be the consumer; don't guess.
        r = probe.check_dep_version_floor(
            requirements_path=floor_file, installed={})
        assert r.healthy
        assert "not_importable" in r.reason

    def test_unparseable_installed_version_never_alarms(self, probe, floor_file):
        r = probe.check_dep_version_floor(
            requirements_path=floor_file, installed={"meshtastic": "garbage"})
        assert r.healthy  # version_below is conservative on unparseable input

    def test_live_read_of_own_env_returns_a_result(self, probe):
        # No injection: reads this repo's real core.txt + this interpreter.
        # Whatever the env, the check must complete without raising and give
        # a truthful reason from the known vocabulary.
        r = probe.check_dep_version_floor()
        assert isinstance(r.healthy, bool)
        assert r.reason  # never silent


# ---------------------------------------------------------------------------
# Wiring — the check must actually be registered (a detector nobody runs
# is the #79/#80 void class)
# ---------------------------------------------------------------------------

class TestDepFloorRegistered:

    def test_gateway_probe_registers_dep_floor(self):
        probe = create_gateway_health_probe(interval=3600)
        assert "dep_floor" in probe._checks


# ---------------------------------------------------------------------------
# The shared parser (one parser, one comparison — honest_failure_modes #5)
# ---------------------------------------------------------------------------

class TestRequirementsFloorParser:

    @pytest.mark.parametrize("line,expected", [
        ("meshtastic>=2.7.9", "2.7.9"),
        ("meshtastic==2.7.9", "2.7.9"),
        ("meshtastic~=2.7.9", "2.7.9"),
        ("  meshtastic >= 2.7.9  # trailing comment", "2.7.9"),
    ])
    def test_floor_forms_parse(self, tmp_path, line, expected):
        req = tmp_path / "r.txt"
        req.write_text(line + "\n")
        assert read_requirement_floors(["meshtastic"], req) == {
            "meshtastic": expected}

    @pytest.mark.parametrize("line", [
        "meshtastic",            # bare — no floor to compare
        "meshtastic<3.0",        # upper bound only
        "# meshtastic>=2.7.9",   # commented out
    ])
    def test_non_floor_forms_ignored(self, tmp_path, line):
        req = tmp_path / "r.txt"
        req.write_text(line + "\n")
        assert read_requirement_floors(["meshtastic"], req) == {}

    def test_unreadable_path_returns_empty_never_raises(self, tmp_path):
        assert read_requirement_floors(["meshtastic"], tmp_path / "nope") == {}

    def test_read_floor_from_repo_core_txt_matches_fleet_pin(self):
        # Pins the LIVE SSOT: this repo's core.txt floor must parse and be
        # >= the 2.7.9 fleet baseline this arc established.
        floor = read_floor("meshtastic")
        assert floor is not None
        assert not version_below(floor, "2.7.9")

    @pytest.mark.parametrize("have,floor,below", [
        ("2.7.8", "2.7.9", True),
        ("2.7.9", "2.7.9", False),
        ("2.7.10", "2.7.9", False),   # numeric, not lexicographic
        ("2.8", "2.7.9", False),
        (None, "2.7.9", False),       # missing → conservative
        ("2.7.9", None, False),
        ("garbage", "2.7.9", False),  # unparseable → conservative
    ])
    def test_version_below(self, have, floor, below):
        assert version_below(have, floor) is below
