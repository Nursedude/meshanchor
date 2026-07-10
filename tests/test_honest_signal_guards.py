"""Honest-signal guard suite (Issues #74-#77; ported from MeshForge 2026-06-08).

The TUI must not show a hardcoded success for an action whose result was never
checked — it works, or it says exactly how it didn't. MeshAnchor carries the
same TUI-handler lineage as MeshForge, so it carries the same defect class; this
is the regression home for it here.

  * TestApplyConfigRestartReturnChecked — no handler discards
    apply_config_and_restart()'s (ok, msg) (the MF020 contract).
  * TestRnsRestartReturnChecked — rns_interfaces.py binds stop/start_service()
    so a failed rnsd restart can't read as success (S3 item 1).
  * TestPortConflictVerifyBeforeDone — diagnose_rns_port_conflict() verifies the
    shared instance is up before printing "Done." (S3 item 2).
  * TestReportActionHelper — the shared confirm-or-honest dialog primitive.
  * TestMF020LintRule — the lint rule fires on the bad shape, stays quiet on
    the honest one and outside the handler tree.
  * TestSdrMockProvenance / TestChannelPskProvenance / TestTrafficDemoProvenance
    — S6 fabricated-data labeling: simulated/demo output must carry visible
    provenance (MOCK-MODE banner, honest PSK classification, SAMPLE-DATA note)
    so a HAM never reads np.random noise as real RF and a security audit never
    sees a false encryption verdict. (Skip gracefully if a handler file is
    absent so the suite tolerates MeshAnchor's divergent tree.)
  * TestSwallowedErrorTailS7 — S7 false-clean swallowed-error tail: a failed
    read in a status surface must render "(status unavailable: …)" not vanish,
    and the RNS health probe must not hardcode the shared-instance name.
"""
import os
import re
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLERS_DIR = REPO_ROOT / "src" / "launcher_tui" / "handlers"

sys.path.insert(0, str(REPO_ROOT / "src" / "launcher_tui"))
sys.path.insert(0, str(REPO_ROOT / "src"))

_lint_path = REPO_ROOT / "scripts" / "lint.py"
_spec = importlib.util.spec_from_file_location("lint", _lint_path)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)

_BARE_APPLY = re.compile(r'^_?apply_config_and_restart\s*\(')

# Statement-start service-control call (return discarded). Scoped per-file, not
# handler-wide: handler sites legitimately fire-and-forget a stop before a
# checked start, and others belong to later burn-down slices.
_BARE_SVC = re.compile(r'^(?:stop|start|restart)_service\s*\(')


class TestApplyConfigRestartReturnChecked:
    """apply_config_and_restart() returns (success, msg) precisely so callers
    surface a failed restart. A bare-statement call drops it and shows a
    hardcoded "restarted" even when the daemon stayed down (#74-#77)."""

    def test_no_bare_apply_config_and_restart_in_handlers(self):
        violations = []
        for root, _dirs, files in os.walk(HANDLERS_DIR):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                fp = Path(root) / fn
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for n, line in enumerate(f, 1):
                        s = line.strip()
                        if s.startswith("#"):
                            continue
                        if _BARE_APPLY.match(s):
                            violations.append(f"{fp.relative_to(REPO_ROOT)}:{n}")
        assert not violations, (
            "apply_config_and_restart() return discarded (MF020 / honest-signal "
            "#74-#77) — bind 'ok, msg = ...' and surface restart failure:\n  "
            + "\n  ".join(violations)
        )


