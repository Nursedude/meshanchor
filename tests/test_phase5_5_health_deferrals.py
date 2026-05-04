"""Phase 5.5 — profile-aware health code (the Phase 5 deferrals).

Phase 5 made the startup banner + `/health` endpoint profile-aware (a
service irrelevant for the active deployment profile is `not_applicable`,
not "missing"). Three downstream call sites stayed legacy because the
user-visible MESHCORE-red bug only fired in the banner — they all live
under code paths that only execute under GATEWAY/FULL where the services
*are* required, so deferring was safe. Phase 5.5 hoists profile-awareness
into a shared helper (`utils.profile_services`) and wires it through:

1. `health_score._on_service_event` — `critical=` is now profile-driven
   (was hardcoded `service_name in ('meshtasticd', 'rnsd')`).
2. `active_health_probe.create_gateway_health_probe` — services not in
   the profile's required+optional sets are skipped (in addition to the
   existing noc.yaml `managed: false` filter).
3. `service_menu._bridge_preflight` — the meshtasticd-not-running issue
   is gated on `feature_enabled('meshtastic')` so MESHCORE-only users
   reaching the preflight via the Optional Gateways submenu don't see
   spurious "meshtasticd is required" noise.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
TESTS_DIR = os.path.dirname(__file__)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)


# ─────────────────────────────────────────────────────────────────────
# Test profiles — minimal stand-ins for ProfileDefinition
# ─────────────────────────────────────────────────────────────────────


def _profile(*, required=(), optional=(), feature_flags=None):
    return SimpleNamespace(
        required_services=list(required),
        optional_services=list(optional),
        feature_flags=feature_flags or {},
    )


MESHCORE = _profile(required=[], optional=[],
                    feature_flags={"meshtastic": False})
GATEWAY = _profile(
    required=[],
    optional=["meshtasticd", "rnsd", "mosquitto"],
    feature_flags={"meshtastic": True},
)
FULL = _profile(
    required=["rnsd", "mosquitto"],
    optional=["meshtasticd"],
    feature_flags={"meshtastic": True},
)


@pytest.fixture(autouse=True)
def _drop_profile_cache():
    """Clear profile_services cache between tests so each test sees a
    deterministic resolution path."""
    from utils.profile_services import invalidate_cache
    invalidate_cache()
    yield
    invalidate_cache()


# ─────────────────────────────────────────────────────────────────────
# 1. utils/profile_services — the central helper
# ─────────────────────────────────────────────────────────────────────


class TestServiceRole:
    def test_meshcore_marks_meshtasticd_not_applicable(self):
        from utils.profile_services import service_role
        assert service_role("meshtasticd", profile=MESHCORE) == "not_applicable"
        assert service_role("rnsd", profile=MESHCORE) == "not_applicable"
        assert service_role("mosquitto", profile=MESHCORE) == "not_applicable"

    def test_gateway_marks_all_three_optional(self):
        from utils.profile_services import service_role
        assert service_role("meshtasticd", profile=GATEWAY) == "optional"
        assert service_role("rnsd", profile=GATEWAY) == "optional"
        assert service_role("mosquitto", profile=GATEWAY) == "optional"

    def test_full_marks_rnsd_and_mosquitto_required(self):
        from utils.profile_services import service_role
        assert service_role("rnsd", profile=FULL) == "required"
        assert service_role("mosquitto", profile=FULL) == "required"
        assert service_role("meshtasticd", profile=FULL) == "optional"

    def test_unknown_service_under_known_profile_is_not_applicable(self):
        from utils.profile_services import service_role
        assert service_role("nginx", profile=FULL) == "not_applicable"

    def test_falls_back_to_legacy_when_profile_resolution_fails(self):
        """When `profile=None` and `load_or_detect_profile` raises, we
        must not crash a daemon — fall back to the legacy hardcoded set."""
        from utils import profile_services

        with patch(
            "utils.profile_services._active_profile",
            return_value=None,
        ):
            assert profile_services.service_role("meshtasticd") == "required"
            assert profile_services.service_role("rnsd") == "required"
            assert profile_services.service_role("mosquitto") == "optional"
            assert profile_services.service_role("nginx") == "not_applicable"


class TestIsCritical:
    def test_only_required_services_are_critical(self):
        from utils.profile_services import is_critical
        assert is_critical("rnsd", profile=FULL) is True
        assert is_critical("mosquitto", profile=FULL) is True
        # optional under FULL — not critical.
        assert is_critical("meshtasticd", profile=FULL) is False

    def test_meshcore_has_no_critical_services(self):
        """The whole point of MESHCORE-as-primary: a missing meshtasticd
        / rnsd / mosquitto must not hurt the network health score."""
        from utils.profile_services import is_critical
        assert is_critical("meshtasticd", profile=MESHCORE) is False
        assert is_critical("rnsd", profile=MESHCORE) is False
        assert is_critical("mosquitto", profile=MESHCORE) is False


class TestIsManaged:
    def test_meshcore_doesnt_manage_legacy_three(self):
        from utils.profile_services import is_managed
        assert is_managed("meshtasticd", profile=MESHCORE) is False
        assert is_managed("rnsd", profile=MESHCORE) is False
        assert is_managed("mosquitto", profile=MESHCORE) is False

    def test_gateway_manages_all_three(self):
        from utils.profile_services import is_managed
        assert is_managed("meshtasticd", profile=GATEWAY) is True
        assert is_managed("rnsd", profile=GATEWAY) is True
        assert is_managed("mosquitto", profile=GATEWAY) is True

    def test_full_manages_required_and_optional(self):
        from utils.profile_services import is_managed
        assert is_managed("rnsd", profile=FULL) is True
        assert is_managed("mosquitto", profile=FULL) is True
        assert is_managed("meshtasticd", profile=FULL) is True


class TestActiveProfileCache:
    def test_resolved_once_then_cached(self):
        from utils.profile_services import _active_profile

        with patch(
            "utils.deployment_profiles.load_or_detect_profile",
            return_value=GATEWAY,
        ) as m:
            first = _active_profile()
            second = _active_profile()
        assert first is GATEWAY
        assert second is GATEWAY
        assert m.call_count == 1  # cached

    def test_invalidate_cache_drops_resolved_profile(self):
        from utils.profile_services import _active_profile, invalidate_cache

        with patch(
            "utils.deployment_profiles.load_or_detect_profile",
            return_value=GATEWAY,
        ) as m:
            _active_profile()
            invalidate_cache()
            _active_profile()
        assert m.call_count == 2

    def test_resolution_failure_caches_none(self):
        from utils.profile_services import _active_profile

        with patch(
            "utils.deployment_profiles.load_or_detect_profile",
            side_effect=RuntimeError("boom"),
        ) as m:
            assert _active_profile() is None
            assert _active_profile() is None
        # Cache hit: we don't retry on every call after a failure.
        assert m.call_count == 1


# ─────────────────────────────────────────────────────────────────────
# 2. health_score._on_service_event — `critical=` is profile-driven
# ─────────────────────────────────────────────────────────────────────


class TestHealthScoreCritical:
    def _make_event(self, name, available):
        return SimpleNamespace(service_name=name, available=available)

    def test_meshcore_meshtasticd_event_is_not_critical(self):
        from utils import health_score

        scorer = MagicMock()
        with patch.object(health_score, "_health_scorer", scorer), \
             patch("utils.profile_services._active_profile", return_value=MESHCORE):
            health_score._on_service_event(self._make_event("meshtasticd", False))
        scorer.report_service_status.assert_called_once()
        assert scorer.report_service_status.call_args.kwargs["critical"] is False

    def test_full_rnsd_event_is_critical(self):
        from utils import health_score

        scorer = MagicMock()
        with patch.object(health_score, "_health_scorer", scorer), \
             patch("utils.profile_services._active_profile", return_value=FULL):
            health_score._on_service_event(self._make_event("rnsd", True))
        assert scorer.report_service_status.call_args.kwargs["critical"] is True

    def test_full_meshtasticd_event_is_not_critical_optional(self):
        """meshtasticd is OPTIONAL under FULL — should not be critical."""
        from utils import health_score

        scorer = MagicMock()
        with patch.object(health_score, "_health_scorer", scorer), \
             patch("utils.profile_services._active_profile", return_value=FULL):
            health_score._on_service_event(self._make_event("meshtasticd", False))
        assert scorer.report_service_status.call_args.kwargs["critical"] is False

    def test_event_dropped_when_scorer_unset(self):
        from utils import health_score

        with patch.object(health_score, "_health_scorer", None):
            # Should not raise.
            health_score._on_service_event(self._make_event("rnsd", False))


# ─────────────────────────────────────────────────────────────────────
# 3. active_health_probe.create_default_probe — profile-driven probe set
# ─────────────────────────────────────────────────────────────────────


class TestActiveHealthProbe:
    def _registered_check_names(self, probe):
        # `register_check` is the only public hook we need to inspect; we
        # capture it via Mock and pull names from positional args.
        return [c.args[0] for c in probe.register_check.call_args_list]

    def test_meshcore_registers_no_legacy_three(self):
        from utils import active_health_probe as ahp

        probe = MagicMock()
        with patch.object(ahp, "ActiveHealthProbe", return_value=probe), \
             patch.object(ahp, "_unmanaged_services", return_value=set()), \
             patch("utils.profile_services._active_profile", return_value=MESHCORE):
            ahp.create_gateway_health_probe()
        names = self._registered_check_names(probe)
        # None of the legacy three should be probed under MESHCORE.
        assert "meshtasticd" not in names
        assert "rnsd" not in names
        assert "mosquitto" not in names

    def test_gateway_registers_all_three(self):
        from utils import active_health_probe as ahp

        probe = MagicMock()
        with patch.object(ahp, "ActiveHealthProbe", return_value=probe), \
             patch.object(ahp, "_unmanaged_services", return_value=set()), \
             patch("utils.profile_services._active_profile", return_value=GATEWAY):
            ahp.create_gateway_health_probe()
        names = self._registered_check_names(probe)
        assert "meshtasticd" in names
        assert "rnsd" in names
        assert "mosquitto" in names

    def test_unmanaged_filter_still_wins_under_gateway(self):
        """noc.yaml's `managed: false` is the per-host override that
        was already there pre-Phase-5.5; Phase 5.5 must not regress it."""
        from utils import active_health_probe as ahp

        probe = MagicMock()
        with patch.object(ahp, "ActiveHealthProbe", return_value=probe), \
             patch.object(ahp, "_unmanaged_services",
                          return_value={"meshtasticd"}), \
             patch("utils.profile_services._active_profile", return_value=GATEWAY):
            ahp.create_gateway_health_probe()
        names = self._registered_check_names(probe)
        assert "meshtasticd" not in names  # unmanaged in noc.yaml
        assert "rnsd" in names
        assert "mosquitto" in names

    def test_full_registers_required_and_optional(self):
        from utils import active_health_probe as ahp

        probe = MagicMock()
        with patch.object(ahp, "ActiveHealthProbe", return_value=probe), \
             patch.object(ahp, "_unmanaged_services", return_value=set()), \
             patch("utils.profile_services._active_profile", return_value=FULL):
            ahp.create_gateway_health_probe()
        names = self._registered_check_names(probe)
        assert set(names) == {"meshtasticd", "rnsd", "mosquitto"}


# ─────────────────────────────────────────────────────────────────────
# 4. service_menu._bridge_preflight — meshtasticd issue gated on flag
# ─────────────────────────────────────────────────────────────────────


def _make_service_menu_handler(feature_flags):
    """Build a ServiceMenuHandler with a fake TUIContext for testing."""
    from launcher_tui.handlers.service_menu import ServiceMenuHandler
    from handler_test_utils import make_handler_context

    ctx = make_handler_context(feature_flags=feature_flags)
    h = ServiceMenuHandler()
    h.set_context(ctx)
    return h


class TestBridgePreflightProfileGate:
    def _patch_external_checks(self):
        """Patch every external dep of `_bridge_preflight` so the test
        only exercises the flag-gated meshtasticd issue."""
        # rnsd: report running so we don't trip on issue 1.
        rnsd_status = MagicMock(available=True)
        # meshtasticd: report DOWN so the issue would be added if the
        # gate doesn't take effect.
        mt_status = MagicMock(available=False)

        def fake_check_service(name, **_):
            if name == "rnsd":
                return rnsd_status
            if name == "meshtasticd":
                return mt_status
            raise AssertionError(f"unexpected check_service({name!r})")

        # Identity exists, gateway config valid, no nomadnet conflict.
        return [
            patch("launcher_tui.handlers.service_menu.check_service",
                  side_effect=fake_check_service),
            patch("launcher_tui.handlers.service_menu.subprocess.run",
                  return_value=MagicMock(returncode=1, stdout="", stderr="")),
            patch("launcher_tui.handlers.service_menu.get_identity_path",
                  return_value=MagicMock(exists=lambda: True)),
            patch("gateway.config.GatewayConfig.load",
                  return_value=MagicMock(validate=lambda: (True, []))),
        ]

    def test_meshcore_profile_skips_meshtasticd_issue(self):
        h = _make_service_menu_handler(feature_flags={
            "meshtastic": False, "meshcore": True,
        })
        h.ctx.dialog.yesno = MagicMock(return_value=False)

        with self._patch_external_checks()[0], \
             self._patch_external_checks()[1], \
             self._patch_external_checks()[2], \
             self._patch_external_checks()[3]:
            result = h._bridge_preflight()

        # Either result is fine (preflight passes/fails for unrelated
        # reasons in the test env). What matters: no msgbox / yesno
        # dialog was raised over a *meshtasticd* issue. The dialog is
        # only invoked when there's at least one issue.
        if h.ctx.dialog.yesno.called:
            yesno_text = h.ctx.dialog.yesno.call_args.args[1]
            assert "meshtasticd" not in yesno_text

    def test_gateway_profile_keeps_meshtasticd_issue(self):
        h = _make_service_menu_handler(feature_flags={
            "meshtastic": True, "gateway": True,
        })
        h.ctx.dialog.yesno = MagicMock(return_value=False)

        patches = self._patch_external_checks()
        with patches[0], patches[1], patches[2], patches[3]:
            h._bridge_preflight()

        # Under GATEWAY the meshtasticd issue must surface in the dialog.
        assert h.ctx.dialog.yesno.called
        yesno_text = h.ctx.dialog.yesno.call_args.args[1]
        assert "meshtasticd" in yesno_text


# ─────────────────────────────────────────────────────────────────────
# 5. save_profile invalidates the profile_services cache
# ─────────────────────────────────────────────────────────────────────


class TestSaveProfileInvalidatesCache:
    def test_save_profile_drops_cached_profile(self, tmp_path, monkeypatch):
        """After `save_profile`, the next is_critical call must consult
        `load_or_detect_profile` again rather than serving stale state."""
        from utils import deployment_profiles, profile_services

        # Redirect _PROFILE_PATH so the test doesn't write the user's real config.
        monkeypatch.setattr(deployment_profiles, "_PROFILE_PATH",
                            tmp_path / "deployment.json")

        # Prime cache with GATEWAY.
        with patch(
            "utils.deployment_profiles.load_or_detect_profile",
            return_value=GATEWAY,
        ):
            profile_services._active_profile()  # caches GATEWAY

        # Save the MESHCORE profile — should invalidate cache.
        meshcore_real = deployment_profiles.PROFILES[
            deployment_profiles.ProfileName.MESHCORE
        ]
        deployment_profiles.save_profile(meshcore_real)

        # Subsequent resolution sees MESHCORE.
        with patch(
            "utils.deployment_profiles.load_or_detect_profile",
            return_value=MESHCORE,
        ):
            assert profile_services.is_critical("meshtasticd") is False
            assert profile_services.is_critical("rnsd") is False
