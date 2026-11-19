"""CS-1 witness EXPORT: observer_snapshot() -> daemon_status.json -> TUI line.

The observer's state is derived in the daemon process; the TUI is another
process. These tests pin the two halves of that hop and the one key they
share (hfm #4, #5), and that every degraded read is UNKNOWN, never healthy
(hfm #1, #2).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from gateway import meshcore_observer as obs
from gateway import meshcore_observer_status as st

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
STALE = 90.0


class _Handler:
    def __init__(self, snap=None, exc=None):
        self._snap, self._exc = snap, exc

    def observer_snapshot(self):
        if self._exc is not None:
            raise self._exc
        return self._snap


class _Bridge:
    def __init__(self, handler):
        self._meshcore_handler = handler


# --- writer: observer_status_for(bridge) ------------------------------------

class TestWriter:
    def test_no_bridge_is_gateway_not_running(self):
        assert st.observer_status_for(None) == {"state": "gateway_not_running"}

    def test_no_handler(self):
        assert st.observer_status_for(_Bridge(None)) == {"state": "no_meshcore_handler"}

    def test_handler_without_snapshot_is_unobservable(self):
        out = st.observer_status_for(_Bridge(object()))
        assert out["state"] == "unobservable"
        assert "observer_snapshot" in out["reason"]

    def test_snapshot_raising_is_unobservable_with_reason(self):
        out = st.observer_status_for(_Bridge(_Handler(exc=RuntimeError("boom"))))
        assert out == {"state": "unobservable",
                       "reason": "observer_snapshot raised: RuntimeError: boom"}

    @pytest.mark.parametrize("bad", [None, [], "ok", {"queued": 3}, {"state": 7}])
    def test_malformed_snapshot_is_unobservable(self, bad):
        out = st.observer_status_for(_Bridge(_Handler(snap=bad)))
        assert out["state"] == "unobservable"

    def test_magicmock_bridge_does_not_masquerade_as_a_state(self):
        # Existing gateway_cli tests drive get_gateway_stats with a MagicMock
        # bridge; its auto-attributes must never read as an observer state.
        out = st.observer_status_for(MagicMock())
        assert out["state"] == "unobservable"

    def test_snapshot_passed_through_as_a_copy(self):
        snap = {"state": "ok", "queued": 4}
        out = st.observer_status_for(_Bridge(_Handler(snap=snap)))
        assert out == snap and out is not snap

    def test_output_is_json_serialisable_for_every_writer_branch(self):
        for bridge in (None, _Bridge(None), _Bridge(object()),
                       _Bridge(_Handler(exc=ValueError("x"))),
                       _Bridge(_Handler(snap={"state": "disabled"}))):
            json.dumps(st.observer_status_for(bridge))


class TestGatewayStatsCarriesTheKey:
    """Every return path of get_gateway_stats carries STATUS_KEY (hfm #4)."""

    def _with_bridge(self, bridge):
        import gateway.gateway_cli as cli
        original = cli._active_bridge
        try:
            cli._active_bridge = bridge
            return cli.get_gateway_stats()
        finally:
            cli._active_bridge = original

    def test_not_started(self):
        stats = self._with_bridge(None)
        assert stats[st.STATUS_KEY] == {"state": "gateway_not_running"}

    def test_running_bridge(self):
        bridge = MagicMock()
        bridge._running = True
        bridge.get_status.return_value = {"statistics": {}}
        bridge._meshcore_handler = _Handler(snap={"state": "disabled"})
        stats = self._with_bridge(bridge)
        assert stats[st.STATUS_KEY] == {"state": "disabled"}

    def test_error_path(self):
        bridge = MagicMock()
        bridge.get_status.side_effect = RuntimeError("status broke")
        bridge._meshcore_handler = _Handler(snap={"state": "ok"})
        stats = self._with_bridge(bridge)
        assert stats["status"].startswith("Error")
        # review r1 I1: the error path never exports the handler's snapshot
        assert stats[st.STATUS_KEY]["state"] == "unobservable"


# --- the vocabulary: closed enum, closed consumer (hfm #7) -----------------

class TestVocabulary:
    def test_every_derivable_observer_state_has_a_tone(self):
        derived = set()
        for enabled in (False, True):
            for mode in obs.MODES:
                for radio in obs.RADIOS:
                    for setup in obs.SETUPS:
                        for outcome in obs.OUTCOMES:
                            derived.add(obs.derive_state(
                                enabled=enabled, mode=mode, radio=radio,
                                setup=setup, last_outcome=outcome))
        missing = derived - set(st.TONES)
        assert not missing, f"observer states with no TUI tone: {sorted(missing)}"

    def test_every_writer_state_has_a_tone(self):
        for s in ("gateway_not_running", "no_meshcore_handler", "unobservable"):
            assert s in st.TONES

    def test_only_ok_reads_ok(self):
        assert [s for s, t in st.TONES.items() if t == "ok"] == ["ok"]


# --- reader: describe(doc, now, stale_after_s) ------------------------------

def _doc(observer, *, age_s=5.0, bridge_extra=None, daemon_extra=None):
    gb = {"name": "gateway_bridge", "alive": True, "running": True}
    if observer is not None:
        gb[st.STATUS_KEY] = observer
    gb.update(bridge_extra or {})
    daemon = {"status": "running"}
    daemon.update(daemon_extra or {})
    return {"daemon": daemon,
            "updated_at": (NOW - timedelta(seconds=age_s)).isoformat(),
            "services": {"gateway_bridge": gb}}


def _say(doc):
    return st.describe(doc, now=NOW, stale_after_s=STALE)


class TestReader:
    def test_disabled_is_off_not_ok(self):
        tone, line = _say(_doc({"state": "disabled"}))
        assert tone == "off"
        assert "OFF" in line

    def test_disabled_with_config_error_warns(self):
        tone, line = _say(_doc({"state": "disabled",
                                "config_error": "observer_enabled must be a JSON bool"}))
        assert tone == "bad"
        assert "JSON bool" in line

    def test_ok_says_queued_not_stored_and_ages_at_read_time(self):
        tone, line = _say(_doc({"state": "ok", "queued": 12,
                                "last_queued_age_s": 3.0}, age_s=20.0))
        assert tone == "ok"
        assert "12 queued" in line
        assert "storage unproven" in line
        assert "23s ago" in line  # 3 s at write time + 20 s file age

    def test_never_is_unknown_not_ok(self):
        tone, _ = _say(_doc({"state": "never", "queued": 0}))
        assert tone == "unknown"

    @pytest.mark.parametrize("state", ["refusing", "failing", "no_radio",
                                       "not_subscribed", "no_mqtt_client",
                                       "client_error", "subscribe_failed",
                                       "unsupported_supervisor_mode"])
    def test_faults_are_bad_and_name_the_state(self, state):
        tone, line = _say(_doc({"state": state, "last_error": "E: why"}))
        assert tone == "bad"
        assert state in line

    def test_last_error_is_shown(self):
        _, line = _say(_doc({"state": "client_error", "last_error": "OSError: refused"}))
        assert "OSError: refused" in line

    def test_unobservable_carries_reason(self):
        tone, line = _say(_doc({"state": "unobservable", "reason": "observer_snapshot raised: X"}))
        assert tone == "unknown"
        assert "observer_snapshot raised: X" in line

    def test_unrecognised_state_is_unknown_and_loud(self):
        tone, line = _say(_doc({"state": "sparkly"}))
        assert tone == "unknown"
        assert "unrecognised" in line and "sparkly" in line

    def test_missing_key_is_unknown_older_daemon(self):
        tone, line = _say(_doc(None))
        assert tone == "unknown"
        assert "does not report" in line

    def test_stale_file_is_unknown_and_keeps_last_word(self):
        tone, line = _say(_doc({"state": "ok", "queued": 1}, age_s=STALE + 1))
        assert tone == "unknown"
        assert "STALE" in line and "ok" in line

    def test_future_timestamp_is_unknown(self):
        tone, line = _say(_doc({"state": "ok", "queued": 1}, age_s=-120.0))
        assert tone == "unknown"
        assert "future" in line

    def test_small_negative_skew_is_tolerated(self):
        tone, _ = _say(_doc({"state": "ok", "queued": 1, "last_queued_age_s": 1.0},
                            age_s=-2.0))
        assert tone == "ok"

    @pytest.mark.parametrize("doc", [
        None, [], {}, {"updated_at": "garbage", "services": {}},
        {"updated_at": NOW.isoformat()},
        {"updated_at": NOW.isoformat(), "services": {}},
        {"updated_at": NOW.isoformat(), "services": {"gateway_bridge": "x"}},
        {"updated_at": NOW.isoformat(),
         "services": {"gateway_bridge": {st.STATUS_KEY: "ok"}}},
        {"updated_at": "2026-11-19T12:00:00",  # naive timestamp
         "services": {"gateway_bridge": {st.STATUS_KEY: {"state": "ok"}}}},
    ])
    def test_malformed_documents_are_unknown(self, doc):
        tone, _ = _say(doc)
        assert tone == "unknown"

    def test_gateway_not_running_is_unknown_tone(self):
        tone, line = _say(_doc({"state": "gateway_not_running"}))
        assert tone == "unknown"
        assert "gateway" in line


class TestReadFromFile:
    def test_missing_file_is_unknown(self, tmp_path):
        tone, line = st.observer_status_line(tmp_path / "nope.json", now=NOW,
                                             stale_after_s=STALE)
        assert tone == "unknown"
        assert "unreadable" in line

    def test_bad_json_is_unknown(self, tmp_path):
        p = tmp_path / "daemon_status.json"
        p.write_text("{not json")
        tone, _ = st.observer_status_line(p, now=NOW, stale_after_s=STALE)
        assert tone == "unknown"

    def test_round_trip_through_the_real_writer(self, tmp_path):
        """Writer output, dumped the way daemon._write_status_file dumps it,
        reads back as the same state (the two halves share STATUS_KEY)."""
        gb = {"name": "gateway_bridge", "alive": True,
              st.STATUS_KEY: st.observer_status_for(_Bridge(_Handler(snap={"state": "disabled"})))}
        p = tmp_path / "daemon_status.json"
        gb.update(running=True)
        p.write_text(json.dumps({"daemon": {"status": "running"},
                                 "updated_at": NOW.isoformat(),
                                 "services": {"gateway_bridge": gb}},
                                indent=2, default=str))
        tone, line = st.observer_status_line(p, now=NOW, stale_after_s=STALE)
        assert tone == "off" and "OFF" in line

    def test_stale_window_derives_from_daemon_interval(self):
        from daemon_config import DaemonConfig
        assert st.default_stale_after_s() == 3 * DaemonConfig().status_file_interval


# --- TUI: the one line + the detail view ------------------------------------

@pytest.fixture
def tui_handler():
    import os
    import sys
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    for p in (os.path.join(src, "launcher_tui"), src):
        if p not in sys.path:
            sys.path.insert(0, p)
    from handlers.meshcore import MeshCoreHandler as TUIMeshCoreHandler
    h = TUIMeshCoreHandler.__new__(TUIMeshCoreHandler)
    h.ctx = MagicMock()
    return h


class TestTUI:
    @pytest.mark.parametrize("tone,tag", [("ok", "[OK]"), ("off", "[OFF]"),
                                          ("bad", "[FAULT]"), ("unknown", "[UNKNOWN]"),
                                          ("weird", "[UNKNOWN]")])
    def test_summary_tags_the_tone(self, tui_handler, tone, tag):
        from unittest.mock import patch
        with patch.object(st, "observer_status_line", return_value=(tone, "LINE")):
            out = tui_handler._observer_status_summary()
        assert out == f"CS-1 observer {tag}: LINE"

    def test_summary_survives_a_raising_reader(self, tui_handler):
        from unittest.mock import patch
        with patch.object(st, "observer_status_line", side_effect=RuntimeError("x")):
            out = tui_handler._observer_status_summary()
        assert "[UNKNOWN]" in out and "RuntimeError" in out

    def test_menu_subtitle_carries_the_observer_line_and_choice(self, tui_handler):
        from unittest.mock import patch
        tui_handler.ctx.dialog.menu.return_value = "back"
        with patch.object(tui_handler, "_daemon_unit_state", return_value="active"), \
             patch.object(st, "observer_status_line", return_value=("off", "OFF (x)")):
            tui_handler._meshcore_daemon_control()
        title, subtitle, choices = tui_handler.ctx.dialog.menu.call_args[0]
        assert subtitle == "meshanchor-daemon.service: active\nCS-1 observer [OFF]: OFF (x)"
        assert "observer" in [k for k, _ in choices]

    def test_observer_choice_is_wired(self, tui_handler):
        from unittest.mock import patch
        tui_handler.ctx.dialog.menu.side_effect = ["observer", "back"]
        with patch.object(tui_handler, "_daemon_unit_state", return_value="active"), \
             patch.object(tui_handler, "_daemon_show_observer") as show, \
             patch.object(st, "observer_status_line", return_value=("off", "x")):
            tui_handler._meshcore_daemon_control()
        show.assert_called_once()
        tui_handler.ctx.notify_unwired.assert_not_called()

    def test_detail_view_prints_the_raw_export(self, tui_handler, capsys):
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        with patch.object(mod, "clear_screen"), \
             patch.object(st, "observer_status_line", return_value=("ok", "ok — 3 queued")), \
             patch.object(st, "read_observer", return_value=st.ObserverView("ok", "ok — 3 queued", {"state": "ok", "queued": 3}, "", {})):
            tui_handler._daemon_show_observer()
        out = capsys.readouterr().out
        assert "[OK]" in out and '"queued": 3' in out
        tui_handler.ctx.wait_for_enter.assert_called_once()

    def test_detail_view_says_why_when_nothing_exported(self, tui_handler, capsys):
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        with patch.object(mod, "clear_screen"), \
             patch.object(st, "observer_status_line", return_value=("unknown", "U")), \
             patch.object(st, "read_observer", return_value=st.ObserverView("unknown", "U", None, "WHY-X", {})):
            tui_handler._daemon_show_observer()
        out = capsys.readouterr().out
        assert "no exported witness" in out and "WHY-X" in out


class TestRawExport:
    def test_reads_the_object(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"services": {"gateway_bridge": {st.STATUS_KEY: {"state": "ok"}}}}))
        assert st.raw_observer_export(p) == {"state": "ok"}

    @pytest.mark.parametrize("text", ["", "{}", "[]", '{"services": {"gateway_bridge": {"meshcore_observer": 1}}}'])
    def test_none_when_absent_or_wrong(self, tmp_path, text):
        p = tmp_path / "s.json"
        p.write_text(text)
        assert st.raw_observer_export(p) is None

    def test_none_when_missing_file(self, tmp_path):
        assert st.raw_observer_export(tmp_path / "nope") is None


