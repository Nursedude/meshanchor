"""Oracle posture: published by the daemon, rendered by the TUI (roadmap 1d).

The posture is decided by env vars in the DAEMON's process — on
meshanchor-server the allowlist arrives via a systemd drop-in. So it must
be reported by the process that built the responder, never recomputed from
the TUI's own environment. These tests pin that, and pin the three
outcomes staying distinct: UNKNOWN, BUILD FAILED, and OFF are different
claims and collapsing any pair reports a broken oracle as a deliberate one.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context  # noqa: E402
# TWO different classes share the name MeshCoreHandler: the TUI one that
# RENDERS the posture line, and the gateway one that BUILDS the responder.
# A first draft of this file asserted the daemon's attribute on the TUI
# object and failed; its sibling fed the TUI object to _oracle_posture and
# PASSED, reporting a readable posture for an object that has no oracle at
# all. Import both under distinct names so that cannot recur.
from handlers.meshcore import MeshCoreHandler as TuiMeshCoreHandler  # noqa: E402
from gateway.meshcore_handler import MeshCoreHandler as DaemonMeshCoreHandler  # noqa: E402
from utils.stats_api import _oracle_posture  # noqa: E402

def _live_oracle():
    """A REAL MeshOracleResponder in the live posture, never a stub.

    ⚠️ This was a SimpleNamespace carrying the attribute names taken from
    from_env's constructor kwargs (allowlist=, allowed_channels=, ...).
    The real class stores them privately (_allowlist, _allowed_channels,
    _answer_all, _cooldown_s, _transport); only `consume` is public. The
    stub therefore encoded the author's assumption and agreed with the
    reader's identical assumption, so the suite was green while the daemon
    served allowlist=0 channels=[] cooldown=null against its own build log
    saying allowlist=1 channels=meshanchor cooldown=10s.

    A fixture that fabricates field names pins the author, not the object.
    Build the real thing; it is the only witness that can disagree.
    """
    from oracle.responder import MeshOracleResponder
    return MeshOracleResponder(
        snapshot_fn=lambda: {}, send_fn=lambda *a, **k: True,
        log_fn=lambda r: None,
        allowlist={"7eb0fa289c11"}, allowed_channels={"meshanchor"},
        answer_all=False, cooldown_s=10.0, transport="meshcore",
        consume=False)


@pytest.fixture
def handler():
    """The TUI handler — the thing that RENDERS the line."""
    h = TuiMeshCoreHandler()
    h.set_context(make_handler_context())
    h.ctx.wait_for_enter = MagicMock()
    return h


def _daemon_handler(build_side_effect=None):
    """The gateway handler — the thing that BUILDS the responder."""
    import threading
    from queue import Queue
    cfg = SimpleNamespace(meshcore=SimpleNamespace(
        enabled=True, device_path='/dev/ttyUSB1', baud_rate=115200,
        connection_type='serial', tcp_host='localhost', tcp_port=4000,
        auto_fetch_messages=True, bridge_channels=True, bridge_dms=True,
        simulation_mode=True, bridge_target_channel=1,
        channel_poll_interval_sec=5, repliable_contacts=[],
        dm_replies_enabled=False, bridge_source_channels=None,
        advert_heartbeat_sec=0, advert_heartbeat_flood=False))
    patcher = patch.object(
        DaemonMeshCoreHandler, "_build_meshcore_oracle_responder",
        side_effect=build_side_effect if build_side_effect
        else (lambda self=None: None))
    with patcher:
        return DaemonMeshCoreHandler(
            config=cfg, node_tracker=MagicMock(), health=MagicMock(),
            stop_event=threading.Event(), stats={}, stats_lock=threading.Lock(),
            message_queue=Queue(maxsize=10))


class TestPostureFromTheDaemon:
    def test_no_handler_is_unobservable_not_off(self):
        p = _oracle_posture(None)
        assert p["observable"] is False
        assert "enabled" not in p        # must not assert a state it cannot see

    def test_build_failure_is_distinct_from_disabled(self):
        broken = SimpleNamespace(_oracle_error="RuntimeError: boom", _oracle=None)
        off = SimpleNamespace(_oracle_error=None, _oracle=None)
        pb, po = _oracle_posture(broken), _oracle_posture(off)
        assert pb["enabled"] is False and pb["error"]
        assert po["enabled"] is False and "error" not in po
        assert pb != po

    def test_enabled_reports_the_live_shape(self):
        h = SimpleNamespace(_oracle_error=None, _oracle=_live_oracle())
        p = _oracle_posture(h)
        assert p == {"observable": True, "enabled": True, "answer_all": False,
                     "allowlist": 1, "channels": ["meshanchor"],
                     "cooldown_s": 10.0, "consume": False,
                     "transport": "meshcore"}

    def test_allowlist_is_a_count_not_the_tokens(self):
        h = SimpleNamespace(_oracle_error=None, _oracle=_live_oracle())
        p = _oracle_posture(h)
        assert p["allowlist"] == 1
        assert "7eb0fa289c11" not in repr(p)

    def test_every_field_is_actually_read_off_the_real_object(self):
        """No field may fall through to a default.

        This is the pin the SimpleNamespace stub could not provide: it
        fails if any attribute name drifts from what the class really
        stores, which is exactly how the daemon shipped allowlist=0.
        """
        h = SimpleNamespace(_oracle_error=None, _oracle=_live_oracle())
        p = _oracle_posture(h)
        assert "unreadable" not in p, p.get("unreadable")
        assert p["allowlist"] == 1
        assert p["channels"] == ["meshanchor"]
        assert p["cooldown_s"] == 10.0
        assert p["transport"] == "meshcore"

    def test_an_unreadable_field_is_named_not_defaulted(self):
        """A responder missing a field must SAY so, not report a zero."""
        h = SimpleNamespace(_oracle_error=None,
                            _oracle=SimpleNamespace(consume=False))
        p = _oracle_posture(h)
        assert p["unreadable"], "silently defaulted every field"
        assert "allowlist" in p["unreadable"]


class TestHandlerRecordsABuildFailure:
    def test_a_raising_builder_leaves_a_witness(self):
        """Without this, a FAILED oracle and an off-by-design one are
        indistinguishable — both just leave self._oracle None."""
        h = _daemon_handler(build_side_effect=RuntimeError("boom"))
        assert h._oracle is None
        assert h._oracle_error and "boom" in h._oracle_error
        assert _oracle_posture(h)["error"]

    def test_a_clean_build_records_no_error(self):
        h = _daemon_handler()
        assert h._oracle is None and h._oracle_error is None
        p = _oracle_posture(h)
        assert p == {"observable": True, "enabled": False}


class TestPostureLine:
    def test_off_says_off_and_why(self, handler):
        line = handler._oracle_posture_line(
            {"observable": True, "enabled": False})
        assert "OFF" in line and "MESHANCHOR_ORACLE_ENABLED" in line

    def test_build_failure_is_loud_and_never_reads_as_off(self, handler):
        line = handler._oracle_posture_line(
            {"observable": True, "enabled": False, "error": "RuntimeError: boom"})
        assert "BUILD FAILED" in line and "boom" in line
        assert "OFF (" not in line

    def test_unobservable_never_reads_as_off(self, handler):
        line = handler._oracle_posture_line(
            {"observable": False, "reason": "no meshcore handler on this bridge"})
        assert "UNKNOWN" in line
        assert "OFF (" not in line

    def test_missing_key_from_an_older_daemon_is_unknown(self, handler):
        """A daemon that predates this field must not read as a disabled
        oracle — absence of the report is not a report of absence."""
        line = handler._oracle_posture_line(None)
        assert "UNKNOWN" in line
        assert "OFF (" not in line

    def test_enabled_matches_the_daemons_own_vocabulary(self, handler):
        line = handler._oracle_posture_line({
            "observable": True, "enabled": True, "answer_all": False,
            "allowlist": 1, "channels": ["meshanchor"], "cooldown_s": 10.0,
            "consume": False, "transport": "meshcore"})
        # Same words the journal uses, so a grep and this pane agree.
        for token in ("answer_all=False", "allowlist=1",
                      "channels=meshanchor", "cooldown=10s", "consume=False"):
            assert token in line, f"{token!r} missing from {line!r}"


# ── the pane that CARRIES the line must be able to see the daemon ────────
#
# Found 2026-09-22 while porting 1e into MeshForge: _meshcore_stats was gated
# on _is_gateway_running(), an in-process global that meshanchor-daemon (a
# separate process) never sets in the TUI, so the pane said "not running"
# from every TUI and the posture line above never rendered live. The
# 09-21/22 verification was of the daemon's /api/stats over curl, not of
# the pane. These pin the pane reading the daemon's OWN endpoint.

import json as _json
import urllib.error as _uerr


def _http(payload):
    resp = MagicMock()
    resp.read.return_value = _json.dumps(payload).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = lambda *a: False
    return patch("urllib.request.urlopen", return_value=resp)


class TestStatsPaneReadsTheDaemon:
    def test_fetch_targets_the_daemons_own_endpoint(self, handler):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")) as uo:
            payload, err = handler._stats_fetch()
        assert payload is None and "refused" in err
        assert uo.call_args[0][0].full_url == "http://127.0.0.1:8081/api/stats"

    def test_unreachable_daemon_is_unknown_not_zero(self, handler, capsys):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")), \
             patch("handlers.meshcore._is_gateway_running", return_value=False):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "UNKNOWN" in out and "unreachable" in out
        assert "Messages RX" not in out          # no counters rendered as zero
        assert "Daemon Control" in out           # in-app remediation, not a shell line

    def test_live_daemon_renders_the_posture_line_and_counters(self, handler, capsys):
        with _http({"running": True, "meshcore_connected": True,
                    "oracle": {"observable": True, "enabled": True, "answer_all": False,
                               "allowlist": 1, "channels": ["meshanchor"],
                               "cooldown_s": 10.0, "consume": False},
                    "uptime_seconds": 3700,
                    "stats": {"meshcore_rx": 7, "meshcore_tx": 2, "meshcore_acks": 1}}):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "CONNECTED" in out and "Bridge:      Running" in out
        assert ("Oracle:      ON  answer_all=False allowlist=1 channels=meshanchor "
                "cooldown=10s consume=False") in out
        assert "Messages RX:    7" in out and "Uptime: 1h 1m 40s" in out

    def test_503_is_the_daemons_own_reason_not_unreachable(self, handler, capsys):
        """utils.stats_api answers 503 {"error": "Gateway bridge not active"}
        while the daemon is up and starting — its words, not ours."""
        body = _json.dumps({"error": "Gateway bridge not active"}).encode()
        err = _uerr.HTTPError("u", 503, "Service Unavailable", {}, None)
        err.read = lambda: body
        with patch("urllib.request.urlopen", side_effect=err), \
             patch("handlers.meshcore._is_gateway_running", return_value=False):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "HTTP 503" in out and "Gateway bridge not active" in out

    def test_404_is_an_older_daemon_build(self, handler):
        err = _uerr.HTTPError("u", 404, "Not Found", {}, None)
        err.read = lambda: b""
        with patch("urllib.request.urlopen", side_effect=err):
            payload, reason = handler._stats_fetch()
        assert payload is None and "predates" in reason and "Restart" in reason

    def test_in_process_fallback_only_when_this_process_is_the_daemon(self, handler, capsys):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")), \
             patch("handlers.meshcore._is_gateway_running", return_value=True), \
             patch("handlers.meshcore._get_gateway_stats", return_value={
                 "running": True, "status": "Running", "meshcore_connected": False,
                 "statistics": {"meshcore_rx": 1}, "uptime_seconds": 5}):
            handler._meshcore_stats()
        out = capsys.readouterr().out
        assert "DISCONNECTED" in out and "Messages RX:    1" in out
        assert "UNKNOWN (daemon did not report a posture" in out
