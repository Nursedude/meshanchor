"""Phase 5 — startup health flip + /health endpoint.

Three layers under test:

1. `utils.startup_health` profile-aware classification — MESHCORE-only
   shows `ready` even with meshtasticd absent; FULL flags missing rnsd
   as error; backward-compat (no profile) still works.
2. `utils.startup_health.get_health_dict` JSON shape — includes
   `profile_name`, per-service `not_applicable` + `fix_hint`.
3. `ConfigAPIHandler` `/health` dispatch — 200 envelope, 503 on import
   failure, 500 on internal error, do_GET routes the path correctly.
"""

import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.config_api import ConfigAPIHandler
from utils.deployment_profiles import PROFILES, ProfileName
from utils.startup_health import (
    HealthSummary,
    ServiceHealth,
    get_health_dict,
    run_health_check,
)


# ─────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────


def _svc(name: str, running: bool, optional: bool = False, port=None) -> ServiceHealth:
    """Build a ServiceHealth without going through the live service_check."""
    return ServiceHealth(
        name=name,
        running=running,
        port=port if running else None,
        status_text="running" if running else "not running",
        optional=optional,
        fix_hint="" if running else f"sudo systemctl start {name}",
    )


def _make_api_stub(path: str):
    h = ConfigAPIHandler.__new__(ConfigAPIHandler)
    h.path = path
    h.command = "GET"
    h.headers = {"Content-Length": "0"}
    h.rfile = io.BytesIO(b"")
    h.wfile = io.BytesIO()
    h.client_address = ("127.0.0.1", 50001)
    h.api = None
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    return h


def _read_response(handler) -> dict:
    raw = handler.wfile.getvalue().decode()
    return json.loads(raw) if raw else {}


# ─────────────────────────────────────────────────────────────────────
# 1. Profile-aware classification
# ─────────────────────────────────────────────────────────────────────


class TestProfileAwareClassification:
    """The crux of Phase 5 — a MESHCORE box without meshtasticd/rnsd is
    `ready`, not red."""

    def _patch_checks(self, meshtasticd: bool, rnsd: bool, mosquitto: bool):
        return patch.multiple(
            "utils.startup_health",
            check_meshtasticd=lambda: _svc("meshtasticd", meshtasticd, optional=False, port=4403),
            check_rnsd=lambda: _svc("rnsd", rnsd, optional=True, port=37428),
            check_mosquitto=lambda: _svc("mosquitto", mosquitto, optional=True, port=1883),
            detect_hardware=MagicMock(return_value=MagicMock(detected=False)),
            get_node_count=MagicMock(return_value=0),
        )

    def test_meshcore_profile_with_no_services_is_ready(self):
        """The original bug: MESHCORE-only, nothing running, must be ready."""
        with self._patch_checks(meshtasticd=False, rnsd=False, mosquitto=False):
            summary = run_health_check(profile=PROFILES[ProfileName.MESHCORE])
        assert summary.overall_status == "ready"
        assert summary.is_ready is True
        # All three services flagged not_applicable for MESHCORE.
        for s in summary.services:
            assert s.not_applicable is True, f"{s.name} should be n/a under MESHCORE"

    def test_meshcore_profile_with_meshtasticd_running_still_ready(self):
        """An incidentally-running meshtasticd doesn't gate health under MESHCORE."""
        with self._patch_checks(meshtasticd=True, rnsd=False, mosquitto=False):
            summary = run_health_check(profile=PROFILES[ProfileName.MESHCORE])
        assert summary.overall_status == "ready"
        meshtasticd = next(s for s in summary.services if s.name == "meshtasticd")
        assert meshtasticd.not_applicable is True
        assert meshtasticd.running is True  # state still reported

    def test_full_profile_missing_required_rnsd_is_error(self):
        """FULL profile requires rnsd — its absence is an error, not a degradation."""
        with self._patch_checks(meshtasticd=True, rnsd=False, mosquitto=True):
            summary = run_health_check(profile=PROFILES[ProfileName.FULL])
        assert summary.overall_status == "error"
        assert summary.is_ready is False
        rnsd = next(s for s in summary.services if s.name == "rnsd")
        assert rnsd.not_applicable is False
        assert rnsd.optional is False  # required under FULL

    def test_full_profile_missing_optional_meshtasticd_is_degraded(self):
        """FULL marks meshtasticd as optional — its absence degrades but doesn't break."""
        with self._patch_checks(meshtasticd=False, rnsd=True, mosquitto=True):
            summary = run_health_check(profile=PROFILES[ProfileName.FULL])
        assert summary.overall_status == "degraded"
        assert summary.is_ready is True  # degraded counts as ready
        meshtasticd = next(s for s in summary.services if s.name == "meshtasticd")
        assert meshtasticd.optional is True
        assert meshtasticd.not_applicable is False

    def test_gateway_profile_all_optional_so_no_services_running_is_ready(self):
        """GATEWAY lists meshtasticd/rnsd/mosquitto as optional — none required."""
        with self._patch_checks(meshtasticd=False, rnsd=False, mosquitto=False):
            summary = run_health_check(profile=PROFILES[ProfileName.GATEWAY])
        # All three are optional under GATEWAY, so none required → critical_ok
        # but optional_ok=False → "degraded" (still reported as ready).
        assert summary.overall_status == "degraded"
        assert summary.is_ready is True

    def test_no_profile_falls_back_to_legacy_behaviour(self):
        """Backward compat — without a profile, meshtasticd is the gate."""
        with self._patch_checks(meshtasticd=False, rnsd=False, mosquitto=False):
            summary = run_health_check(profile=None)
        # Without a profile, ServiceHealth defaults flow through:
        # meshtasticd defaults to optional=False (required).
        # So missing meshtasticd → error.
        assert summary.overall_status == "error"
        assert summary.is_ready is False
        # No service should be flagged not_applicable when profile is None
        for s in summary.services:
            assert s.not_applicable is False