# --- review r1 (contextless code-review medium) -----------------------------

OK_OBS = {"state": "ok", "queued": 5, "last_queued_age_s": 1.0}


class TestR1LivenessIsOnePredicate:
    """I1/I2/N6: an observer line is only as live as the daemon AND the bridge
    that host it — read from the same document, at read time."""

    @pytest.mark.parametrize("daemon_extra", [{"status": "stopped"}, {"status": None},
                                              {"status": "starting"}])
    def test_daemon_not_running_is_unknown_with_last_word(self, daemon_extra):
        tone, line = _say(_doc(OK_OBS, daemon_extra=daemon_extra))
        assert tone == "unknown"
        assert "daemon" in line and "last said" in line

    def test_missing_daemon_block_is_unknown(self):
        doc = _doc(OK_OBS)
        del doc["daemon"]
        assert _say(doc)[0] == "unknown"

    @pytest.mark.parametrize("bridge_extra", [{"running": False}, {"alive": False},
                                              {"running": None}, {"alive": "yes"}])
    def test_bridge_not_running_or_alive_is_unknown(self, bridge_extra):
        tone, line = _say(_doc(OK_OBS, bridge_extra=bridge_extra))
        assert tone == "unknown"
        assert "gateway bridge" in line

    def test_shim_error_is_reported_as_the_error_not_older_code(self):
        tone, line = _say(_doc(None, bridge_extra={"alive": False, "status": "error",
                                                   "running": None}))
        assert tone == "unknown"
        assert "older code" not in line and "error" in line

    def test_writer_stopped_bridge_does_not_export_its_snapshot(self):
        import gateway.gateway_cli as cli
        bridge = MagicMock()
        bridge._running = False
        bridge.get_status.return_value = {"statistics": {}}
        bridge._meshcore_handler = _Handler(snap=dict(OK_OBS))
        original = cli._active_bridge
        try:
            cli._active_bridge = bridge
            stats = cli.get_gateway_stats()
        finally:
            cli._active_bridge = original
        assert stats[st.STATUS_KEY] == {"state": "gateway_not_running"}

    def test_writer_error_path_does_not_export_its_snapshot(self):
        import gateway.gateway_cli as cli
        bridge = MagicMock()
        bridge.get_status.side_effect = RuntimeError("status broke")
        bridge._meshcore_handler = _Handler(snap=dict(OK_OBS))
        original = cli._active_bridge
        try:
            cli._active_bridge = bridge
            stats = cli.get_gateway_stats()
        finally:
            cli._active_bridge = original
        assert stats[st.STATUS_KEY]["state"] == "unobservable"
        assert "status broke" in stats[st.STATUS_KEY]["reason"]


