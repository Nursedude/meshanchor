"""Handler + checker tests for the meshtasticd apt update arc (2026-07-10
TUI-updates audit, ported from MeshForge).

The apt-layer module itself is pinned by tests/test_meshtasticd_apt.py (a
byte-port of MeshForge's); this file pins the MA-side wiring: the handler
state machine and check_all_versions' candidate/hold semantics.
"""

from unittest.mock import MagicMock, patch

import pytest

from handlers.updates import UpdatesHandler


def _handler():
    h = UpdatesHandler()
    h.ctx = MagicMock()
    return h


def _msgbox_titles(dialog):
    return [c.args[0] for c in dialog.msgbox.call_args_list if c.args]


class TestUpdateMeshtasticdFlow:
    """_update_meshtasticd drives the apt state machine (candidate truth,
    hold as deliberate pin, dry-run installability, guided repo repair)
    instead of a blind `apt upgrade` string."""

    def _state(self, **kw):
        from updates.meshtasticd_apt import MeshtasticdAptState
        defaults = dict(
            apt_available=True,
            installed='2.7.24.58~obs472b14c~alpha',
            candidate='2.7.26.61~obs54e0d8d~beta',
            held=False, update_available=True, candidate_installable=True,
        )
        defaults.update(kw)
        return MeshtasticdAptState(**defaults)

    def test_up_to_date_reports_no_update(self):
        h = _handler()
        state = self._state(candidate='2.7.24.58~obs472b14c~alpha',
                            update_available=False, candidate_installable=None)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=state), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'No Update' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_held_decline_runs_nothing(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=False)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=self._state(held=True)), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        run.assert_not_called()
        assert h.ctx.dialog.yesno.call_args.kwargs.get('default_no') is True

    def test_held_accept_unholds_and_reholds(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=self._state(held=True)), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'ok 2.7.24 -> 2.7.26')) as run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_meshtasticd()
        run.assert_called_once_with(unhold=True, rehold=True)

    def test_broken_candidate_without_repo_diagnosis_stops_honestly(self):
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        state = self._state(candidate_installable=False,
                            blocking_detail='E: unmet deps: libc6 (>= 2.42)',
                            mismatched_repos=[])
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=state), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'Candidate Not Installable' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_broken_candidate_guided_repair_then_upgrade(self):
        from updates.meshtasticd_apt import RepoLine
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        bad = RepoLine('/etc/apt/sources.list.d/meshtastic.list', 1,
                       'deb .../Meshtastic:/beta/Debian_Testing/ /')
        broken = self._state(candidate_installable=False,
                             blocking_detail='E: libc6 (>= 2.42)',
                             mismatched_repos=[bad])
        repaired = self._state(candidate_installable=True)
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   side_effect=[broken, repaired]) as get_state, \
                patch('handlers.updates.disable_repo_lines',
                      return_value=(True, 'disabled')) as disable, \
                patch('handlers.updates.apt_update',
                      return_value=(True, 'refreshed')), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'verified')) as run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_meshtasticd()
        disable.assert_called_once_with([bad])
        assert get_state.call_count == 2  # never proceeds on the stale state
        run.assert_called_once()

    def test_repair_that_does_not_cure_stops(self):
        from updates.meshtasticd_apt import RepoLine
        h = _handler()
        h.ctx.dialog.yesno = MagicMock(return_value=True)
        bad = RepoLine('/etc/apt/x.list', 1, 'deb bad /')
        broken = self._state(candidate_installable=False,
                             blocking_detail='E: nope', mismatched_repos=[bad])
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   side_effect=[broken, broken]), \
                patch('handlers.updates.disable_repo_lines',
                      return_value=(True, 'disabled')), \
                patch('handlers.updates.apt_update', return_value=(True, 'ok')), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'Still Blocked' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()

    def test_not_installed_points_at_installer(self):
        h = _handler()
        with patch('handlers.updates.get_meshtasticd_apt_state',
                   return_value=self._state(installed=None,
                                            update_available=False,
                                            candidate_installable=None)), \
                patch('handlers.updates.run_meshtasticd_upgrade') as run:
            h._update_meshtasticd()
        assert 'Not Installed' in _msgbox_titles(h.ctx.dialog)
        run.assert_not_called()


