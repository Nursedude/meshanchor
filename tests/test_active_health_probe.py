"""Tests for active_health_probe — focuses on noc.yaml `managed: false` honored
by create_gateway_health_probe(). Without this, MeshCore-only boxes
(--skip-meshtasticd installs) emit UNHEALTHY warnings every 30s for
services they intentionally don't run.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class TestUnmanagedServices:
    """_unmanaged_services() reads /etc/meshanchor/noc.yaml and returns the
    set of service names with `managed: false`."""

    def test_returns_empty_when_yaml_missing(self, tmp_path):
        from utils import active_health_probe as ahp
        with patch.object(ahp, 'Path', return_value=tmp_path / "missing.yaml"):
            assert ahp._unmanaged_services() == set()

    def test_returns_empty_when_yaml_malformed(self, tmp_path):
        from utils import active_health_probe as ahp
        bad = tmp_path / "noc.yaml"
        bad.write_text(":\n  not: [valid")
        with patch.object(ahp, 'Path', return_value=bad):
            assert ahp._unmanaged_services() == set()

    def test_extracts_managed_false_services_nested(self, tmp_path):
        """install_noc.sh emits services nested under top-level `noc:` —
        this is the canonical shape on every fleet box."""
        from utils import active_health_probe as ahp
        cfg = tmp_path / "noc.yaml"
        cfg.write_text(
            "noc:\n"
            "  services:\n"
            "    meshtasticd:\n"
            "      managed: false\n"
            "    rnsd:\n"
            "      managed: true\n"
            "    mosquitto:\n"
            "      managed: false\n"
        )
        with patch.object(ahp, 'Path', return_value=cfg):
            assert ahp._unmanaged_services() == {"meshtasticd", "mosquitto"}

    def test_extracts_managed_false_services_flat(self, tmp_path):
        """Hand-edited flat configs (no `noc:` wrapper) must still work —
        this was the fixture shape used pre-fix."""
        from utils import active_health_probe as ahp
        cfg = tmp_path / "noc.yaml"
        cfg.write_text(
            "services:\n"
            "  meshtasticd:\n"
            "    managed: false\n"
        )
        with patch.object(ahp, 'Path', return_value=cfg):
            assert ahp._unmanaged_services() == {"meshtasticd"}

    def test_default_managed_true_when_unspecified(self, tmp_path):
        """Service entry without explicit `managed:` defaults to managed=True
        — must NOT appear in unmanaged set."""
        from utils import active_health_probe as ahp
        cfg = tmp_path / "noc.yaml"
        cfg.write_text(
            "noc:\n"
            "  services:\n"
            "    meshtasticd:\n"
            "      auto_start: true\n"
        )
        with patch.object(ahp, 'Path', return_value=cfg):
            assert ahp._unmanaged_services() == set()


class TestCreateGatewayHealthProbe:
    """create_gateway_health_probe skips checks for unmanaged services.

    Phase 5.5 added a second filter (profile-aware ``is_managed``) on top
    of the noc.yaml ``managed: false`` filter. These tests pin a profile
    where all three services are managed (FULL) so they continue to
    exercise the noc.yaml filter in isolation — without the patch they'd
    pick up whatever the CI runner auto-detects (typically MESHCORE on a
    clean image, where nothing is managed).
    """

    def _full_profile(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            required_services=["rnsd", "mosquitto"],
            optional_services=["meshtasticd"],
            feature_flags={},
        )

    def test_unmanaged_service_not_registered(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, '_unmanaged_services', return_value={"meshtasticd"}), \
             patch("utils.profile_services._active_profile",
                   return_value=self._full_profile()):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert "meshtasticd" not in registered, (
            "managed=false meshtasticd must not be probed — would emit "
            "UNHEALTHY noise on MeshCore-only boxes"
        )
        assert "rnsd" in registered
        assert "mosquitto" in registered

    def test_all_managed_registers_all(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, '_unmanaged_services', return_value=set()), \
             patch("utils.profile_services._active_profile",
                   return_value=self._full_profile()):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert {"meshtasticd", "rnsd", "mosquitto"}.issubset(registered)


class TestRNSWedgeProbes:
    """RNS-reliability parity port (2026-05-31): two HealthResult probes for
    the rnsd-RPC fragility class that check_rns_port can't see —
      check_rns_rpc_responsive:                 #68/#72 wedged-RPC
      check_rns_interface_down_peer_reachable:  2026-05-30 stuck-uplink islanding
    """

    def _probe(self):
        from utils.active_health_probe import ActiveHealthProbe
        return ActiveHealthProbe()

    # --- check_rns_rpc_responsive ---

    def test_rpc_timeout_is_unhealthy(self):
        from utils import rns_status_parser as rsp
        from utils.rns_status_parser import RNSStatus
        with patch.object(rsp, "run_rnstatus",
                          return_value=RNSStatus(parse_error="timed out", timed_out=True)):
            r = self._probe().check_rns_rpc_responsive()
            assert r.healthy is False
            assert "rns_rpc_unresponsive" in r.reason

    def test_rpc_healthy_when_not_timed_out(self):
        from utils import rns_status_parser as rsp
        from utils.rns_status_parser import RNSStatus
        with patch.object(rsp, "run_rnstatus", return_value=RNSStatus()):
            assert self._probe().check_rns_rpc_responsive().healthy is True

    def test_rpc_fast_error_is_healthy_not_wedge(self):
        """A down rnsd fails FAST (timed_out False) — service/port probes own
        that, so the RPC-wedge probe must NOT alarm."""
        from utils import rns_status_parser as rsp
        from utils.rns_status_parser import RNSStatus
        with patch.object(rsp, "run_rnstatus",
                          return_value=RNSStatus(parse_error="no shared instance")):
            assert self._probe().check_rns_rpc_responsive().healthy is True

    def test_rpc_passes_timeout_s_through(self):
        from utils import rns_status_parser as rsp
        from utils.rns_status_parser import RNSStatus
        with patch.object(rsp, "run_rnstatus", return_value=RNSStatus()) as m:
            self._probe().check_rns_rpc_responsive(timeout_s=4.0)
            assert m.call_args.kwargs.get("timeout_s") == 4.0

    # --- check_rns_interface_down_peer_reachable ---

    def _down_tcp_status(self):
        from utils.rns_status_parser import RNSStatus, RNSInterface, InterfaceStatus
        return RNSStatus(interfaces=[RNSInterface(
            type_name="TCPInterface",
            display_name="Regional RNS/192.168.1.5:4242",
            status=InterfaceStatus.DOWN,
        )])

    def test_iface_down_peer_reachable_is_unhealthy(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, "_tcp_reachable", return_value=True):
            r = self._probe().check_rns_interface_down_peer_reachable(
                rnstatus_status=self._down_tcp_status())
            assert r.healthy is False
            assert "rns_interface_down_peer_reachable" in r.reason
            assert "192.168.1.5:4242" in r.reason

    def test_iface_down_peer_unreachable_is_healthy(self):
        """Down + peer NOT reachable = genuine outage, owned elsewhere."""
        from utils import active_health_probe as ahp
        with patch.object(ahp, "_tcp_reachable", return_value=False):
            assert self._probe().check_rns_interface_down_peer_reachable(
                rnstatus_status=self._down_tcp_status()).healthy is True

    def test_iface_up_is_healthy(self):
        from utils import active_health_probe as ahp
        from utils.rns_status_parser import RNSStatus, RNSInterface, InterfaceStatus
        up = RNSStatus(interfaces=[RNSInterface(
            type_name="TCPInterface",
            display_name="Regional RNS/192.168.1.5:4242",
            status=InterfaceStatus.UP,
        )])
        with patch.object(ahp, "_tcp_reachable", return_value=True):
            assert self._probe().check_rns_interface_down_peer_reachable(
                rnstatus_status=up).healthy is True

    def test_iface_parse_error_is_healthy(self):
        from utils.rns_status_parser import RNSStatus
        assert self._probe().check_rns_interface_down_peer_reachable(
            rnstatus_status=RNSStatus(parse_error="rnsd down")).healthy is True

    def test_iface_non_tcp_down_ignored(self):
        """A non-TCP interface Down has no routable peer — must be ignored."""
        from utils import active_health_probe as ahp
        from utils.rns_status_parser import RNSStatus, RNSInterface, InterfaceStatus
        st = RNSStatus(interfaces=[RNSInterface(
            type_name="AutoInterface",
            display_name="Default Interface",
            status=InterfaceStatus.DOWN,
        )])
        with patch.object(ahp, "_tcp_reachable", return_value=True):
            assert self._probe().check_rns_interface_down_peer_reachable(
                rnstatus_status=st).healthy is True

    # --- _tcp_reachable helper ---

    def test_tcp_reachable_true_on_connect(self):
        from utils import active_health_probe as ahp
        with patch("socket.create_connection") as mc:
            mc.return_value.__enter__ = lambda s: s
            mc.return_value.__exit__ = lambda s, *a: False
            assert ahp._tcp_reachable("10.0.0.1", 4242) is True

    def test_tcp_reachable_false_on_oserror(self):
        from utils import active_health_probe as ahp
        with patch("socket.create_connection", side_effect=OSError("refused")):
            assert ahp._tcp_reachable("10.0.0.1", 4242) is False


class TestRNSWedgeProbesRegistered:
    """The two probes must be registered (gated behind rnsd) so they run live
    on the existing 30s cadence — otherwise they're dead code."""

    def _full_profile(self):
        # Same shape TestCreateGatewayHealthProbe uses: a profile where rnsd
        # is managed so the gate opens.
        from types import SimpleNamespace
        return SimpleNamespace(
            required_services=["rnsd", "mosquitto"],
            optional_services=["meshtasticd"],
            feature_flags={},
        )

    def test_rnsd_wedge_probes_registered_when_rnsd_managed(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, "_unmanaged_services", return_value=set()), \
             patch("utils.profile_services._active_profile",
                   return_value=self._full_profile()):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert "rnsd_rpc" in registered
        assert "rnsd_interface" in registered

    def test_rnsd_wedge_probes_skipped_when_rnsd_unmanaged(self):
        from utils import active_health_probe as ahp
        with patch.object(ahp, "_unmanaged_services", return_value={"rnsd"}), \
             patch("utils.profile_services._active_profile",
                   return_value=self._full_profile()):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert "rnsd_rpc" not in registered
        assert "rnsd_interface" not in registered