class TestR1Nits:
    @pytest.mark.parametrize("queued", [None, -1, "5", 2.5, True])
    def test_ok_without_a_valid_counter_is_unknown(self, queued):
        obs = {"state": "ok", "queued": queued}
        if queued is None:
            obs.pop("queued")
        assert _say(_doc(obs))[0] == "unknown"

    def test_disabled_with_config_error_tone_comes_from_the_table(self):
        assert st.TONES["disabled_config_error"] == "bad"

    def test_stale_window_comes_from_the_writer_interval(self):
        # The daemon says it writes every 300 s: 200 s old is fresh.
        doc = _doc(OK_OBS, age_s=200.0, daemon_extra={"status_file_interval": 300})
        assert _say(doc)[0] == "ok"
        doc = _doc(OK_OBS, age_s=901.0, daemon_extra={"status_file_interval": 300})
        assert _say(doc)[0] == "unknown"

    @pytest.mark.parametrize("bad", [0, -5, "30", None, True])
    def test_invalid_writer_interval_falls_back(self, bad):
        doc = _doc(OK_OBS, age_s=STALE + 1, daemon_extra={"status_file_interval": bad})
        assert _say(doc)[0] == "unknown"

    def test_daemon_writes_its_interval(self, tmp_path):
        import daemon as d
        from unittest.mock import patch
        inst = d.DaemonController.__new__(d.DaemonController)
        inst._config = MagicMock(status_file_interval=45)
        inst._started_at = None
        inst._profile_name = "x"
        inst._registry = None
        inst._watchdog = None
        path = tmp_path / "daemon_status.json"
        with patch.object(d.DaemonController, "_status_file_path", return_value=path):
            inst._write_status_file()
        assert json.loads(path.read_text())["daemon"]["status_file_interval"] == 45

    def test_default_stale_survives_a_broken_config_load(self):
        from unittest.mock import patch
        from daemon_config import DaemonConfig
        with patch.object(DaemonConfig, "load", side_effect=RuntimeError("yaml")):
            assert st.default_stale_after_s() == 3 * DaemonConfig().status_file_interval

    def test_detail_view_names_the_real_reason(self, tmp_path):
        obj, why = st.raw_observer_export_with_reason(tmp_path / "nope.json")
        assert obj is None and "unreadable" in why
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"services": {"gateway_bridge": {}}}))
        obj, why = st.raw_observer_export_with_reason(p)
        assert obj is None and "no meshcore_observer" in why

    def test_subtitle_line_is_bounded(self, tui_handler):
        from unittest.mock import patch
        with patch.object(st, "observer_status_line", return_value=("unknown", "x" * 500)):
            out = tui_handler._observer_status_summary()
        assert len(out) <= st.SUBTITLE_MAX and out.endswith("…")


    def test_detail_view_shows_the_full_line(self, tui_handler, capsys):
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        long = "y" * 300
        with patch.object(mod, "clear_screen"), \
             patch.object(st, "observer_status_line", return_value=("unknown", long)), \
             patch.object(st, "read_observer", return_value=st.ObserverView("unknown", long, None, "w", {})):
            tui_handler._daemon_show_observer()
        assert long in capsys.readouterr().out