class TestRnsRestartReturnChecked:
    """S3 item 1 (#74-#77): rns_interfaces._fix_rns_ownership restarts rnsd after
    a permission fix. The stop/start_service returns must be bound so a daemon
    left stopped never reads "rnsd restarted" (mirrors rns_monitor.py:149-153).
    Scoped to rns_interfaces.py — the broad *_service sweep is later slices."""

    def test_no_bare_service_control_in_rns_interfaces(self):
        fp = HANDLERS_DIR / "rns_interfaces.py"
        violations = []
        with open(fp, encoding="utf-8", errors="ignore") as f:
            for n, line in enumerate(f, 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if _BARE_SVC.match(s):
                    violations.append(f"rns_interfaces.py:{n}: {s}")
        assert not violations, (
            "stop/start_service() return discarded in rns_interfaces.py "
            "(honest-signal #74-#77) — bind 'ok, msg = ...' and gate the "
            "'restarted' message on the start result:\n  "
            + "\n  ".join(violations)
        )


class TestPortConflictVerifyBeforeDone:
    """S3 item 2 (#74-#77): _rns_diagnostics_engine.diagnose_rns_port_conflict()
    must confirm rnsd actually claimed the shared instance
    (handler._wait_for_rns_shared_instance) before printing 'Done.' — starting
    rnsd != resolving the port conflict, and the discarded start_service() return
    hid a failed start. Behavioral (not a source scan: the sibling stop_service
    is a legitimate stop-then-pkill path)."""

    def _run(self, monkeypatch, capsys, *, start_ok, instance_up):
        import handlers._rns_diagnostics_engine as eng
        # Isolate side effects: replace the module's subprocess/time refs so the
        # real pkill never fires and sleeps are no-ops (real modules untouched).
        monkeypatch.setattr(eng, "subprocess", MagicMock())
        monkeypatch.setattr(eng, "time", MagicMock())
        monkeypatch.setattr(eng, "start_service", lambda name: (start_ok, "boom"))
        handler = MagicMock()
        handler._check_lxmf_app_conflict.return_value = "NomadNet"
        handler.ctx.dialog.yesno.return_value = True
        handler._wait_for_rns_shared_instance.return_value = instance_up
        eng.diagnose_rns_port_conflict(handler)
        return capsys.readouterr().out, handler

    def test_done_only_when_instance_verified(self, monkeypatch, capsys):
        out, _ = self._run(monkeypatch, capsys, start_ok=True, instance_up=True)
        assert "Done." in out

    def test_no_done_when_instance_never_up(self, monkeypatch, capsys):
        out, _ = self._run(monkeypatch, capsys, start_ok=True, instance_up=False)
        assert "Done." not in out
        assert "NOT available" in out

    def test_no_done_and_no_verify_when_start_fails(self, monkeypatch, capsys):
        out, handler = self._run(monkeypatch, capsys, start_ok=False, instance_up=True)
        assert "Done." not in out
        assert "FAILED to start rnsd" in out
        # Never started → never claim to have verified.
        handler._wait_for_rns_shared_instance.assert_not_called()


class TestReportActionHelper:
    """TUIContext.report_action — the shared confirm-or-honest dialog primitive."""

    def _ctx(self):
        from handler_protocol import TUIContext
        return TUIContext(dialog=MagicMock())

    def test_success_shows_success_dialog_and_returns_true(self):
        ctx = self._ctx()
        assert ctx.report_action(True, "Applied", "did it") is True
        ctx.dialog.msgbox.assert_called_once_with("Applied", "did it")

    def test_failure_shows_failure_dialog_and_returns_false(self):
        ctx = self._ctx()
        assert ctx.report_action(False, "Applied", "did it", "Restart Failed", "nope") is False
        ctx.dialog.msgbox.assert_called_once_with("Restart Failed", "nope")

    def test_failure_default_title_and_body(self):
        ctx = self._ctx()
        ctx.report_action(False, "Applied", "did it")
        title, body = ctx.dialog.msgbox.call_args[0]
        assert title == "Action Failed"
        assert "did not complete" in body

    def test_truthiness_is_coerced_to_bool(self):
        ctx = self._ctx()
        assert ctx.report_action(0, "Applied", "did it") is False
        assert ctx.report_action(1, "Applied", "did it") is True


class TestMF020LintRule:
    """MF020: fire on a discarded apply_config_and_restart() in a TUI handler;
    stay quiet on the honest bound form and outside the handler tree."""

    def _handler_file(self, tmp_path: Path, body: str) -> Path:
        d = tmp_path / "src" / "launcher_tui" / "handlers"
        d.mkdir(parents=True)
        fp = d / "fake_handler.py"
        fp.write_text(body)
        return fp

    def _mf020(self, issues):
        return [i for i in issues if i.code == "MF020"]

    def test_fires_on_bare_call(self, tmp_path):
        fp = self._handler_file(
            tmp_path, "def go(self):\n    apply_config_and_restart('meshtasticd')\n")
        assert self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))

    def test_fires_on_aliased_bare_call(self, tmp_path):
        fp = self._handler_file(
            tmp_path, "def go(self):\n    _apply_config_and_restart('meshtasticd')\n")
        assert self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))

    def test_quiet_when_result_is_bound(self, tmp_path):
        fp = self._handler_file(
            tmp_path,
            "def go(self):\n    ok, msg = apply_config_and_restart('meshtasticd')\n"
            "    self.ctx.report_action(ok, 'A', 'b', 'C', msg)\n")
        assert not self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))

    def test_quiet_outside_handler_tree(self, tmp_path):
        d = tmp_path / "src" / "utils"
        d.mkdir(parents=True)
        fp = d / "elsewhere.py"
        fp.write_text("def go():\n    apply_config_and_restart('meshtasticd')\n")
        assert not self._mf020(lint.MeshAnchorLinter().lint_file(str(fp)))


