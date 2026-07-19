"""Tests for the MeshAnchor role-aware fleet provisioner (scripts/provision_role.py).

MeshCore-side port of MeshForge's test_provision_role.py (2026-07-18). Covers
role resolution (inherits/merge), the pure plan/diff engine against mocked SSOT
observe functions, action->SSOT-call mapping, external-role skip, the (empty)
masking invariant, and deployment.json role read/write.

Run: python3 -m pytest tests/test_provision_role.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "scripts", _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import provision_role as pr  # noqa: E402


CATALOG = {
    "roles": {
        "meshanchor-noc": {
            "services": {"rnsd": "enabled", "mosquitto": "enabled",
                         "meshanchor-daemon": "enabled", "meshanchor-map": "enabled",
                         "meshtasticd": "disabled", "meshanchor-gateway": "disabled"},
        },
        "base": {"services": {"rnsd": "enabled"}},
        "child": {"inherits": "base", "services": {"mosquitto": "enabled"}},
        "external": {"provisioned_by": "firmware",
                     "services": {"rnsd": "enabled"}},
    }
}


class TestResolveRole:
    def test_inherits_merges_parent_services(self):
        r = pr.resolve_role(CATALOG, "child")
        assert r["services"]["rnsd"] == "enabled"       # from parent
        assert r["services"]["mosquitto"] == "enabled"  # own
        assert "inherits" in r

    def test_no_inherits_returns_own(self):
        r = pr.resolve_role(CATALOG, "meshanchor-noc")
        assert r["services"]["meshtasticd"] == "disabled"
        assert "inherits" not in r

    def test_unknown_role_raises(self):
        with pytest.raises(KeyError):
            pr.resolve_role(CATALOG, "nope")

    def test_load_roles_requires_roles_key(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 0\n")
        with pytest.raises(ValueError):
            pr.load_roles(bad)

    def test_real_catalog_loads_and_resolves(self):
        """The committed docs/fleet_roles.yaml parses and carries meshanchor-noc."""
        cat = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        rd = pr.resolve_role(cat, "meshanchor-noc")
        assert rd["services"]["rnsd"] == "enabled"
        assert rd["services"]["meshanchor-daemon"] == "enabled"


def _mock_observe(running, enabled, installed, masked=False):
    return (
        patch.object(pr, "check_systemd_service", return_value=(running, enabled)),
        patch.object(pr, "is_service_unit_installed", return_value=installed),
        patch.object(pr, "is_service_masked", return_value=masked),
    )


def _plan_one(unit, desired, running, enabled, installed, masked=False):
    role_def = {"services": {unit: desired}}
    m1, m2, m3 = _mock_observe(running, enabled, installed, masked)
    with m1, m2, m3:
        return pr.plan(role_def)


def _pick(actions, item):
    return [x for x in actions if x.item == item][0]


class TestPlanUnitStates:
    def test_enabled_when_active_is_noop(self):
        assert _pick(_plan_one("meshanchor-map", "enabled", True, True, True),
                     "meshanchor-map").verb == "noop"

    def test_enabled_when_inactive_enables(self):
        assert _pick(_plan_one("meshanchor-map", "enabled", False, False, True),
                     "meshanchor-map").verb == "enable"

    def test_enabled_when_absent_warns_required(self):
        a = _pick(_plan_one("meshanchor-map", "enabled", False, False, False),
                  "meshanchor-map")
        assert a.verb == "warn" and a.required is True

    def test_disabled_when_active_disables(self):
        assert _pick(_plan_one("meshtasticd", "disabled", True, True, True),
                     "meshtasticd").verb == "disable"

    def test_disabled_when_absent_is_noop(self):
        assert _pick(_plan_one("meshtasticd", "disabled", False, False, False),
                     "meshtasticd").verb == "noop"

    def test_absent_when_present_warns_not_required(self):
        a = _pick(_plan_one("meshanchor-gateway", "absent", False, False, True),
                  "meshanchor-gateway")
        assert a.verb == "warn" and a.required is False

    def test_absent_when_missing_is_noop(self):
        assert _pick(_plan_one("meshanchor-gateway", "absent", False, False, False),
                     "meshanchor-gateway").verb == "noop"


class TestPlanServiceOverrides:
    def _plan_with_override(self, override):
        role_def = {"services": {"meshanchor-map": "enabled"}}
        m1, m2, m3 = _mock_observe(False, False, True)
        with m1, m2, m3:
            return pr.plan(role_def, {"meshanchor-map": override})

    def test_waiver_with_reason_is_nonblocking_advisory(self):
        a = _pick(self._plan_with_override(
            {"state": "disabled", "reason": "RF-sparse site"}), "meshanchor-map")
        assert a.verb == "warn" and a.required is False and "RF-sparse" in a.detail

    def test_waiver_without_reason_stays_blocking(self):
        a = _pick(self._plan_with_override({"state": "disabled"}), "meshanchor-map")
        assert a.verb == "warn" and a.required is True

    def test_waived_unit_is_not_converged(self):
        a = _pick(self._plan_with_override(
            {"state": "disabled", "reason": "x"}), "meshanchor-map")
        assert a.verb not in pr.PLAN_CHANGE_VERBS

    def test_read_overrides_parses_deployment_json(self, tmp_path, monkeypatch):
        dj = tmp_path / "deployment.json"
        dj.write_text(json.dumps({"role": "meshanchor-noc",
                                  "service_overrides": {"meshtasticd": {"state": "absent"}}}))
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", dj)
        assert pr.read_overrides()["meshtasticd"]["state"] == "absent"

    def test_read_overrides_absent_key_is_empty(self, tmp_path, monkeypatch):
        dj = tmp_path / "deployment.json"
        dj.write_text(json.dumps({"role": "meshanchor-noc"}))
        monkeypatch.setattr(pr, "DEPLOYMENT_JSON", dj)
        assert pr.read_overrides() == {}


class TestMaskingInvariant:
    """MeshAnchor's KNOWN_RNS_RIVALS is empty (meshanchor-daemon is the legit RNS
    client on an MA box), so a converge produces NO mask action by default — but
    the masking loop still works if a genuine rival is ever registered."""

    def test_no_masking_by_default(self):
        role_def = {"services": {"rnsd": "enabled"}}
        m1, m2, m3 = _mock_observe(True, True, True)
        with m1, m2, m3:
            actions = pr.plan(role_def)
        assert not any(a.item.startswith("mask:") for a in actions)

    def test_injected_rival_present_gets_masked(self):
        role_def = {"services": {"rnsd": "enabled"}}
        with patch.object(pr, "KNOWN_RNS_RIVALS", ("some-rival",)), \
             patch.object(pr, "check_systemd_service", return_value=(True, True)), \
             patch.object(pr, "is_service_unit_installed", return_value=True), \
             patch.object(pr, "is_service_masked", return_value=False):
            actions = pr.plan(role_def)
        mask = _pick(actions, "mask:some-rival")
        assert mask.verb == "mask"

    def test_no_rnsd_no_masking(self):
        role_def = {"services": {"mosquitto": "enabled"}}
        with patch.object(pr, "KNOWN_RNS_RIVALS", ("some-rival",)), \
             patch.object(pr, "check_systemd_service", return_value=(True, True)), \
             patch.object(pr, "is_service_unit_installed", return_value=True), \
             patch.object(pr, "is_service_masked", return_value=False):
            actions = pr.plan(role_def)
        assert not any(a.item.startswith("mask:") for a in actions)


class TestApplyAction:
    def test_enable_calls_enable_service_with_start(self):
        a = pr.Action("u", "inactive", "enabled", "enable")
        with patch.object(pr, "enable_service", return_value=(True, "ok")) as m:
            assert pr.apply_action(a) is True
            m.assert_called_once_with("u", start=True)

    def test_disable_calls_stop_then_disable(self):
        a = pr.Action("u", "active", "disabled", "disable")
        with patch.object(pr, "stop_service", return_value=(True, "s")) as ms, \
             patch.object(pr, "disable_service", return_value=(True, "d")) as md:
            assert pr.apply_action(a) is True
            ms.assert_called_once()
            md.assert_called_once()

    def test_mask_calls_mask_service_with_bare_name(self):
        a = pr.Action("mask:some-rival", "present", "masked", "mask")
        with patch.object(pr, "mask_service", return_value=(True, "m")) as m:
            assert pr.apply_action(a) is True
            m.assert_called_once_with("some-rival")

    def test_noop_and_warn_never_mutate(self):
        with patch.object(pr, "enable_service") as m:
            pr.apply_action(pr.Action("u", "x", "y", "noop"))
            pr.apply_action(pr.Action("u", "x", "y", "warn"))
            m.assert_not_called()


class TestExternalRoleSkipped:
    def test_external_role_not_converged(self, tmp_path, monkeypatch):
        rf = tmp_path / "roles.yaml"
        rf.write_text(json.dumps(CATALOG))  # YAML is a JSON superset
        rc = pr.main(["--role", "external", "--roles-file", str(rf)])
        assert rc == 2  # provisioned_by -> refuse to converge