# --- review r2 (Fable, by running) ------------------------------------------
# Same KIND as r1 (a verdict computed without all of its context) -> the seam:
# describe() derives EVERY context fact first and each one prefixes the verdict.

class TestR2ContextFirst:
    def test_stale_file_without_key_says_stale_not_older_code(self):
        tone, line = _say(_doc(None, age_s=170 * 86400))
        assert tone == "unknown"
        assert line.startswith("UNKNOWN — STALE")
        assert "older code" not in line

    def test_stale_and_stopped_names_both(self):
        tone, line = _say(_doc(OK_OBS, age_s=170 * 86400,
                               daemon_extra={"status": "stopped"}))
        assert tone == "unknown"
        assert "STALE" in line and "stopped" in line and "last said" in line

    @pytest.mark.parametrize("unit", ["failed", "inactive", "activating", "", "error: x"])
    def test_unit_not_active_is_unknown_even_with_a_fresh_ok_file(self, unit):
        tone, line = st.describe(_doc(OK_OBS), now=NOW, stale_after_s=STALE,
                                 unit_state=unit)
        assert tone == "unknown"
        assert "unit" in line and "last said" in line

    def test_unit_active_allows_ok(self):
        tone, _ = st.describe(_doc(OK_OBS), now=NOW, stale_after_s=STALE,
                              unit_state="active")
        assert tone == "ok"

    def test_shim_error_without_key_names_the_status_error(self):
        _, line = _say(_doc(None, bridge_extra={"alive": False, "running": None,
                                                "status": "error"}))
        assert "status='error'" in line


