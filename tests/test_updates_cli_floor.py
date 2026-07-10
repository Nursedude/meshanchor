"""CLI fleet-floor arc + self-update git redesign — MA port of the
MeshForge 2026-07-10 test classes (tests/test_updates.py there)."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from handlers.updates import UpdatesHandler
from utils.pip_install import PipResult

RNSD_IFACE = "/etc/reticulum/interfaces/Meshtastic_Interface.py"


def _path_exists(rnsd_present):
    def fake(self):
        s = str(self)
        if s == RNSD_IFACE:
            return rnsd_present
        return False
    return fake


def _result(returncode, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _handler():
    h = UpdatesHandler()
    h.ctx = MagicMock()
    return h


def _msgbox_titles(dialog):
    return [c.args[0] for c in dialog.msgbox.call_args_list if c.args]


class TestPipxUpgradeCliTargetsOwner:
    """`_pipx_upgrade_cli` must run pipx as the OWNER of the resolved CLI
    binary (so the write lands in the pipx home the reader reads from) AND
    target the reviewed fleet floor, never PyPI-latest (2026-07-10 audit:
    `pipx upgrade` overshoots the reviewed pin the moment PyPI moves)."""

    def test_drops_to_owner_when_owner_differs_from_euid(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli',
                   return_value='/home/op/.local/bin/meshtastic'), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch('os.stat', return_value=MagicMock(st_uid=1000)), \
                patch('pwd.getpwuid', return_value=MagicMock(pw_name='op')), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0, stdout='ok')) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd[:4] == ['sudo', '-u', 'op', '-H']
        assert cmd[-4:] == ['pipx', 'install', '--force', 'meshtastic==2.7.9']

    def test_plain_pipx_when_owner_is_current_user(self):
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli',
                   return_value='/usr/local/bin/meshtastic'), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch('os.stat', return_value=MagicMock(st_uid=0)), \
                patch('pwd.getpwuid', return_value=MagicMock(pw_name='root')), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0)) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd == ['pipx', 'install', '--force', 'meshtastic==2.7.9']
        assert 'sudo' not in cmd

    def test_no_cli_targets_real_user_fresh_install(self):
        """No resolved binary → install into the OPERATOR's pipx home
        (SUDO_USER under sudo), never root's by accident — the old install
        path ran `pipx install` in the current (root) context."""
        h = _handler()
        with patch('utils.cli.find_meshtastic_cli', return_value=None), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch('utils.paths.get_real_username', return_value='op'), \
                patch('pwd.getpwnam', return_value=MagicMock(pw_uid=1000)), \
                patch('os.geteuid', return_value=0), \
                patch('handlers.updates.subprocess.run',
                      return_value=_result(0)) as run:
            success, _msg = h._pipx_upgrade_cli()
        assert success is True
        cmd = run.call_args.args[0]
        assert cmd[:4] == ['sudo', '-u', 'op', '-H']
        assert cmd[-1] == 'meshtastic==2.7.9'

    def test_unreadable_floor_refuses_blind_upgrade(self):
        h = _handler()
        with patch('handlers.updates.read_floor', return_value=None):
            success, msg = h._pipx_upgrade_cli()
        assert success is False
        assert 'baseline' in msg.lower()



class TestCliFragmentationRepair:
    """A pip --user script shadowing the pipx shim means pipx upgrades never
    reach what runs — _update_cli must surface and repair it (2026-07-10)."""

    def _versions(self, installed='2.7.9'):
        from types import SimpleNamespace
        return {'cli': SimpleNamespace(
            name='Meshtastic CLI', installed=installed, latest='2.7.9',
            fleet_floor='2.7.9', update_available=False,
            install_command='pipx install meshtastic',
            update_command='pipx upgrade meshtastic')}

    def test_fragmented_cli_offers_and_runs_repair(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._pipx_upgrade_cli = MagicMock(return_value=(True, 'ok'))
        diag = {'path': '/home/op/.local/bin/meshtastic',
                'kind': 'pip-script', 'shebang': '#!/usr/bin/python3'}
        with patch('handlers.updates._check_all_versions',
                   return_value=self._versions()), \
                patch('utils.cli.diagnose_meshtastic_cli', return_value=diag):
            h._update_cli()
        h._pipx_upgrade_cli.assert_called_once_with()
        assert 'Repaired' in _msgbox_titles(h.ctx.dialog)

    def test_pipx_owned_cli_skips_repair(self):
        h = _handler()
        h._pipx_upgrade_cli = MagicMock(return_value=(True, 'ok'))
        diag = {'path': '/home/op/.local/bin/meshtastic', 'kind': 'pipx',
                'shebang': '#!/home/op/.local/share/pipx/venvs/meshtastic/bin/python'}
        with patch('handlers.updates._check_all_versions',
                   return_value=self._versions()), \
                patch('utils.cli.diagnose_meshtastic_cli', return_value=diag):
            h._update_cli()
        h._pipx_upgrade_cli.assert_not_called()
        assert 'No Update' in _msgbox_titles(h.ctx.dialog)



class TestPipInstallMeshtasticFloorPinned:
    """The lib writer must land ON the reviewed floor, not PyPI-latest."""

    def test_upgrade_pins_to_floor(self):
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(True, stdout='ok', verified=True)])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch('handlers.updates.read_floor', return_value='2.7.9'), \
                patch("handlers.updates.pip_install", pi):
            success, _ = h._pip_install_meshtastic(upgrade=True)
        assert success is True
        assert pi.call_args_list[0].args[0] == ['meshtastic==2.7.9']

    def test_upgrade_without_floor_refuses(self):
        h = _handler()
        pi = MagicMock()
        with patch('handlers.updates.read_floor', return_value=None), \
                patch("handlers.updates.pip_install", pi):
            success, msg = h._pip_install_meshtastic(upgrade=True)
        assert success is False
        assert 'baseline' in msg.lower()
        pi.assert_not_called()

    def test_bootstrap_install_without_floor_proceeds_unpinned(self):
        h = _handler()
        pi = MagicMock(side_effect=[PipResult(True, stdout='ok', verified=True)])
        with patch.object(pathlib.Path, "exists", _path_exists(False)), \
                patch('handlers.updates.read_floor', return_value=None), \
                patch("handlers.updates.pip_install", pi):
            success, _ = h._pip_install_meshtastic(upgrade=False)
        assert success is True
        assert pi.call_args_list[0].args[0] == ['meshtastic']



class TestUpdateMeshanchorGitFlow:
    """The 2026-07-10 self-update redesign: git truth, --ff-only, per-step
    honest report, and the running services actually restarted."""

    def _state(self, **kw):
        from updates.meshanchor_git import MeshAnchorGitState
        defaults = dict(is_git_repo=True, repo_dir='/opt/x', head='a' * 40,
                        remote_head='b' * 40, behind=2, ahead=0, dirty=False,
                        update_available=True, fetch_ok=True)
        defaults.update(kw)
        return MeshAnchorGitState(**defaults)

    def test_up_to_date_verified_against_fetch(self):
        h = _handler()
        state = self._state(behind=0, remote_head='a' * 40,
                            update_available=False)
        with patch('handlers.updates.get_meshanchor_git_state',
                   return_value=state), \
                patch('handlers.updates.run_meshanchor_git_update') as run:
            h._update_meshanchor()
        assert 'Up To Date' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_offline_is_unknown_not_up_to_date(self):
        h = _handler()
        state = self._state(fetch_ok=False, error='fetch failed: no route')
        with patch('handlers.updates.get_meshanchor_git_state',
                   return_value=state), \
                patch('handlers.updates.run_meshanchor_git_update') as run:
            h._update_meshanchor()
        assert 'Cannot Verify Remote' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_dirty_tree_refuses(self):
        h = _handler()
        with patch('handlers.updates.get_meshanchor_git_state',
                   return_value=self._state(dirty=True)), \
                patch('handlers.updates.run_meshanchor_git_update') as run:
            h._update_meshanchor()
        assert 'Local Changes Present' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_git_step_failure_aborts_with_report(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        with patch('handlers.updates.get_meshanchor_git_state',
                   return_value=self._state()), \
                patch('handlers.updates.meshanchor_services_to_restart',
                      return_value=['meshanchor']), \
                patch('handlers.updates.run_meshanchor_git_update',
                      return_value=(False, 'ff failed')), \
                patch('handlers.updates.requirements_changed') as req, \
                patch('handlers.updates._apply_config_and_restart') as restart:
            h._update_meshanchor()
        assert 'Update Failed' in _msgbox_titles(h.ctx.dialog)
        req.assert_not_called()       # aborted before the deps step
        restart.assert_not_called()   # and before any restart

    def test_full_path_restarts_active_services(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._refresh_service_files = MagicMock(return_value=(True, 'meshforge.service'))
        after = self._state(head='b' * 40, behind=0, update_available=False)
        with patch('handlers.updates.get_meshanchor_git_state',
                   side_effect=[self._state(), after]), \
                patch('handlers.updates.meshanchor_services_to_restart',
                      return_value=['meshanchor', 'meshanchor-map']), \
                patch('handlers.updates.run_meshanchor_git_update',
                      return_value=(True, 'updated aaaa -> bbbb (verified)')), \
                patch('handlers.updates.requirements_changed',
                      return_value=False), \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')) as restart:
            h._update_meshanchor()
        assert restart.call_count == 2
        restarted = [c.args[0] for c in restart.call_args_list]
        assert restarted == ['meshanchor', 'meshanchor-map']
        assert 'Update Applied' in _msgbox_titles(h.ctx.dialog)

    def test_deps_step_runs_when_diff_unknown(self):
        """None (could-not-determine) must run the deps step — skipping on
        unknown is the silent-failure direction."""
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._refresh_service_files = MagicMock(return_value=(True, 'ok'))
        after = self._state(head='b' * 40)
        with patch('handlers.updates.get_meshanchor_git_state',
                   side_effect=[self._state(), after]), \
                patch('handlers.updates.meshanchor_services_to_restart',
                      return_value=[]), \
                patch('handlers.updates.run_meshanchor_git_update',
                      return_value=(True, 'updated (verified)')), \
                patch('handlers.updates.requirements_changed',
                      return_value=None), \
                patch('handlers.updates.repo_root',
                      return_value=pathlib.Path('/nonexistent-repo')), \
                patch('handlers.updates.pip_install') as pi:
            h._update_meshanchor()
        # requirements.txt missing at the fake root -> deps step FAILS loudly
        # (never silently skipped); pip itself was not reachable.
        titles = _msgbox_titles(h.ctx.dialog)
        assert 'Update Failed' in titles

    def test_restart_failure_is_a_fail_line_not_masked(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        h._refresh_service_files = MagicMock(return_value=(True, 'ok'))
        after = self._state(head='b' * 40)
        with patch('handlers.updates.get_meshanchor_git_state',
                   side_effect=[self._state(), after]), \
                patch('handlers.updates.meshanchor_services_to_restart',
                      return_value=['meshanchor']), \
                patch('handlers.updates.run_meshanchor_git_update',
                      return_value=(True, 'updated (verified)')), \
                patch('handlers.updates.requirements_changed',
                      return_value=False), \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(False, 'unit crashed')):
            h._update_meshanchor()
        assert 'Update Failed' in _msgbox_titles(h.ctx.dialog)
        # The failing unit is named in the report body.
        body = [c.args[1] for c in h.ctx.dialog.msgbox.call_args_list
                if c.args and c.args[0] == 'Update Failed'][0]
        assert 'restart meshanchor' in body
        assert 'unit crashed' in body