def _fake_proc(tmp_path, pid, *, open_fds, soft="1024", hard="524288"):
    """Build a fake /proc/<pid> with `open_fds` fd entries + a limits file."""
    pdir = tmp_path / str(pid)
    fd_dir = pdir / "fd"
    fd_dir.mkdir(parents=True)
    for i in range(open_fds):
        (fd_dir / str(i)).write_text("")
    limits = (
        "Limit                     Soft Limit           Hard Limit           Units\n"
        "Max open files            {soft}                 {hard}               files\n"
    ).format(soft=soft, hard=hard)
    (pdir / "limits").write_text(limits)
    return str(tmp_path)


class TestFdExhaustionProbe:
    """MeshForge Issue #73 parity port (2026-05-31): proactive fd-leak probe.
    Counts /proc/<MainPID>/fd vs the soft RLIMIT_NOFILE and flags BEFORE the
    map's :5000 wedges (the original incident was meshanchor-server itself)."""

    def _probe(self):
        from utils.active_health_probe import ActiveHealthProbe
        return ActiveHealthProbe()

    def test_quiet_when_healthy(self, tmp_path):
        root = _fake_proc(tmp_path, 4242, open_fds=50, soft="1024")
        r = self._probe().check_fd_exhaustion(
            "meshanchor-map.service", proc_root=root, main_pid=4242)
        assert r.healthy is True

    def test_degraded_past_80pct(self, tmp_path):
        root = _fake_proc(tmp_path, 4242, open_fds=820, soft="1024")
        r = self._probe().check_fd_exhaustion(
            "meshanchor-map.service", proc_root=root, main_pid=4242)
        assert r.healthy is False
        assert "fd_exhaustion (degraded)" in r.reason

    def test_wedge_past_95pct(self, tmp_path):
        root = _fake_proc(tmp_path, 4242, open_fds=1000, soft="1024")
        r = self._probe().check_fd_exhaustion(
            "meshanchor-map.service", proc_root=root, main_pid=4242)
        assert r.healthy is False
        assert "fd_exhaustion (wedge)" in r.reason
        assert "[Errno 24]" in r.reason

    def test_healthy_when_pid_unresolved(self, tmp_path):
        r = self._probe().check_fd_exhaustion(
            "meshanchor-map.service", proc_root=str(tmp_path), main_pid=None,
            systemctl_path="/nonexistent/systemctl")
        assert r.healthy is True
        assert r.reason == "inactive_or_unresolved"

    def test_healthy_when_proc_vanished(self, tmp_path):
        r = self._probe().check_fd_exhaustion(
            "meshanchor-map.service", proc_root=str(tmp_path), main_pid=99999)
        assert r.healthy is True
        assert r.reason == "fd_usage_unreadable"

    def test_healthy_when_soft_limit_unlimited(self, tmp_path):
        root = _fake_proc(tmp_path, 4242, open_fds=9000, soft="unlimited")
        r = self._probe().check_fd_exhaustion(
            "meshanchor-map.service", proc_root=root, main_pid=4242)
        assert r.healthy is True

    def test_fd_probe_registered_in_factory(self):
        """Both fd checks must be wired into create_gateway_health_probe (else
        dead code). Registered unconditionally — each self-guards when its
        service is down. The daemon check was added after the 2026-05-31
        message_queue incident (daemon was the 2nd fd-leak victim)."""
        from utils import active_health_probe as ahp
        with patch.object(ahp, "_unmanaged_services", return_value=set()):
            probe = ahp.create_gateway_health_probe()
        registered = set(probe._checks.keys())
        assert "meshanchor_map_fds" in registered
        assert "meshanchor_daemon_fds" in registered

    def test_daemon_fd_check_targets_daemon_service(self, tmp_path):
        """The daemon fd check resolves meshanchor-daemon.service, not the map."""
        root = _fake_proc(tmp_path, 7777, open_fds=1000, soft="1024")
        r = self._probe().check_fd_exhaustion(
            "meshanchor-daemon.service", proc_root=root, main_pid=7777)
        assert r.healthy is False
        assert "meshanchor-daemon.service" in r.reason