class TestR2Bounds:
    @pytest.mark.parametrize("interval", [86400, 1e9, float("inf"), float("nan"), 10 ** 400])
    def test_absurd_written_interval_falls_back(self, interval):
        doc = _doc(OK_OBS, age_s=2 * 86400, daemon_extra={"status_file_interval": interval})
        assert _say(doc)[0] == "unknown"

    @pytest.mark.parametrize("bad", [True, 0, -5])
    def test_invalid_interval_uses_fallback_not_its_own_value(self, bad):
        # 50 s old is fresh under the 90 s fallback; a mutant that accepted
        # True/0/-5 as an interval would call it stale.
        doc = _doc(OK_OBS, age_s=50.0, daemon_extra={"status_file_interval": bad})
        assert _say(doc)[0] == "ok"

    def test_interval_at_the_cap_is_honoured(self):
        doc = _doc(OK_OBS, age_s=3 * 3600 - 10,
                   daemon_extra={"status_file_interval": st.MAX_INTERVAL_S})
        assert _say(doc)[0] == "ok"

    @pytest.mark.parametrize("age", [float("nan"), float("inf"), -1.0, "3", True])
    def test_ok_with_an_invalid_age_is_unknown_and_never_raises(self, age):
        tone, line = _say(_doc({"state": "ok", "queued": 3, "last_queued_age_s": age}))
        assert tone == "unknown"
        assert "last_queued_age_s" in line

    def test_ok_without_an_age_is_still_ok(self):
        assert _say(_doc({"state": "ok", "queued": 3}))[0] == "ok"

    def test_reader_never_raises_when_describe_does(self, tmp_path):
        from unittest.mock import patch
        p = tmp_path / "s.json"
        p.write_text("{}")
        with patch.object(st, "_view", side_effect=RuntimeError("inner")):
            view = st.read_observer(p, now=NOW, stale_after_s=STALE)
        assert view.tone == "unknown" and "RuntimeError: inner" in view.line

    def test_nan_through_the_real_json_dump_is_unknown(self, tmp_path):
        p = tmp_path / "s.json"
        doc = _doc({"state": "ok", "queued": 1, "last_queued_age_s": float("nan")})
        p.write_text(json.dumps(doc))
        assert st.observer_status_line(p, now=NOW, stale_after_s=STALE)[0] == "unknown"


