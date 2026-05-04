"""Phase 6.2 — meshforge-maps systemd lifecycle control.

Three layers under test:

1. ``utils.meshforge_maps_lifecycle`` — the policy/wrapper module.
   Localhost gate, unit-existence pre-check, ``start`` / ``stop`` /
   ``restart`` thin wrappers around ``service_check``, and the
   ``UnitStatus`` / ``format_unit_status`` rendering helpers.

2. The Phase 6 menu — Phase 6.2 adds a fourth row ``mf_lifecycle`` and
   keeps the prior three Phase 6 + 6.3 rows untouched.

3. ``MeshforgeMapsHandler._lifecycle_menu`` — the sub-submenu flow.
   Refusal paths (remote endpoint, missing unit, undetectable systemctl),
   the start/stop/restart double-confirm dance, the post-action re-probe,
   and the read-only "show status" path.

The lifecycle wrappers themselves never spawn ``subprocess`` in these tests
— ``service_check.start_service`` / ``stop_service`` / ``restart_service``
are patched at the lifecycle module's import site so we exercise the policy
code without touching real systemd.
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils import meshforge_maps_lifecycle as lifecycle
from utils.meshforge_maps_lifecycle import (
    INSTALL_HINT_URL,
    LOCALHOST_ALIASES,
    LifecycleResult,
    SYSTEMD_UNIT,
    UnitStatus,
    format_unit_status,
    get_unit_status,
    is_localhost,
    is_unit_installed,
    restart,
    start,
    stop,
)
from utils.meshforge_maps_config import MapsConfig
from launcher_tui.handlers.meshforge_maps import MeshforgeMapsHandler


# ─────────────────────────────────────────────────────────────────────
# 1. Localhost gate
# ─────────────────────────────────────────────────────────────────────


class TestIsLocalhost:
    def test_plain_localhost(self):
        assert is_localhost("localhost") is True

    def test_ipv4_loopback(self):
        assert is_localhost("127.0.0.1") is True

    def test_ipv6_loopback(self):
        assert is_localhost("::1") is True

    def test_zero_zero_zero_zero_treated_as_local(self):
        # meshforge-maps systemd unit binds 0.0.0.0 by default, and a user
        # who pasted that into Phase 6.3's host field still expects to be
        # operating on this host. Lifecycle is a sudo-on-self operation.
        assert is_localhost("0.0.0.0") is True

    def test_remote_hostname_rejected(self):
        assert is_localhost("noc.example.org") is False

    def test_remote_lan_ip_rejected(self):
        assert is_localhost("192.168.1.50") is False

    def test_empty_string_rejected(self):
        assert is_localhost("") is False

    def test_case_insensitive(self):
        assert is_localhost("LOCALHOST") is True

    def test_strips_whitespace(self):
        assert is_localhost("  localhost  ") is True


# ─────────────────────────────────────────────────────────────────────
# 2. Unit-existence pre-check
# ─────────────────────────────────────────────────────────────────────


def _fake_proc(stdout: str = "", returncode: int = 0):
    cp = MagicMock()
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


class TestIsUnitInstalled:
    def test_installed_when_unit_present_in_listing(self):
        with patch.object(
            lifecycle.subprocess, "run",
            return_value=_fake_proc(
                stdout="UNIT FILE                STATE\n"
                       f"{SYSTEMD_UNIT}.service     enabled\n"
            ),
        ):
            installed, err = is_unit_installed()
        assert installed is True
        assert err is None

    def test_not_installed_when_unit_absent(self):
        with patch.object(
            lifecycle.subprocess, "run",
            return_value=_fake_proc(stdout="0 unit files listed.\n"),
        ):
            installed, err = is_unit_installed()
        assert installed is False
        assert err is None

    def test_systemctl_missing_treated_as_uncertain(self):
        with patch.object(
            lifecycle.subprocess, "run",
            side_effect=FileNotFoundError("no systemctl"),
        ):
            installed, err = is_unit_installed()
        assert installed is False
        assert err and "systemctl" in err

    def test_timeout_returns_error(self):
        with patch.object(
            lifecycle.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5),
        ):
            installed, err = is_unit_installed()
        assert installed is False
        assert err and "timeout" in err.lower()

    def test_oserror_returns_error(self):
        with patch.object(
            lifecycle.subprocess, "run",
            side_effect=OSError("permission denied"),
        ):
            installed, err = is_unit_installed()
        assert installed is False
        assert err and "could not list" in err.lower()


# ─────────────────────────────────────────────────────────────────────
# 3. UnitStatus aggregation
# ─────────────────────────────────────────────────────────────────────


class TestGetUnitStatus:
    def test_not_installed_short_circuits_to_safe_defaults(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(False, None)):
            status = get_unit_status()
        assert status.installed is False
        assert status.active is False
        assert status.enabled is False

    def test_install_check_error_propagates_into_unit_status(self):
        with patch.object(
            lifecycle, "is_unit_installed",
            return_value=(False, "systemctl not found"),
        ):
            status = get_unit_status()
        assert status.installed is False
        assert status.active is None
        assert status.enabled is None
        assert "systemctl" in (status.error or "")

    def test_active_and_enabled_inferred_from_systemctl_returncodes(self):
        # systemctl is-active / is-enabled return rc=0 when true, rc=3 when false.
        def fake_run(cmd, **kwargs):
            if "is-active" in cmd:
                return _fake_proc(returncode=0)
            if "is-enabled" in cmd:
                return _fake_proc(returncode=3)
            if "show" in cmd:
                return _fake_proc(stdout="SubState=running\n")
            return _fake_proc(returncode=0)

        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(lifecycle.subprocess, "run", side_effect=fake_run):
            status = get_unit_status()
        assert status.installed is True
        assert status.active is True
        assert status.enabled is False
        assert status.sub_state == "running"

    def test_subprocess_error_collapses_to_none(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(
                 lifecycle.subprocess, "run",
                 side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5),
             ):
            status = get_unit_status()
        assert status.installed is True
        assert status.active is None
        assert status.enabled is None
        assert status.sub_state is None


# ─────────────────────────────────────────────────────────────────────
# 4. Lifecycle wrappers (start / stop / restart) and gating
# ─────────────────────────────────────────────────────────────────────


class TestLifecycleStart:
    def test_remote_host_refused_without_calling_systemctl(self):
        with patch.object(lifecycle, "start_service") as mock_start:
            result = start(host="noc.example.org")
        assert isinstance(result, LifecycleResult)
        assert result.success is False
        assert result.action == "start"
        assert "local-only" in result.message
        mock_start.assert_not_called()

    def test_missing_unit_refused_without_calling_systemctl(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(False, None)), \
             patch.object(lifecycle, "start_service") as mock_start:
            result = start(host="localhost")
        assert result.success is False
        assert "not installed" in result.message
        assert INSTALL_HINT_URL in result.message
        mock_start.assert_not_called()

    def test_unit_detection_error_surfaces_in_result(self):
        with patch.object(
            lifecycle, "is_unit_installed",
            return_value=(False, "systemctl not found"),
        ), patch.object(lifecycle, "start_service") as mock_start:
            result = start(host="localhost")
        assert result.success is False
        assert "systemctl" in result.message
        mock_start.assert_not_called()

    def test_happy_path_invokes_start_service_with_unit_name(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(
                 lifecycle, "start_service",
                 return_value=(True, f"{SYSTEMD_UNIT} started"),
             ) as mock_start:
            result = start(host="localhost")
        assert result.success is True
        assert result.action == "start"
        assert SYSTEMD_UNIT in result.message
        mock_start.assert_called_once()
        call_args, _ = mock_start.call_args
        assert call_args[0] == SYSTEMD_UNIT

    def test_systemctl_failure_is_propagated_intact(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(
                 lifecycle, "start_service",
                 return_value=(False, "Unit meshforge-maps.service not found."),
             ):
            result = start(host="localhost")
        assert result.success is False
        assert "not found" in result.message


class TestLifecycleStop:
    def test_remote_host_refused(self):
        with patch.object(lifecycle, "stop_service") as mock_stop:
            result = stop(host="192.168.1.50")
        assert result.success is False
        assert result.action == "stop"
        mock_stop.assert_not_called()

    def test_happy_path_invokes_stop_service(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(
                 lifecycle, "stop_service",
                 return_value=(True, f"{SYSTEMD_UNIT} stopped"),
             ) as mock_stop:
            result = stop(host="localhost")
        assert result.success is True
        mock_stop.assert_called_once_with(SYSTEMD_UNIT, timeout=30)


class TestLifecycleRestart:
    def test_remote_host_refused(self):
        with patch.object(lifecycle, "restart_service") as mock_restart:
            result = restart(host="noc.example.org")
        assert result.success is False
        assert result.action == "restart"
        mock_restart.assert_not_called()

    def test_happy_path_invokes_restart_service(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(
                 lifecycle, "restart_service",
                 return_value=(True, f"{SYSTEMD_UNIT} restarted"),
             ) as mock_restart:
            result = restart(host="localhost")
        assert result.success is True
        mock_restart.assert_called_once_with(SYSTEMD_UNIT, timeout=30)

    def test_timeout_kwarg_threaded_through(self):
        with patch.object(lifecycle, "is_unit_installed", return_value=(True, None)), \
             patch.object(
                 lifecycle, "restart_service", return_value=(True, "ok"),
             ) as mock_restart:
            restart(host="localhost", timeout=12)
        mock_restart.assert_called_once_with(SYSTEMD_UNIT, timeout=12)


# ─────────────────────────────────────────────────────────────────────
# 5. format_unit_status rendering
# ─────────────────────────────────────────────────────────────────────


class TestFormatUnitStatus:
    def test_not_installed_renders_install_hint(self):
        out = format_unit_status(UnitStatus(installed=False, active=False, enabled=False))
        assert "NOT INSTALLED" in out
        assert INSTALL_HINT_URL in out

    def test_unknown_when_install_check_failed(self):
        out = format_unit_status(
            UnitStatus(installed=False, active=None, enabled=None, error="systemctl not found"),
        )
        assert "UNKNOWN" in out
        assert "systemctl not found" in out

    def test_active_status_rendering(self):
        out = format_unit_status(
            UnitStatus(installed=True, active=True, enabled=True, sub_state="running"),
        )
        assert "ACTIVE" in out
        assert "Enabled at boot: enabled" in out
        assert "SubState:        running" in out

    def test_inactive_status_rendering(self):
        out = format_unit_status(
            UnitStatus(installed=True, active=False, enabled=False),
        )
        assert "INACTIVE" in out
        assert "disabled" in out
        # No SubState line when None
        assert "SubState" not in out

    def test_unknown_active_when_systemctl_show_failed(self):
        out = format_unit_status(
            UnitStatus(installed=True, active=None, enabled=None),
        )
        # 'unknown' appears for both fields
        assert "UNKNOWN" in out
        assert "unknown" in out


# ─────────────────────────────────────────────────────────────────────
# 6. Handler menu shape (Phase 6.2 row added)
# ─────────────────────────────────────────────────────────────────────


class TestMenuItemsIncludeLifecycleRow:
    def test_lifecycle_row_present(self):
        h = MeshforgeMapsHandler()
        keys = [item[0] for item in h.menu_items()]
        assert "mf_lifecycle" in keys

    def test_phase6_and_6_3_keys_preserved(self):
        h = MeshforgeMapsHandler()
        keys = [item[0] for item in h.menu_items()]
        for required in ("mf_status", "mf_open", "mf_endpoint"):
            assert required in keys

    def test_lifecycle_row_unflagged(self):
        h = MeshforgeMapsHandler()
        items = {item[0]: item for item in h.menu_items()}
        # Section-level "maps" gating handles flagging — per-row stays None.
        assert items["mf_lifecycle"][2] is None

    def test_execute_dispatches_lifecycle(self):
        h = MeshforgeMapsHandler()
        ctx = MagicMock()
        h.ctx = ctx
        h.execute("mf_lifecycle")
        ctx.safe_call.assert_called_once()
        args = ctx.safe_call.call_args.args
        assert args[0] == "Maps Lifecycle Control"
        assert args[1] == h._lifecycle_menu


# ─────────────────────────────────────────────────────────────────────
# 7. Lifecycle menu refusal paths
# ─────────────────────────────────────────────────────────────────────


def _make_handler_with_dialog():
    """Wire a MeshforgeMapsHandler with a MagicMock ctx + dialog."""
    h = MeshforgeMapsHandler()
    ctx = MagicMock()
    ctx.dialog = MagicMock()
    h.ctx = ctx
    return h, ctx


class TestLifecycleMenuRefusals:
    def test_remote_endpoint_refused_with_msgbox_no_action(self):
        h, ctx = _make_handler_with_dialog()
        cfg = MapsConfig(host="noc.example.org", port=8808, timeout=3.0)
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=cfg,
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed"
        ) as mock_check, patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_start"
        ) as mock_start:
            h._lifecycle_menu()

        # Refusal renders a single msgbox and never reaches the unit check
        # or the start helper.
        ctx.dialog.msgbox.assert_called_once()
        title, body = ctx.dialog.msgbox.call_args.args
        assert "Lifecycle Unavailable" in title
        assert "noc.example.org" in body
        mock_check.assert_not_called()
        mock_start.assert_not_called()
        ctx.dialog.menu.assert_not_called()

    def test_missing_unit_refused_with_install_hint(self):
        h, ctx = _make_handler_with_dialog()
        cfg = MapsConfig(host="localhost", port=8808, timeout=3.0)
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=cfg,
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(False, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_start"
        ) as mock_start:
            h._lifecycle_menu()

        ctx.dialog.msgbox.assert_called_once()
        title, body = ctx.dialog.msgbox.call_args.args
        assert "Not Installed" in title
        assert INSTALL_HINT_URL in body
        mock_start.assert_not_called()
        ctx.dialog.menu.assert_not_called()

    def test_unit_detection_error_refused_with_helpful_message(self):
        h, ctx = _make_handler_with_dialog()
        cfg = MapsConfig(host="localhost", port=8808, timeout=3.0)
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=cfg,
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(False, "systemctl not found"),
        ):
            h._lifecycle_menu()

        ctx.dialog.msgbox.assert_called_once()
        title, body = ctx.dialog.msgbox.call_args.args
        assert "Cannot Detect Unit" in title
        assert "systemctl not found" in body
        ctx.dialog.menu.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# 8. Lifecycle menu happy path — confirm + action + re-probe
# ─────────────────────────────────────────────────────────────────────


def _localhost_cfg():
    return MapsConfig(host="localhost", port=8808, timeout=3.0)


def _make_unit_status(active: bool = True) -> UnitStatus:
    return UnitStatus(
        installed=True, active=active, enabled=True, sub_state="running" if active else "dead",
    )


class TestLifecycleActionFlow:
    def test_start_confirmed_invokes_lifecycle_start_then_reprobes(self):
        h, ctx = _make_handler_with_dialog()
        cfg = _localhost_cfg()

        # Simulate the dialog flow: menu picks "start", confirm picks YES,
        # then the post-action menu picks "back".
        ctx.dialog.menu.side_effect = ["start", "back"]
        ctx.dialog.yesno.return_value = True

        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=cfg,
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(active=True),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_start",
            return_value=LifecycleResult(success=True, action="start", message="ok"),
        ) as mock_start:
            h._lifecycle_menu()

        mock_start.assert_called_once_with(host="localhost")
        # Confirm dialog must default to NO so accidental Enter doesn't fire.
        kwargs = ctx.dialog.yesno.call_args.kwargs
        assert kwargs.get("default_no") is True
        # Result msgbox shows post-action state.
        titles = [c.args[0] for c in ctx.dialog.msgbox.call_args_list]
        assert any("Succeeded" in t for t in titles)

    def test_start_aborted_at_confirm_does_not_call_lifecycle(self):
        h, ctx = _make_handler_with_dialog()
        ctx.dialog.menu.side_effect = ["start", "back"]
        ctx.dialog.yesno.return_value = False  # User says NO
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=_localhost_cfg(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_start",
        ) as mock_start:
            h._lifecycle_menu()
        mock_start.assert_not_called()
        # No success/fail msgbox either — abort is silent.
        assert ctx.dialog.msgbox.call_count == 0

    def test_stop_confirmed_invokes_lifecycle_stop(self):
        h, ctx = _make_handler_with_dialog()
        ctx.dialog.menu.side_effect = ["stop", "back"]
        ctx.dialog.yesno.return_value = True
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=_localhost_cfg(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(active=False),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_stop",
            return_value=LifecycleResult(success=True, action="stop", message="ok"),
        ) as mock_stop:
            h._lifecycle_menu()
        mock_stop.assert_called_once_with(host="localhost")

    def test_restart_confirmed_invokes_lifecycle_restart(self):
        h, ctx = _make_handler_with_dialog()
        ctx.dialog.menu.side_effect = ["restart", "back"]
        ctx.dialog.yesno.return_value = True
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=_localhost_cfg(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_restart",
            return_value=LifecycleResult(success=True, action="restart", message="ok"),
        ) as mock_restart:
            h._lifecycle_menu()
        mock_restart.assert_called_once_with(host="localhost")

    def test_lifecycle_failure_renders_failed_msgbox(self):
        h, ctx = _make_handler_with_dialog()
        ctx.dialog.menu.side_effect = ["start", "back"]
        ctx.dialog.yesno.return_value = True
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=_localhost_cfg(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_start",
            return_value=LifecycleResult(
                success=False, action="start",
                message="Job for meshforge-maps.service failed",
            ),
        ):
            h._lifecycle_menu()
        titles = [c.args[0] for c in ctx.dialog.msgbox.call_args_list]
        assert any("Failed" in t for t in titles)

    def test_show_status_renders_unit_status_msgbox(self):
        h, ctx = _make_handler_with_dialog()
        ctx.dialog.menu.side_effect = ["status", "back"]
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=_localhost_cfg(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(active=True),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.lifecycle_start",
        ) as mock_start:
            h._lifecycle_menu()
        # Status-only path never confirms or starts anything.
        ctx.dialog.yesno.assert_not_called()
        mock_start.assert_not_called()
        # And renders one msgbox titled with the unit name.
        titles = [c.args[0] for c in ctx.dialog.msgbox.call_args_list]
        assert any(SYSTEMD_UNIT in t for t in titles)

    def test_back_exits_loop_without_action(self):
        h, ctx = _make_handler_with_dialog()
        ctx.dialog.menu.return_value = "back"
        with patch(
            "launcher_tui.handlers.meshforge_maps.load_maps_config",
            return_value=_localhost_cfg(),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.is_unit_installed",
            return_value=(True, None),
        ), patch(
            "launcher_tui.handlers.meshforge_maps.get_unit_status",
            return_value=_make_unit_status(),
        ):
            h._lifecycle_menu()
        ctx.dialog.yesno.assert_not_called()
        ctx.dialog.msgbox.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# 9. Module-level constants sanity
# ─────────────────────────────────────────────────────────────────────


class TestModuleConstants:
    def test_systemd_unit_name(self):
        assert SYSTEMD_UNIT == "meshforge-maps"

    def test_localhost_aliases_complete(self):
        for alias in ("localhost", "127.0.0.1", "::1"):
            assert alias in LOCALHOST_ALIASES

    def test_install_hint_points_to_meshforge_maps_repo(self):
        assert "meshforge-maps" in INSTALL_HINT_URL