# --- S6: fabricated-data provenance (mock/demo paths must be labeled) ---

SDR_HANDLER = HANDLERS_DIR / "sdr.py"
CHANNEL_HANDLER = HANDLERS_DIR / "channel_config.py"
TRAFFIC_HANDLER = HANDLERS_DIR / "traffic_inspector.py"


class _FakeBackend:
    def __init__(self, name):
        self.name = name


class _FakeRF:
    """Minimal stand-in for RFAwareness: only .backend.name is read by the banner."""
    def __init__(self, name):
        self.backend = _FakeBackend(name)


class TestSdrMockProvenance:
    """S6 (#74-#77): in the MOCK backend every SDR measurement is np.random noise
    (utils.rf_awareness.MockSDR.receive_samples). Each measurement surface must
    carry a provenance banner so a HAM never reads simulated dBm as real RF."""

    def _cls(self):
        if not SDR_HANDLER.exists():
            pytest.skip("sdr.py not present in this repo")
        from handlers.sdr import SDRHandler
        return SDRHandler

    def test_banner_fires_in_mock_backend(self):
        banner = self._cls()._mock_banner(_FakeRF("MOCK"))
        assert "MOCK MODE" in banner and "SIMULATED" in banner

    def test_banner_silent_on_real_backend_and_none(self):
        cls = self._cls()
        assert cls._mock_banner(_FakeRF("SOAPY")) == ""
        assert cls._mock_banner(None) == ""

    def test_all_measurement_surfaces_carry_banner(self):
        if not SDR_HANDLER.exists():
            pytest.skip("sdr.py not present in this repo")
        src = SDR_HANDLER.read_text(encoding="utf-8")
        # spectrum / waterfall / utilization / survey / interference — 5 surfaces.
        assert src.count("self._mock_banner(rf)") >= 5, (
            "an SDR measurement surface is missing its MOCK-MODE provenance banner (S6)"
        )

    def test_gain_message_gated_on_set_gain_return(self):
        if not SDR_HANDLER.exists():
            pytest.skip("sdr.py not present in this repo")
        src = SDR_HANDLER.read_text(encoding="utf-8")
        # "Gain set to" must live under the set_gain() truth, not fire regardless.
        assert "elif rf.set_gain(gain):" in src, (
            "_rf_settings must gate 'Gain set' on set_gain()'s return (S6)"
        )