class TestR2Pins:
    """Kill r2's surviving mutants m04 m21 m27 m40."""

    def test_reader_bridge_running_must_be_true_not_truthy(self):
        assert _say(_doc(OK_OBS, bridge_extra={"running": 1}))[0] == "unknown"

    def test_writer_running_must_be_true_not_truthy(self):
        import gateway.gateway_cli as cli
        bridge = MagicMock()
        bridge._running = 1
        bridge.get_status.return_value = {"statistics": {}}
        bridge._meshcore_handler = _Handler(snap=dict(OK_OBS))
        original = cli._active_bridge
        try:
            cli._active_bridge = bridge
            stats = cli.get_gateway_stats()
        finally:
            cli._active_bridge = original
        assert stats[st.STATUS_KEY] == {"state": "gateway_not_running"}

    def test_exported_internal_tone_key_is_not_a_state(self):
        tone, line = _say(_doc({"state": "disabled_config_error"}))
        assert tone == "unknown" and "unrecognised" in line

    def test_raw_reason_when_gateway_bridge_missing(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"services": {}}))
        obj, why = st.raw_observer_export_with_reason(p)
        assert obj is None and why == "daemon status has no gateway_bridge entry"


class TestR2WriterSerialisable:
    def test_unserialisable_snapshot_is_unobservable(self):
        out = st.observer_status_for(_Bridge(_Handler(snap={"state": "ok",
                                                            "length_buckets": {(1, 2): 3}})))
        assert out["state"] == "unobservable"
        assert "JSON" in out["reason"]
        json.dumps(out)


