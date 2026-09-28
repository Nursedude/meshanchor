"""TUI audit finding 8 (2026-09-27) — MeshAnchor twin of MeshForge 3d0e1980,
shaped to MA: here the daemon IS a service (meshanchor-daemon runs daemon.py),
the "meshanchor" unit is core.orchestrator, and the daemon's Config API holds
:8081 (MEASURED on meshanchor-server: pid of daemon.py LISTEN 127.0.0.1:8081)
— so the TUI's auto-start lost that race on every launch and read STOPPED.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context


def _daemon():
    from handlers.daemon import DaemonHandler
    h = DaemonHandler()
    h.set_context(make_handler_context())
    h.ctx.dialog._yesno_returns = [True]
    return h


class TestStart:
    def test_refuses_when_the_daemon_service_already_runs(self):
        h = _daemon()
        with patch("utils.service_check.check_service",
                   return_value=SimpleNamespace(available=True)), \
             patch("handlers.daemon.subprocess.Popen") as popen:
            h._daemon_start()
        popen.assert_not_called()
        assert h.ctx.dialog.last_msgbox_title == "Daemon NOT Started"
        assert "meshanchor-daemon service" in h.ctx.dialog.last_msgbox_text

    def test_unknown_service_state_refuses(self):
        h = _daemon()
        with patch("utils.service_check.check_service", side_effect=RuntimeError), \
             patch("handlers.daemon.subprocess.Popen") as popen:
            h._daemon_start()
        popen.assert_not_called()

    def test_service_down_starts_and_logs_to_the_file(self, tmp_path):
        h = _daemon()
        log = tmp_path / "daemon.log"
        proc = MagicMock()
        proc.poll.return_value = None
        with patch("utils.service_check.check_service",
                   return_value=SimpleNamespace(available=False)), \
             patch("handlers.daemon._daemon_log_path", return_value=log), \
             patch("handlers.daemon.subprocess.Popen", return_value=proc) as popen, \
             patch("time.sleep"):
            h._daemon_start()
        import subprocess
        kw = popen.call_args.kwargs
        assert kw["stdout"] is not subprocess.DEVNULL and log.exists()


def test_logs_read_the_unit_that_runs_daemon_py(tmp_path):
    h = _daemon()
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(stdout="daemon line\n", returncode=0)
    with patch("handlers.daemon.subprocess.run", side_effect=fake_run), \
         patch("handlers.daemon.clear_screen", lambda: None), \
         patch("handlers.daemon._daemon_log_path", return_value=tmp_path / "x.log"), \
         patch("builtins.input", lambda *a, **k: ""):
        h.ctx.wait_for_enter = lambda *a, **k: None
        h._daemon_logs()
    cmd = seen["cmd"]
    assert cmd[cmd.index("-u") + 1] == "meshanchor-daemon"


class TestConfigApiPort:
    def _api(self):
        from handlers.config_api import ConfigAPIHandler
        h = ConfigAPIHandler()
        h.set_context(make_handler_context())
        return h

    def test_auto_start_skips_a_port_already_served(self):
        h = self._api()
        with patch.object(type(h), "_port_in_use", staticmethod(lambda port=8081: True)), \
             patch("utils.config_api.ConfigAPIServer") as server:
            h._maybe_auto_start()
        server.assert_not_called()

    def test_menu_says_served_elsewhere_not_stopped(self):
        h = self._api()
        h.ctx.dialog._menu_returns = ["back"]
        with patch.object(type(h), "_port_in_use", staticmethod(lambda port=8081: True)):
            h._config_api_menu()
        body = str([c for c in h.ctx.dialog.calls if c[0] == "menu"][0])
        assert "SERVED BY ANOTHER PROCESS" in body and "STOPPED" not in body