class TestChannelPskProvenance:
    """S6 (#74-#77): the channel PSK column must reflect THIS channel's psk
    field, not a whole-output substring scan (which gave a false encryption
    verdict — wrong for a security audit)."""

    def _cls(self):
        if not CHANNEL_HANDLER.exists():
            pytest.skip("channel_config.py not present in this repo")
        from handlers.channel_config import ChannelConfigHandler
        return ChannelConfigHandler

    def test_classify_psk_tokens(self):
        cls = self._cls()
        assert cls._classify_psk("none") == "None"
        assert cls._classify_psk("unset") == "None"
        assert cls._classify_psk("AQ==") == "Default"      # well-known default key
        assert cls._classify_psk('"AQ=="') == "Default"    # quoted/dict-style dump
        assert cls._classify_psk("aB3xK9p2Qz==") == "Set"  # a real-looking key

    def test_unknown_is_question_not_false_none(self):
        cls = self._cls()
        # the honest contract: when we can't parse it, say '?' not 'None'.
        assert cls._classify_psk("") == "?"
        assert cls._classify_psk(None) == "?"

    def test_no_whole_output_substring_idiom(self):
        if not CHANNEL_HANDLER.exists():
            pytest.skip("channel_config.py not present in this repo")
        src = CHANNEL_HANDLER.read_text(encoding="utf-8")
        assert "'none' not in raw.lower()" not in src, (
            "channel PSK must not be derived from a whole-output substring scan "
            "(false encryption verdict — S6)"
        )


class TestTrafficDemoProvenance:
    """S6 (#74-#77): the HTML path view falls back to demo hops with fabricated
    SNR/RSSI when no real paths exist. The final 'generated' dialog must label
    that as sample data, not just the dismissable prompt."""

    def test_demo_path_labels_final_dialog(self):
        if not TRAFFIC_HANDLER.exists():
            pytest.skip("traffic_inspector.py not present in this repo")
        src = TRAFFIC_HANDLER.read_text(encoding="utf-8")
        assert "used_demo" in src and "SAMPLE DATA" in src, (
            "demo path visualization must carry a SAMPLE DATA provenance note "
            "into the final dialog (S6)"
        )


# --- S7: false-clean swallowed-error tail (status reads must not vanish) ---

GATEWAY_HANDLER = HANDLERS_DIR / "gateway.py"
MESHCORE_HANDLER = HANDLERS_DIR / "meshcore.py"
UPDATES_HANDLER = HANDLERS_DIR / "updates.py"
NOMADNET_HANDLER = HANDLERS_DIR / "nomadnet.py"
NOMADNET_CHECKS = HANDLERS_DIR / "_nomadnet_rns_checks.py"


def _src_or_skip(fp: Path) -> str:
    if not fp.exists():
        pytest.skip(f"{fp.name} not present in this repo")
    return fp.read_text(encoding="utf-8")