class TestR2OneReadView:
    def test_view_carries_line_raw_and_context_from_one_read(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps(_doc(OK_OBS, age_s=10.0)))
        view = st.read_observer(p, now=NOW, stale_after_s=STALE, unit_state="active")
        assert view.tone == "ok"
        assert view.raw == OK_OBS
        assert view.context["daemon_status"] == "running"
        assert view.context["bridge_running"] is True and view.context["bridge_alive"] is True
        assert view.context["file_age_s"] == pytest.approx(10.0)
        assert view.context["unit_state"] == "active"

    def test_observer_status_line_is_the_view_line(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps(_doc(OK_OBS)))
        view = st.read_observer(p, now=NOW, stale_after_s=STALE)
        assert st.observer_status_line(p, now=NOW, stale_after_s=STALE) == (view.tone, view.line)

    def test_detail_view_prints_the_context(self, tui_handler, capsys):
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        ctx = {"file_age_s": 12.0, "daemon_status": "stopped", "bridge_running": True,
               "bridge_alive": False, "unit_state": "failed"}
        view = st.ObserverView("unknown", "UNKNOWN — x", {"state": "ok"}, "", ctx)
        with patch.object(mod, "clear_screen"), \
             patch.object(tui_handler, "_daemon_unit_state", return_value="failed"), \
             patch.object(st, "read_observer", return_value=view):
            tui_handler._daemon_show_observer()
        out = capsys.readouterr().out
        for needle in ("12s", "stopped", "alive=False", "failed", '"state": "ok"'):
            assert needle in out


class TestR2Subtitle:
    def test_ok_subtitle_keeps_the_disclaimer_with_an_11_digit_counter(self, tmp_path, tui_handler):
        from unittest.mock import patch
        p = tmp_path / "s.json"
        p.write_text(json.dumps(_doc({"state": "ok", "queued": 12345678901,
                                      "last_queued_age_s": 2700.0})))
        real = st.observer_status_line
        with patch.object(st, "observer_status_line",
                          side_effect=lambda **kw: real(p, now=NOW, stale_after_s=STALE, **kw)):
            out = tui_handler._observer_status_summary(unit_state="active")
        assert "[OK]" in out and "storage unproven" in out and not out.endswith("…")

    def test_menu_passes_the_unit_state_to_the_reader(self, tui_handler):
        from unittest.mock import patch
        tui_handler.ctx.dialog.menu.return_value = "back"
        with patch.object(tui_handler, "_daemon_unit_state", return_value="failed"), \
             patch.object(st, "observer_status_line", return_value=("unknown", "x")) as osl:
            tui_handler._meshcore_daemon_control()
        assert osl.call_args.kwargs.get("unit_state") == "failed"


# --- review r3 (Fable, by running): pin every surviving mutant that matters --