# ─────────────────────────────────────────────────────────────────────
# 2. is_ready property + get_health_dict shape
# ─────────────────────────────────────────────────────────────────────


class TestIsReadyProperty:
    def test_ready_status_is_ready(self):
        s = HealthSummary(overall_status="ready")
        assert s.is_ready is True

    def test_degraded_status_is_ready(self):
        """Degraded = optional service down but core OK — still operational."""
        s = HealthSummary(overall_status="degraded")
        assert s.is_ready is True

    def test_error_status_is_not_ready(self):
        s = HealthSummary(overall_status="error")
        assert s.is_ready is False

    def test_unknown_status_is_not_ready(self):
        """The default 'unknown' state must not claim readiness."""
        s = HealthSummary(overall_status="unknown")
        assert s.is_ready is False


class TestHealthDictShape:
    def test_dict_includes_profile_name(self):
        s = HealthSummary(profile_name="MeshCore", overall_status="ready")
        s.services.append(_svc("meshtasticd", running=False))
        s.services[-1].not_applicable = True
        d = get_health_dict(s)
        assert d["profile_name"] == "MeshCore"
        assert d["overall_status"] == "ready"
        assert d["is_ready"] is True

    def test_dict_includes_per_service_not_applicable(self):
        s = HealthSummary(overall_status="ready")
        running_svc = _svc("rnsd", running=True, optional=True, port=37428)
        running_svc.not_applicable = False
        na_svc = _svc("meshtasticd", running=False)
        na_svc.not_applicable = True
        s.services = [running_svc, na_svc]

        d = get_health_dict(s)
        names = {svc["name"]: svc for svc in d["services"]}
        assert names["rnsd"]["not_applicable"] is False
        assert names["meshtasticd"]["not_applicable"] is True
        # fix_hint must round-trip too
        assert "fix_hint" in names["meshtasticd"]


# ─────────────────────────────────────────────────────────────────────
# 3. /health endpoint dispatch
# ─────────────────────────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_returns_health_envelope_on_success(self):
        fake_summary = HealthSummary(profile_name="MeshCore", overall_status="ready")
        with patch("utils.startup_health.run_health_check", return_value=fake_summary), \
             patch("utils.deployment_profiles.load_or_detect_profile",
                   return_value=PROFILES[ProfileName.MESHCORE]):
            h = _make_api_stub("/health")
            h._handle_health_get()
        body = _read_response(h)
        assert "health" in body
        assert body["health"]["profile_name"] == "MeshCore"
        assert body["health"]["overall_status"] == "ready"
        assert body["health"]["is_ready"] is True
        h.send_response.assert_called_with(200)

    def test_run_health_check_exception_returns_500(self):
        with patch("utils.startup_health.run_health_check",
                   side_effect=RuntimeError("boom")):
            with patch("utils.deployment_profiles.load_or_detect_profile",
                       return_value=PROFILES[ProfileName.MESHCORE]):
                h = _make_api_stub("/health")
                h._handle_health_get()
        body = _read_response(h)
        assert "Health check failed" in body["error"]
        h.send_response.assert_called_with(500)

    def test_profile_resolution_failure_falls_back_to_no_profile(self):
        """If load_or_detect_profile raises, we still run a (legacy-mode) check."""
        fake_summary = HealthSummary(overall_status="error")
        with patch("utils.startup_health.run_health_check",
                   return_value=fake_summary) as mock_run:
            with patch("utils.deployment_profiles.load_or_detect_profile",
                       side_effect=RuntimeError("no config")):
                h = _make_api_stub("/health")
                h._handle_health_get()
        # run_health_check must have been called with profile=None
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs.get("profile") is None
        body = _read_response(h)
        assert body["health"]["overall_status"] == "error"

    def test_do_GET_dispatches_health_path(self):
        h = _make_api_stub("/health")
        with patch.object(h, "_handle_health_get") as routed:
            h.do_GET()
            routed.assert_called_once()

    def test_do_GET_dispatches_health_with_query_string(self):
        h = _make_api_stub("/health?nocache=1")
        with patch.object(h, "_handle_health_get") as routed:
            h.do_GET()
            routed.assert_called_once()


# ─────────────────────────────────────────────────────────────────────
# 4. Integration — do_GET path doesn't collide with /health-prefixed
#    config paths and doesn't require the config API to be initialized.
# ─────────────────────────────────────────────────────────────────────


class TestHealthRoutingIsolation:
    def test_health_works_when_config_api_uninitialized(self):
        """The /health route must not depend on `self.api` being set —
        it's the daemon's standalone readiness probe, not a config read."""
        fake_summary = HealthSummary(profile_name="MeshCore", overall_status="ready")
        with patch("utils.startup_health.run_health_check", return_value=fake_summary):
            with patch("utils.deployment_profiles.load_or_detect_profile",
                       return_value=PROFILES[ProfileName.MESHCORE]):
                h = _make_api_stub("/health")
                h.api = None  # explicit — config API not initialized
                h.do_GET()
        body = _read_response(h)
        assert body.get("health", {}).get("overall_status") == "ready"
        h.send_response.assert_called_with(200)