class TestSwallowedErrorTailS7:
    """S7 (#74-#77): the last Thread-1 slice. A failed read in a status surface
    must render '(status unavailable: …)' rather than silently vanish (an empty
    section reads as 'all clear'); and the RNS pre-launch health probe must not
    hardcode the shared-instance socket name. Static source guards (zero-FP,
    skip-if-absent so they port to MeshAnchor's divergent tree)."""

    def test_gateway_breaker_read_failure_surfaces(self):
        src = _src_or_skip(GATEWAY_HANDLER)
        assert "CIRCUIT BREAKERS: (status unavailable" in src, (
            "a failed circuit-breaker read must render '(status unavailable: …)' "
            "in _show_gateway_status, not an empty section that reads as "
            "'no open breakers' (S7)"
        )

    def test_meshcore_subtitle_distinguishes_read_failure(self):
        src = _src_or_skip(MESHCORE_HANDLER)
        assert "MeshCore: status unavailable" in src, (
            "_meshcore_status_line must not let a config-read failure masquerade "
            "as the neutral 'feature unavailable' subtitle (S7)"
        )

    def test_updates_service_step_failure_surfaces(self):
        src = _src_or_skip(UPDATES_HANDLER)
        # 2026-07-10 self-update redesign: the unit-file step returns
        # (ok, detail) into the per-step completion report — a failure becomes
        # a [FAIL] line in the dialog instead of an inline "(service update
        # error:" note. Pin the new mechanism.
        assert "_refresh_service_files" in src and "unit-file refresh error" in src, (
            "the unit-file refresh step must surface an unexpected failure into "
            "the per-step completion report, not pass so the update reads clean (S7)"
        )

    def test_nomadnet_storage_prep_failure_surfaced(self):
        src = _src_or_skip(NOMADNET_HANDLER)
        assert "_rns_storage_prep_warning" in src and "Storage perms not fixed" in src, (
            "a swallowed /etc/reticulum/storage perms-fix failure must reach the "
            "NomadNet launch surface (drift/permission risk), not just a log (S7)"
        )

    def test_nomadnet_rns_probe_is_instance_aware(self):
        src = _src_or_skip(NOMADNET_CHECKS)
        assert "_probe_shared_instance_connect" in src, (
            "the RNS health probe must use the canonical instance-aware helper (S7)"
        )
        assert "rns/default" not in src, (
            "the RNS health probe must not hardcode the 'default' shared-instance "
            "socket — a non-default box gets a false health verdict (#72 class, S7)"
        )


# --- S8: MeshAnchor-divergent honest-signal fixes (MA-native; no MeshForge twin) ---

GATEWAY_DIR = REPO_ROOT / "src" / "gateway"
SUPERVISOR_HANDLER = GATEWAY_DIR / "meshcore_supervisor_handler.py"
BROADCAST_BRIDGE = GATEWAY_DIR / "lxmf_broadcast_bridge.py"
BROADCAST_TUI = HANDLERS_DIR / "lxmf_broadcast.py"


class TestMADivergentS8:
    """S8 (#74-#77): the two HIGH findings from the MeshAnchor-divergent audit
    (`.claude/research/ma_divergent_honest_signal_audit_2026_06_08.md`), both
    operator-facing false verdicts. MA-native surfaces with no MeshForge twin.
    Static source guards (zero-FP, skip-if-absent)."""

    def test_h1_supervisor_emits_recognized_connection_events(self):
        src = _src_or_skip(SUPERVISOR_HANDLER)
        # The dead "radio_up"/"radio_down" events (unrecognized by
        # bridge_health.record_connection_event → silent no-op → MeshCore reads
        # HEALTHY while the radio is down) must be gone.
        assert "radio_up" not in src and "radio_down" not in src, (
            "supervisor handler must emit bridge_health's recognized "
            "'connected'/'disconnected' events, not the no-op radio_* names (H1)"
        )
        assert '"disconnected"' in src, (
            "a radio-down must emit the recognized 'disconnected' event (H1)"
        )

    def test_h1_connect_gates_on_actual_radio_state(self):
        src = _src_or_skip(SUPERVISOR_HANDLER)
        assert 'hello.get("connected")' in src, (
            "connect() must reflect the radio's ACTUAL state from the hello, not "
            "report 'connected' unconditionally (which latched health True) (H1)"
        )

    def test_h2_subscriber_store_renamed_off_delivered(self):
        src = _src_or_skip(BROADCAST_BRIDGE)
        assert "def mark_fanout_enqueued" in src, (
            "the enqueue-time bookkeeping must be named for what it does "
            "(enqueue), not 'mark_delivered' (H2, #16)"
        )
        assert "self._subs.mark_delivered(" not in src, (
            "the fan-out enqueue path must not call mark_delivered — an enqueue "
            "is not a confirmed delivery (H2)"
        )

    def test_h2_tui_labels_fanout_not_delivered(self):
        src = _src_or_skip(BROADCAST_TUI)
        assert "last_ok=" not in src, (
            "the subscriber rows must not label the enqueue timestamp 'last_ok' "
            "(reads as confirmed delivery) (H2)"
        )
        assert "last_fanout" in src and "NOT confirmed" in src, (
            "the subscriber surface must say fan-out is enqueued and delivery is "
            "not confirmed (H2, #16)"
        )