class TestUpdateAllRoutesMeshtasticd:
    """Update All must route meshtasticd through the verified apt runner,
    never the raw command string."""

    def test_meshtasticd_uses_apt_runner(self):
        from types import SimpleNamespace
        h = _handler()  # ctx MagicMock -> yesno truthy
        versions = {
            'meshtasticd': SimpleNamespace(
                name='meshtasticd', update_available=True,
                update_command='sudo apt-get install --only-upgrade -y meshtasticd'),
        }
        h._run_update_command = MagicMock(return_value=(True, 'raw'))
        with patch('handlers.updates._check_all_versions', return_value=versions), \
                patch('handlers.updates.run_meshtasticd_upgrade',
                      return_value=(True, 'apt verified')) as apt_run, \
                patch('handlers.updates._apply_config_and_restart',
                      return_value=(True, 'restarted')):
            h._update_all()
        apt_run.assert_called_once_with()
        routed = [c.args[0] for c in h._run_update_command.call_args_list]
        assert 'meshtasticd' not in routed


class TestCheckAllVersionsMeshtasticdApt:
    """check_all_versions judges meshtasticd by the apt CANDIDATE and treats a
    hold as a deliberate pin (surfaced in notes, never a nagging update)."""

    def _snapshot(self, **kw):
        from updates.meshtasticd_apt import MeshtasticdAptState
        defaults = dict(apt_available=True, installed='2.7.24', candidate='2.7.26',
                        held=False, update_available=True)
        defaults.update(kw)
        return MeshtasticdAptState(**defaults)

    def _run_check(self, vc, snapshot):
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in [
                patch.object(vc, 'get_meshtasticd_apt_snapshot',
                             return_value=snapshot),
                patch.object(vc, 'get_meshtastic_cli_version', return_value=None),
                patch.object(vc, 'get_meshtastic_lib_version', return_value=None),
                patch.object(vc, 'get_latest_meshtastic_cli_version',
                             return_value=None),
                patch.object(vc, 'get_meshanchor_version', return_value=None),
                patch.object(vc, 'get_latest_meshanchor_version', return_value=None),
                patch.object(vc, 'get_meshtasticd_version', return_value=None),
                patch.object(vc, 'get_latest_meshtasticd_version', return_value=None),
                patch.object(vc, 'get_node_firmware_version', return_value=None),
                patch.object(vc, 'get_latest_firmware_version', return_value=None),
            ]:
                stack.enter_context(p)
            return vc.check_all_versions()

    def test_candidate_is_the_latest_source(self):
        import updates.version_checker as vc
        results = self._run_check(vc, self._snapshot())
        info = results['meshtasticd']
        assert info.installed == '2.7.24'
        assert info.latest == '2.7.26'
        assert info.update_available is True
        assert info.held is False

    def test_hold_is_a_pin_not_a_nag(self):
        import updates.version_checker as vc
        results = self._run_check(vc, self._snapshot(held=True))
        info = results['meshtasticd']
        assert info.update_available is False
        assert info.held is True
        assert 'pinned' in info.notes
        assert '2.7.26' in info.notes  # the waiting candidate is visible

    def test_apt_unavailable_falls_back_to_legacy(self):
        import updates.version_checker as vc
        results = self._run_check(vc, None)
        info = results['meshtasticd']
        assert info.installed is None
        assert info.update_available is False

    def test_summary_exposes_held_and_notes(self):
        import updates.version_checker as vc
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in [
                patch.object(vc, 'get_meshtasticd_apt_snapshot',
                             return_value=self._snapshot(held=True)),
                patch.object(vc, 'get_meshtastic_cli_version', return_value=None),
                patch.object(vc, 'get_meshtastic_lib_version', return_value=None),
                patch.object(vc, 'get_latest_meshtastic_cli_version',
                             return_value=None),
                patch.object(vc, 'get_meshanchor_version', return_value=None),
                patch.object(vc, 'get_latest_meshanchor_version', return_value=None),
                patch.object(vc, 'get_node_firmware_version', return_value=None),
                patch.object(vc, 'get_latest_firmware_version', return_value=None),
            ]:
                stack.enter_context(p)
            summary = vc.get_version_summary()
        d = next(c for c in summary['components'] if c['id'] == 'meshtasticd')
        assert d['held'] is True
        assert 'pinned' in d['notes']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
