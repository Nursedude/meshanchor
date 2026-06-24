"""Tests for the Fleet Architecture (fleet_provision) TUI handler + its pure
core. The core takes provision_role as an injected `mod`, so these never touch
real systemd; the handler is driven through a FakeDialog.

Run: python3 -m pytest tests/test_fleet_provision_handler.py -v
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
from handlers import _fleet_provision_core as core    # noqa: E402
from handlers.fleet_provision import FleetProvisionHandler  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class FakeAction:
    def __init__(self, verb, item, current="x/y", desired="enabled", detail="",
                 fail=False):
        self.verb = verb
        self.item = item
        self.current = current
        self.desired = desired
        self.detail = detail
        self.result = ""
        self._fail = fail  # apply_action stub flips this to a failure


def _stub_mod(role="collector", overrides=None, actions=None,
              write_role_raises=False):
    """A stand-in for scripts/provision_role.py — no systemd, no files.

    Records apply side-effects so apply_preset tests can assert WITHOUT touching
    real systemd/deployment.json: ``mod.write_role_calls`` + ``mod.applied``.
    """
    mod = types.SimpleNamespace()
    mod.DEFAULT_ROLES_FILE = "/dev/null"
    mod.read_role = lambda: role
    mod.read_overrides = lambda: (overrides or {})
    mod.load_roles = lambda path: {"roles": {
        "collector": {}, "radio-gateway": {}, "radio-node": {},
        "noc": {"singleton": True},
    }}
    mod.resolve_role = lambda catalog, r: {"services": {}}
    mod.plan = lambda role_def, ov: (actions if actions is not None else [])

    mod.write_role_calls = []

    def _write_role(r):
        if write_role_raises:
            raise OSError("disk full")
        mod.write_role_calls.append(r)
    mod.write_role = _write_role

    mod.applied = []

    def _apply_action(a):
        a.result = "boom" if getattr(a, "_fail", False) else "ok"
        mod.applied.append(a)
        return not getattr(a, "_fail", False)
    mod.apply_action = _apply_action
    return mod


@pytest.fixture(scope="module")
def presets_doc():
    return core.load_presets(core.presets_path(REPO))


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------
class TestCore:
    def test_load_presets_real_catalog(self, presets_doc):
        assert "meshcore-bridge" in presets_doc["presets"]
        assert "legs" in presets_doc

    def test_gateway_overlay_meshcore_bridge(self, presets_doc):
        # MeshAnchor's bridge is a single bridge_mode enum (MeshCore-primary).
        overlay = core.gateway_overlay_for("meshcore-bridge", presets_doc)
        assert overlay.get("bridge_mode") == "meshcore_bridge"

    def test_gateway_overlay_non_bridge_is_empty(self, presets_doc):
        assert core.gateway_overlay_for("monitor", presets_doc) == {}
        assert core.gateway_overlay_for("noc-server", presets_doc) == {}

    def test_preview_preset_filters_to_change_verbs(self, presets_doc):
        mod = _stub_mod(actions=[
            FakeAction("enable", "meshanchor-daemon"),
            FakeAction("noop", "rnsd"),
        ])
        prev = core.preview_preset(mod, "meshcore-bridge", presets_doc, {})
        assert prev["role"] == "radio-gateway"
        # noop filtered out — only the real change remains
        assert [a.item for a in prev["actions"]] == ["meshanchor-daemon"]
        assert prev["gateway_overlay"].get("bridge_mode") == "meshcore_bridge"

    def test_current_box_reports_drift(self, presets_doc):
        mod = _stub_mod(role="collector", actions=[
            FakeAction("disable", "meshanchor-daemon"),
            FakeAction("noop", "meshforge-map"),
        ])
        info = core.current_box(mod)
        assert info["role"] == "collector"
        assert [a.item for a in info["drift"]] == ["meshanchor-daemon"]

    def test_current_box_no_role_is_unknown_not_clean(self):
        """No role set → drift must be None (UNKNOWN), never [] (which the UI
        would render as 'no drift')."""
        info = core.current_box(_stub_mod(role=None))
        assert info["drift"] is None


# ---------------------------------------------------------------------------
# Handler (driven through FakeDialog; provision_role stubbed)
# ---------------------------------------------------------------------------
class TestHandler:
    def test_menu_items_exposes_entry(self):
        items = FleetProvisionHandler().menu_items()
        assert items and items[0][0] == "fleet_provision"

    def test_registered_in_get_all_handlers(self):
        from handlers import get_all_handlers
        assert FleetProvisionHandler in get_all_handlers()

    def test_main_menu_renders_current_box(self, monkeypatch, presets_doc):
        monkeypatch.setattr(core, "load_provision_role",
                            lambda *a, **k: _stub_mod(
                                role="radio-gateway",
                                actions=[FakeAction("enable", "meshanchor-daemon")]))
        monkeypatch.setattr(core, "load_presets", lambda *a, **k: presets_doc)
        ctx = make_handler_context()
        ctx.dialog._menu_returns = ["current", "back"]
        h = FleetProvisionHandler()
        h.set_context(ctx)
        h._main_menu()
        textboxes = [c for c in ctx.dialog.calls if c[0] == "msgbox"]
        assert textboxes, "expected a textbox render"
        assert "Declared role" in textboxes[-1][1][1]

    def test_preview_renders_dry_run_and_overlay(self, monkeypatch, presets_doc):
        # Pin viewer mode so no apply menu is offered (deterministic regardless
        # of the test process euid).
        monkeypatch.setattr("os.geteuid", lambda: 1000)
        monkeypatch.setattr(core, "load_provision_role",
                            lambda *a, **k: _stub_mod(
                                role="collector",
                                actions=[FakeAction("enable", "meshanchor-daemon")]))
        monkeypatch.setattr(core, "load_presets", lambda *a, **k: presets_doc)
        ctx = make_handler_context()
        # main: pick catalog; catalog: pick meshcore-bridge then back; main: back
        ctx.dialog._menu_returns = ["catalog", "meshcore-bridge", "back", "back"]
        h = FleetProvisionHandler()
        h.set_context(ctx)
        h._main_menu()
        textboxes = [c for c in ctx.dialog.calls if c[0] == "msgbox"]
        assert textboxes, "expected a preview textbox"
        body = textboxes[-1][1][1]
        assert "PREVIEW" in body  # still a preview; apply is a separate step
        assert "bridge_mode" in body  # the gateway.json leg overlay is rendered


# ---------------------------------------------------------------------------
# Apply core (provision_role injected — no real systemd / deployment.json)
# ---------------------------------------------------------------------------
class TestApplyCore:
    def test_apply_writes_role_then_applies_changes(self, presets_doc):
        mod = _stub_mod(actions=[
            FakeAction("enable", "meshanchor-daemon"),
            FakeAction("disable", "meshforge-map"),
            FakeAction("noop", "rnsd"),  # filtered out — never applied
        ])
        res = core.apply_preset(mod, "meshcore-bridge", presets_doc, {})
        assert res["ok"] is True
        assert res["role"] == "radio-gateway"
        assert mod.write_role_calls == ["radio-gateway"]
        # only the two change verbs were applied, in order; noop never touched
        assert [a.item for a in mod.applied] == ["meshanchor-daemon", "meshforge-map"]
        assert [r["item"] for r in res["results"]] == ["meshanchor-daemon", "meshforge-map"]
        assert res["failures"] == []

    def test_apply_role_write_failure_short_circuits(self, presets_doc):
        """write_role raising must abort BEFORE any unit is touched — never
        converge units toward a role the box does not record (hfm #4)."""
        mod = _stub_mod(actions=[FakeAction("enable", "meshanchor-daemon")],
                        write_role_raises=True)
        res = core.apply_preset(mod, "meshcore-bridge", presets_doc, {})
        assert res["ok"] is False
        assert res["role_written"] is False
        assert "disk full" in res["role_err"]
        assert mod.applied == []      # no unit action attempted
        assert res["results"] == []

    def test_apply_unit_failure_reported_not_swallowed(self, presets_doc):
        mod = _stub_mod(actions=[
            FakeAction("enable", "meshanchor-daemon", fail=True),
            FakeAction("disable", "meshforge-map"),
        ])
        res = core.apply_preset(mod, "meshcore-bridge", presets_doc, {})
        assert res["ok"] is False
        assert res["role_written"] is True            # role still recorded
        assert [r["item"] for r in res["failures"]] == ["meshanchor-daemon"]
        # the second action still ran (we don't abort the whole converge on one fail)
        assert [a.item for a in mod.applied] == ["meshanchor-daemon", "meshforge-map"]

    def test_apply_no_changes_is_success(self, presets_doc):
        """An empty change set with the role written is a legitimate success
        (idempotent re-apply), not a failure or an ambiguous state."""
        mod = _stub_mod(actions=[FakeAction("noop", "rnsd")])
        res = core.apply_preset(mod, "monitor", presets_doc, {})
        assert res["ok"] is True
        assert res["results"] == []
        assert mod.write_role_calls == ["collector"]


# ---------------------------------------------------------------------------
# Apply handler (admin gate + confirm-after-dry-run + honest report)
# ---------------------------------------------------------------------------
class TestApplyHandler:
    def _ctx_handler(self):
        ctx = make_handler_context()
        h = FleetProvisionHandler()
        h.set_context(ctx)
        return ctx, h

    def test_apply_refused_in_viewer_mode(self, monkeypatch, presets_doc):
        monkeypatch.setattr("os.geteuid", lambda: 1000)
        mod = _stub_mod(actions=[FakeAction("enable", "meshanchor-daemon")])
        ctx, h = self._ctx_handler()
        h._apply_flow(mod, "meshcore-bridge", presets_doc)
        # refusal msgbox, and absolutely no apply happened
        assert ctx.dialog.last_msgbox_title == "Apply Preset"
        assert "admin" in ctx.dialog.last_msgbox_text.lower()
        assert mod.write_role_calls == []
        assert mod.applied == []

    def test_apply_confirm_no_cancels(self, monkeypatch, presets_doc):
        monkeypatch.setattr("os.geteuid", lambda: 0)
        mod = _stub_mod(actions=[FakeAction("enable", "meshanchor-daemon")])
        ctx, h = self._ctx_handler()
        ctx.dialog._yesno_returns = [False]   # operator declines
        h._apply_flow(mod, "meshcore-bridge", presets_doc)
        assert "ancel" in ctx.dialog.last_msgbox_text  # "Cancelled"
        assert mod.write_role_calls == []
        assert mod.applied == []

    def test_apply_confirm_yes_applies_and_reports(self, monkeypatch, presets_doc):
        monkeypatch.setattr("os.geteuid", lambda: 0)
        mod = _stub_mod(actions=[FakeAction("enable", "meshanchor-daemon")])
        ctx, h = self._ctx_handler()
        ctx.dialog._yesno_returns = [True]    # operator confirms
        h._apply_flow(mod, "meshcore-bridge", presets_doc)
        assert mod.write_role_calls == ["radio-gateway"]
        assert [a.item for a in mod.applied] == ["meshanchor-daemon"]
        # honest success report shown
        assert "Preset Applied" in (ctx.dialog.last_msgbox_title or "")

    def test_apply_reports_failure_honestly(self, monkeypatch, presets_doc):
        monkeypatch.setattr("os.geteuid", lambda: 0)
        mod = _stub_mod(actions=[
            FakeAction("enable", "meshanchor-daemon", fail=True)])
        ctx, h = self._ctx_handler()
        ctx.dialog._yesno_returns = [True]
        h._apply_flow(mod, "meshcore-bridge", presets_doc)
        # a failed apply must NOT show a success title
        assert "Applied" not in (ctx.dialog.last_msgbox_title or "")
        assert "incomplete" in (ctx.dialog.last_msgbox_title or "").lower()
