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


# --------------------------------------------------------------------------
# user_timers — systemd --user timers, declare-and-observe (2026-08-09)
#
# WHY THIS EXISTS: before the declaration, "never enrolled here" and "enrolled,
# then someone disabled it" were the SAME filesystem observation, so
# synth_soak_degraded had to call both inert and a silently-retired exerciser
# was indistinguishable from a box that never ran one.
#
# Every test injects enrollment. None of them may consult the real box's
# ~/.config/systemd/user (feedback_tests_must_pin_ambient_state).
# --------------------------------------------------------------------------

_UT_CATALOG = {
    "roles": {
        "meshanchor-noc": {
            "services": {"rnsd": "enabled"},
            "user_timers": {"meshforge-synth-soak.timer": "enabled",
                            "meshforge-propagation-soak.timer": "enabled"},
        },
        "noc-derived": {
            "inherits": "meshanchor-noc",
            "user_timers": {"meshforge-synth-soak.timer": "absent",
                            "meshforge-propagation-soak.timer": "absent"},
        },
        "no-timers": {"services": {"rnsd": "enabled"}},
    }
}


def _enrolled(mapping):
    """Patch the SSOT enrollment reader with an explicit per-unit answer."""
    return patch.object(pr, "user_timer_enrolled",
                        side_effect=lambda unit: mapping.get(unit))


class TestUserTimerInheritance:
    def test_user_timers_inherit_like_services(self):
        """The whole point of merging them the same way: a key that inherited
        for `services` but silently not for `user_timers` would hand the next
        person a role with no coverage and no error."""
        r = pr.resolve_role(_UT_CATALOG, "noc-derived")
        assert r["services"]["rnsd"] == "enabled"          # inherited
        assert r["user_timers"]["meshforge-synth-soak.timer"] == "absent"  # child wins

    def test_child_override_wins_over_parent(self):
        parent = pr.resolve_role(_UT_CATALOG, "meshanchor-noc")
        child = pr.resolve_role(_UT_CATALOG, "noc-derived")
        assert parent["user_timers"]["meshforge-synth-soak.timer"] == "enabled"
        assert child["user_timers"]["meshforge-synth-soak.timer"] == "absent"

    def test_role_without_the_key_gets_an_empty_map(self):
        r = pr.resolve_role(_UT_CATALOG, "no-timers")
        assert r["user_timers"] == {}


class TestUserTimerActions:
    SYNTH = "meshforge-synth-soak.timer"

    def test_declared_enabled_and_enrolled_is_noop(self):
        with _enrolled({self.SYNTH: True}):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        assert [a.verb for a in acts] == ["noop"]

    def test_declared_enabled_but_disabled_is_REQUIRED_warn(self):
        """THE case this whole change exists for — the drill.

        Someone disables the exerciser on a box whose role says it runs it.
        Before: synth_soak_degraded went inert and nothing said a word."""
        with _enrolled({self.SYNTH: False}):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        assert len(acts) == 1
        a = acts[0]
        assert a.verb == "warn" and a.required is True
        assert a.current == "not-enabled" and a.desired == "enabled"

    def test_declared_absent_but_enrolled_is_REQUIRED_warn(self):
        """The other direction: an organ appears on a box that never declared
        it. Silent extra load is drift too."""
        with _enrolled({self.SYNTH: True}):
            acts = pr._user_timer_actions({self.SYNTH: "absent"})
        assert acts[0].verb == "warn" and acts[0].required is True

    def test_declared_absent_and_not_enrolled_is_noop(self):
        with _enrolled({self.SYNTH: False}):
            acts = pr._user_timer_actions({self.SYNTH: "absent"})
        assert [a.verb for a in acts] == ["noop"]

    def test_unobservable_is_advisory_never_drift(self):
        """Unknown is not drift. Flattening it to 'not enabled' would make an
        unreadable dir look like a disabled organ (honest_failure_modes #1)."""
        with _enrolled({self.SYNTH: None}):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        assert acts[0].verb == "warn" and acts[0].required is False
        assert "unreadable" in acts[0].detail

    def test_unknown_desired_state_is_advisory(self):
        with _enrolled({self.SYNTH: True}):
            acts = pr._user_timer_actions({self.SYNTH: "sometimes"})
        assert acts[0].verb == "warn" and acts[0].required is False

    def test_no_declarations_emits_nothing(self):
        assert pr._user_timer_actions({}) == []


class TestUserTimersNeverConverged:
    """--apply must never enable/disable a user unit.

    A converge sweep that can start units is exactly how the 2026-07-24
    incident happened (a restart loop started a unit disabled by design), and
    user units are a scope root cannot even reach (#82). Detection yes,
    convergence by hand.
    """
    SYNTH = "meshforge-synth-soak.timer"

    @pytest.mark.parametrize("declared,enrolled", [
        ("enabled", False), ("absent", True), ("enabled", True),
        ("disabled", False), ("enabled", None),
    ])
    def test_no_action_verb_is_ever_executable(self, declared, enrolled):
        with _enrolled({self.SYNTH: enrolled}):
            acts = pr._user_timer_actions({self.SYNTH: declared})
        for a in acts:
            assert a.verb in ("noop", "warn"), (
                f"user timer produced executable verb {a.verb!r} — --apply "
                f"would act on a systemd --user unit")

    def test_apply_action_skips_the_required_warn(self):
        """Belt and braces: even handed the drift action, apply does nothing."""
        with _enrolled({self.SYNTH: False}):
            acts = pr._user_timer_actions({self.SYNTH: "enabled"})
        with patch.object(pr, "enable_service") as en, \
                patch.object(pr, "stop_service") as st:
            pr.apply_action(acts[0])
        en.assert_not_called()
        st.assert_not_called()
        assert acts[0].result == "skipped"


class TestShippedCatalogDeclaresUserTimers:
    """The real docs/fleet_roles.yaml, not a fixture.

    user_timers inherits, so a role that inherits full-gateway and does NOT
    run the soaks must say `absent` out loud. Without this test, adding a new
    inheriting role silently signs it up for two exercisers it does not run —
    and the box would page for drift the author never declared.
    """

    def test_every_inheriting_role_declares_its_soak_timers(self):
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        parents_with_timers = {
            name for name, node in catalog["roles"].items()
            if node.get("user_timers")}
        for name, node in catalog["roles"].items():
            if node.get("inherits") in parents_with_timers:
                assert node.get("user_timers"), (
                    f"role '{name}' inherits '{node['inherits']}' which "
                    f"declares user_timers, but declares none of its own — "
                    f"it silently inherits them. Say `absent` explicitly.")

    def test_declared_states_are_all_valid(self):
        catalog = pr.load_roles(pr.DEFAULT_ROLES_FILE)
        for name, node in catalog["roles"].items():
            for unit, state in (node.get("user_timers") or {}).items():
                assert state in pr.VALID_UNIT_STATES, (
                    f"{name}.{unit} = {state!r}")