class TestR3Pins:
    def test_missing_updated_at_alone_is_unknown(self):  # n36
        doc = _doc(OK_OBS)
        del doc["updated_at"]
        tone, line = _say(doc)
        assert tone == "unknown"
        assert line.startswith("UNKNOWN — daemon status has no valid updated_at")

    def test_garbage_updated_at_alone_is_unknown(self):  # n36
        doc = _doc(OK_OBS)
        doc["updated_at"] = "yesterday"
        assert _say(doc) [0] == "unknown"

    def test_missing_gateway_bridge_names_it(self):  # n35
        doc = _doc(OK_OBS)
        doc["services"] = {}
        tone, line = _say(doc)
        assert tone == "unknown" and "no gateway_bridge entry" in line
        assert "older code" not in line

    def test_non_dict_observer_names_the_cause(self):  # n30
        tone, line = _say(_doc("ok"))
        assert tone == "unknown"
        assert "not an object" in line and "older code" not in line

    def test_raw_reason_for_non_object(self, tmp_path):  # n33b
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"services": {"gateway_bridge": {st.STATUS_KEY: 3}}}))
        assert st.raw_observer_export_with_reason(p) == (None, f"{st.STATUS_KEY} is not an object")

    def test_negative_file_age_does_not_shrink_the_observer_age(self):  # n40
        _, line = _say(_doc({"state": "ok", "queued": 1, "last_queued_age_s": 10.0},
                            age_s=-30.0))
        assert "last 10s ago" in line

    def test_disclaimer_comes_before_the_age(self):  # n22b
        _, line = _say(_doc(OK_OBS))
        assert line.index("storage unproven") < line.index("last ")

    def test_subtitle_cap_is_76(self):  # n43
        assert st.SUBTITLE_MAX == 76

    @pytest.mark.parametrize("bad", [float("inf"), 7200, 0, -1, True])
    def test_fallback_interval_from_config_is_validated(self, bad):  # n46
        from unittest.mock import patch
        from daemon_config import DaemonConfig
        with patch.object(DaemonConfig, "load", return_value=MagicMock(status_file_interval=bad)):
            assert st.default_stale_after_s() == 3 * DaemonConfig().status_file_interval

    def test_circular_snapshot_is_unobservable(self):  # w08
        snap = {"state": "ok"}
        snap["self"] = snap
        out = st.observer_status_for(_Bridge(_Handler(snap=snap)))
        assert out["state"] == "unobservable"

    def test_pathologically_deep_snapshot_is_unobservable_not_raised(self):  # r3 INFO
        deep = {}
        cur = deep
        for _ in range(100000):
            cur["d"] = {}
            cur = cur["d"]
        out = st.observer_status_for(_Bridge(_Handler(snap={"state": "ok", "deep": deep})))
        assert out["state"] == "unobservable"

    @pytest.mark.parametrize("fallback", [float("nan"), float("inf"), -1.0, 0.0])
    def test_non_finite_fallback_window_is_not_never_stale(self, fallback):  # r3 LOW
        tone, line = st.describe(_doc(OK_OBS, age_s=9 * 86400), now=NOW,
                                 stale_after_s=fallback)
        assert tone == "unknown" and "STALE" in line


class TestR3TUIPins:
    def test_detail_view_reads_the_real_file_with_the_unit_state(self, tui_handler, tmp_path, capsys):  # t06
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        p = tmp_path / "daemon_status.json"
        fresh = _doc(OK_OBS)
        fresh["updated_at"] = datetime.now(timezone.utc).isoformat()
        p.write_text(json.dumps(fresh))
        with patch.object(mod, "clear_screen"), \
             patch.object(st, "_status_path", return_value=p), \
             patch.object(tui_handler, "_daemon_unit_state", return_value="failed"):
            tui_handler._daemon_show_observer()
        out = capsys.readouterr().out
        assert "[UNKNOWN]" in out and "unit is 'failed'" in out and "[OK]" not in out

    def test_detail_view_unrecognised_tone_tags_unknown(self, tui_handler, capsys):  # t09
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        with patch.object(mod, "clear_screen"), \
             patch.object(tui_handler, "_daemon_unit_state", return_value="active"), \
             patch.object(st, "read_observer",
                          return_value=st.ObserverView("weird", "L", None, "w", {})):
            tui_handler._daemon_show_observer()
        assert "[UNKNOWN]" in capsys.readouterr().out

    def test_detail_view_reader_crash_tags_unknown(self, tui_handler, capsys):  # t10
        from unittest.mock import patch
        import handlers._meshcore_daemon as mod
        with patch.object(mod, "clear_screen"), \
             patch.object(tui_handler, "_daemon_unit_state", return_value="active"), \
             patch.object(st, "read_observer", side_effect=RuntimeError("boom")):
            tui_handler._daemon_show_observer()
        out = capsys.readouterr().out
        assert "[UNKNOWN]" in out and "[OK]" not in out

    def test_unit_state_error_is_not_active(self, tui_handler):  # t11
        import subprocess as _sp
        from unittest.mock import patch
        with patch.object(_sp, "run", side_effect=OSError("no systemctl")):
            assert tui_handler._daemon_unit_state() != "active"

    def test_unit_state_empty_output_is_not_active(self, tui_handler):  # t12
        import subprocess as _sp
        from unittest.mock import patch
        with patch.object(_sp, "run", return_value=MagicMock(stdout="", stderr="")):
            assert tui_handler._daemon_unit_state() == "unknown"

    def test_subtitle_is_one_line_even_with_newlines_in_the_error(self, tui_handler):  # r3 LOW
        from unittest.mock import patch
        with patch.object(st, "observer_status_line",
                          return_value=("bad", "failing — ConnectionError: x\ny\r\nz")):
            out = tui_handler._observer_status_summary(unit_state="active")
        assert "\n" not in out and "\r" not in out
        assert out.startswith("CS-1 observer [FAULT]: failing — ConnectionError: x y z")