# --- S8 MED tier (M1-M4) ---

NOMADNET_TMUX_OPS = HANDLERS_DIR / "_nomadnet_tmux_service_ops.py"


class TestMeshcoreSaveGatedM12:
    """S8 M1/M2 (#74-#77): _meshcore_configure / _meshcore_toggle call
    GatewayConfig.save(), which returns False on a failed write (never raises),
    so the 'Saved'/'enabled' dialog must gate on the bool — it had fired even
    when nothing persisted. Shared with MeshForge."""

    def test_meshcore_save_dialogs_gate_on_bool(self):
        src = _src_or_skip(MESHCORE_HANDLER)
        assert src.count("saved = config.save()") >= 2, (
            "_meshcore_configure and _meshcore_toggle must bind config.save()'s "
            "bool and branch on it, not fire 'Saved' unconditionally (S8 M1/M2)"
        )
        assert "Save Failed" in src, (
            "a failed meshcore config write must surface a 'Save Failed' dialog (S8)"
        )


class TestSchedulesProbeFailureM3:
    """S8 M3 (#74-#77): a failed systemctl timer-state probe must not read as
    'all healthy' — _list_timers_scope returns None (not []) on failure and
    _schedules_block surfaces it as healthy:False + a reason (MA-native)."""

    def _fa(self):
        try:
            import monitoring.fleet_aggregator as fa
        except Exception as e:
            pytest.skip(f"fleet_aggregator not importable: {e}")
        return fa

    def test_probe_failure_is_not_healthy(self, monkeypatch):
        fa = self._fa()
        monkeypatch.setattr(fa, "_list_timers_scope", lambda scope: None)
        block = fa._schedules_block()
        assert block["healthy"] is False
        assert "reason" in block and "unavailable" in block["reason"]

    def test_genuinely_empty_is_healthy(self, monkeypatch):
        fa = self._fa()
        monkeypatch.setattr(fa, "_list_timers_scope", lambda scope: [])
        block = fa._schedules_block()
        assert block["healthy"] is True
        assert "reason" not in block


class TestNomadnetUninstallM4:
    """S8 M4 (#74-#77): NomadNet uninstall must not claim 'removed' when the
    unit/wrapper unlink failed (the file remains; the service can re-register)."""

    def test_uninstall_reports_incomplete_on_unlink_failure(self):
        src = _src_or_skip(NOMADNET_TMUX_OPS)
        assert "removal_errors" in src and "uninstall incomplete" in src.lower(), (
            "_do_nomadnet_uninstall must track unlink failures and report "
            "'(uninstall incomplete …)' instead of unconditional 'removed' (S8 M4)"
        )


# --- S8 LOW tier (L3-L5; L1/L2 are documented no-ops) ---

ACTIVE_HEALTH_PROBE = REPO_ROOT / "src" / "utils" / "active_health_probe.py"
MESHCORE_CONN = REPO_ROOT / "src" / "utils" / "meshcore_connection.py"


class TestLowTierS8:
    """S8 L3-L5 (#74-#77): low-severity fabricated-data / false-clean fixes."""

    def test_l3_uptime_percent_none_when_never_checked(self):
        # 0.0% uptime for a never-checked service is a fabricated value for
        # "no data"; the status surface must emit None instead.
        src = _src_or_skip(ACTIVE_HEALTH_PROBE)
        assert "if state.total_checks else None" in src, (
            "active_health_probe get_status must emit uptime_percent=None (not 0.0) "
            "for a never-checked service (S8 L3)"
        )

    def test_l4_daemon_federation_error_is_threaded(self, monkeypatch):
        # A registry-fetch failure must return its reason (not a silent []) so
        # the rollup can surface it instead of reading "0 peers".
        try:
            import monitoring.fleet_rollup as fr
            import monitoring.fleet_aggregator as fa
        except Exception as e:
            pytest.skip(f"fleet_rollup not importable: {e}")
        monkeypatch.setattr(fa, "_http_get_json", lambda url, timeout=0: (None, "boom"))
        peers, err = fr._collect_daemon_federation_peers(timeout=1, fresh_window_s=0)
        assert peers == [] and err is not None and "unreachable" in err

    def test_l5_responds_none_when_not_probed(self):
        # When a persistent owner holds the radio we never probe, so 'responds'
        # must be None (unmeasured), never a fabricated True.
        src = _src_or_skip(MESHCORE_CONN)
        assert "result['responds'] = None" in src, (
            "validate_meshcore_device must not claim responds=True for a device "
            "it never probed (persistent-owner skip) (S8 L5)"
        )


class TestScheduledRunningPanelVisibility:
    """Phase-1 fleet visibility: the /fleet 'Scheduled & Running' panel adds
    crontab / cron-verdict / loop-cron sub-sources to the timer view. The
    operator asked for TRUTHFUL reporting — a failed read must render
    'unavailable', NEVER a silent green. Static guards on the web render lock
    that contract (skip-if-absent so they port across the diverged repos)."""

    FLEET_HTML = REPO_ROOT / "web" / "fleet.html"

    def test_web_has_honest_unavailable_branch(self):
        src = _src_or_skip(self.FLEET_HTML)
        assert "renderSchedSub" in src
        # The sub-source renderer MUST branch on availability and render an
        # explicit 'unavailable' — never fall through to a green pill.
        assert "block.available === false" in src
        assert "'unavailable'" in src

    def test_web_covers_all_three_sources_and_synth(self):
        src = _src_or_skip(self.FLEET_HTML)
        for kind in ("'crontab'", "'verdicts'", "'loop_crons'"):
            assert kind in src, f"Scheduled & Running missing sub-source {kind}"
        assert "data.synth_md" in src, "synth-soak result not wired into the panel"
        assert "Scheduled &amp; Running" in src

    def test_web_labels_loop_crons_ephemeral(self):
        src = _src_or_skip(self.FLEET_HTML)
        assert "session-only" in src, (
            "ephemeral Claude /loop crons must be labeled session-only, not "
            "presented as a durable schedule"
        )


class TestLinterLineOffsetContext20260709:
    """2026-07-09 frontier review of the gates (ported from MeshForge). The
    lookahead/lookback rules resolved a line's position via content.find(line)
    — the FIRST textual occurrence — so a real violation on a LATER duplicate
    line was judged against an earlier twin and silently passed. lint_file now
    threads the true per-line offset."""

    def _lint(self, tmp_path, body):
        d = tmp_path / "src" / "monitoring"
        d.mkdir(parents=True)
        fp = d / "dupe_lines.py"
        fp.write_text(body)
        return lint.MeshAnchorLinter().lint_file(str(fp))

    def test_mf010_caught_on_second_identical_sleep(self, tmp_path):
        body = (
            "import time\n"
            "class C:\n"
            "    def helper(self):\n"
            "        time.sleep(1)\n"
            "    def _poll_loop(self):\n"
            "        time.sleep(1)\n"
        )
        mf010 = [i for i in self._lint(tmp_path, body) if i.code == "MF010"]
        assert len(mf010) == 1, f"expected the loop sleep flagged, got {mf010}"
        assert mf010[0].line == 6

    def test_mf004_caught_on_second_identical_subprocess(self, tmp_path):
        body = (
            "import subprocess\n"
            "def a(cmd):\n"
            "    result = subprocess.run(\n"
            "        cmd, timeout=5)\n"
            "def b(cmd):\n"
            "    result = subprocess.run(\n"
            "        cmd)\n"
        )
        mf004 = [i for i in self._lint(tmp_path, body) if i.code == "MF004"]
        assert len(mf004) == 1, f"expected the untimed call flagged, got {mf004}"
        assert mf004[0].line == 6
