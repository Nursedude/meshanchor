"""Tests for FleetHealthHandler (T0 — Stack Health diagnostic surface).

Mirror of MeshForge's tests, adapted for MeshAnchor's service names
and DB path.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401  (kept for parity / future param tests)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))

from handlers.fleet_health import (  # noqa: E402
    FleetHealthHandler,
    ProbeResult,
)


def _handler() -> FleetHealthHandler:
    return FleetHealthHandler()


# ----------------------------------------------------------------- helpers


def test_humanize_duration_thresholds():
    fn = FleetHealthHandler._humanize_duration
    assert fn(30) == "30s"
    assert fn(90) == "1 min"
    assert fn(3600) == "1.0 hr"
    assert fn(7200) == "2.0 hr"
    assert fn(86400 * 2) == "2.0 days"


def test_first_host_strips_port():
    assert FleetHealthHandler._first_host("192.168.86.38:4242") == "192.168.86.38"


# ----------------------------------------------------------------- rnsd probe


def test_probe_rnsd_inactive(monkeypatch):
    fake_status = MagicMock(available=False)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    r = _handler()._probe_rnsd()
    assert r.status == "fail"
    assert "not running" in r.headline


def test_probe_rnsd_active(monkeypatch):
    fake_status = MagicMock(available=True)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_service_uptime_seconds",
        classmethod(lambda cls, unit: 3600 * 24 * 2),
    )
    r = _handler()._probe_rnsd()
    assert r.status == "ok"
    assert "2.0 days" in r.headline


# ----------------------------------------------------------- RNS path table


def test_probe_rns_path_table_no_rnpath(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    r = _handler()._probe_rns_path_table()
    assert r.status == "info"
    assert "not installed" in r.headline


def test_probe_rns_path_table_empty(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/rnpath")
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_rns_path_table()
    assert r.status == "warn"
    assert "empty" in r.headline


def test_probe_rns_path_table_populated(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/rnpath")
    sample = (
        "<aaa> is 1 hop  away via <hub> on AutoInterfacePeer[eth0] expires X\n"
        "<bbb> is 2 hops away via <hub> on TCPInterface[Hub/X:4242] expires X\n"
        "<ccc> is 0 hops away via <self> on LocalInterface[rns/default] expires X\n"
        "<ddd> is 0 hops away via <self> on LocalInterface[rns/default] expires X\n"
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: sample))
    r = _handler()._probe_rns_path_table()
    assert r.status == "ok"
    assert "2 network destinations" in r.headline
    assert "2 local IPC peers" in r.headline


# ----------------------------------------------------------- RNS hub peers


def test_probe_rns_hub_peers_inbound(monkeypatch):
    out = (
        "ESTAB 0 0 192.168.86.38:4242 192.168.86.29:46146\n"
        "ESTAB 0 0 192.168.86.38:4242 192.168.86.249:47048\n"
        "ESTAB 0 0 192.168.86.38:22   192.168.86.29:55555\n"
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: out))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "ok"
    assert "2 inbound" in r.headline


def test_probe_rns_hub_peers_outbound(monkeypatch):
    out = "ESTAB 0 0 192.168.86.29:46146 192.168.86.38:4242\n"
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: out))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "ok"
    assert "1 outbound" in r.headline
    assert "192.168.86.38" in r.headline


def test_probe_rns_hub_peers_none(monkeypatch):
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_rns_hub_peers()
    assert r.status == "info"
    assert "no :4242 TCP sessions" in r.headline


# ----------------------------------------------------------------- NomadNet


def test_probe_nomadnet_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    r = _handler()._probe_nomadnet()
    assert r.status == "info"
    assert "not installed" in r.headline


def test_probe_nomadnet_quiet_fail(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    nndir = tmp_path / ".nomadnetwork"
    nndir.mkdir()
    logfile = nndir / "logfile"
    logfile.write_text("old\n")
    old = time.time() - 86400 * 2
    os.utime(logfile, (old, old))
    monkeypatch.setattr(FleetHealthHandler, "_pgrep_count", staticmethod(lambda _: 1))
    r = _handler()._probe_nomadnet()
    assert r.status == "fail"
    assert "QUIET" in r.headline
    assert "delay" in (r.hint or "")


def test_probe_nomadnet_daemon_dead(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    nndir = tmp_path / ".nomadnetwork"
    nndir.mkdir()
    (nndir / "logfile").write_text("x\n")
    monkeypatch.setattr(FleetHealthHandler, "_pgrep_count", staticmethod(lambda _: 0))
    r = _handler()._probe_nomadnet()
    assert r.status == "fail"
    assert "daemon NOT running" in r.headline


def test_probe_nomadnet_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    nndir = tmp_path / ".nomadnetwork"
    nndir.mkdir()
    (nndir / "logfile").write_text("x\n")
    monkeypatch.setattr(FleetHealthHandler, "_pgrep_count", staticmethod(lambda _: 1))
    r = _handler()._probe_nomadnet()
    assert r.status == "ok"


# ----------------------------------------------------------- LXMF queue


def test_probe_lxmf_queue_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    outdir = tmp_path / ".nomadnetwork" / "storage" / "messages" / "outbound"
    outdir.mkdir(parents=True)
    r = _handler()._probe_lxmf_queue()
    assert r.status == "ok"
    assert "empty" in r.headline


def test_probe_lxmf_queue_stuck(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    outdir = tmp_path / ".nomadnetwork" / "storage" / "messages" / "outbound"
    outdir.mkdir(parents=True)
    for i in range(15):
        (outdir / f"msg{i}").write_text("x")
    r = _handler()._probe_lxmf_queue()
    assert r.status == "fail"
    assert "stuck pending" in r.headline


def test_probe_lxmf_queue_normal(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    outdir = tmp_path / ".nomadnetwork" / "storage" / "messages" / "outbound"
    outdir.mkdir(parents=True)
    (outdir / "msg1").write_text("x")
    (outdir / "msg2").write_text("x")
    r = _handler()._probe_lxmf_queue()
    assert r.status == "warn"
    assert "2 pending" in r.headline


# ---------------------------------------------------- meshanchor-daemon


def test_probe_meshanchor_daemon_inactive(monkeypatch):
    fake_status = MagicMock(available=False)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    r = _handler()._probe_meshanchor_daemon()
    assert r.status == "info"
    assert "not running" in r.headline


def test_probe_meshanchor_daemon_silent(monkeypatch):
    fake_status = MagicMock(available=True)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_service_uptime_seconds",
        classmethod(lambda cls, unit: 7200),
    )
    # Journal returns empty -> warn for "no recent output".
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_meshanchor_daemon()
    assert r.status == "warn"
    assert "no recent journal output" in r.headline


def test_probe_meshanchor_daemon_active(monkeypatch):
    fake_status = MagicMock(available=True)
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: fake_status,
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_service_uptime_seconds",
        classmethod(lambda cls, unit: 7200),
    )
    monkeypatch.setattr(
        FleetHealthHandler,
        "_run",
        staticmethod(lambda *a, **k: "May 11 08:25 daemon log line\n"),
    )
    r = _handler()._probe_meshanchor_daemon()
    assert r.status == "ok"
    assert "2.0 hr" in r.headline


# ---------------------------------------------------- peer-gateway probe


def test_probe_peer_gateways_daemon_inactive(monkeypatch):
    """If meshanchor-daemon isn't running, the probe is N/A (info)."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=False),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "info"
    assert "not applicable" in r.headline


def test_probe_peer_gateways_silent_journal(monkeypatch):
    """Daemon up but journalctl returns nothing — warn, not fail."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    monkeypatch.setattr(FleetHealthHandler, "_run", staticmethod(lambda *a, **k: ""))
    r = _handler()._probe_peer_gateways()
    assert r.status == "warn"
    assert "no daemon journal output" in r.headline


def test_probe_peer_gateways_no_peers_in_log(monkeypatch):
    """Daemon active, journal has output but no peer lines — isolation warn."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: "May 11 08:25 just a regular log line\n"),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "warn"
    assert "no peer-gateway log entries" in r.headline


def test_probe_peer_gateways_one_live_peer(monkeypatch):
    """One discovered peer, still live (no DOWN line)."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "May 11 08:20 ma-daemon[123]: Discovered peer gateway: moc3-mf "
        "(role=meshtastic)\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "1 live" in r.headline
    assert "moc3-mf" in r.headline


def test_probe_peer_gateways_one_live_one_down(monkeypatch):
    """Two peers, one alive, one DOWN."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "May 11 08:20 ma-daemon[123]: Discovered peer gateway: moc3-mf "
        "(role=meshtastic)\n"
        "May 11 08:21 ma-daemon[123]: Discovered peer gateway: peer-2 (role=test)\n"
        "May 11 08:25 ma-daemon[123]: GATEWAY HEARTBEAT: peer peer-2 is DOWN\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "1 live" in r.headline
    assert "1 DOWN" in r.headline
    assert "moc3-mf" in r.headline


def test_probe_peer_gateways_all_down(monkeypatch):
    """All known peers are DOWN — fail."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "May 11 08:20 ma-daemon[123]: Discovered peer gateway: only-peer "
        "(role=test)\n"
        "May 11 08:25 ma-daemon[123]: GATEWAY HEARTBEAT: peer only-peer is DOWN\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "fail"
    assert "all marked DOWN" in r.headline


def test_probe_peer_gateways_node_tracker_signal(monkeypatch):
    """Production signal: node_tracker RNS announces (heartbeat off).

    This is the line shape that ACTUALLY fires in production today.
    The probe must surface peer-gateway-named RNS nodes as live peers
    even when the heartbeat MQTT feature is disabled.
    """
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "May 11 16:39:42 host py[1]: 2026-05-11 16:39:42 | "
        "gateway.node_tracker | INFO | Discovered RNS node: 3dfbdb5d "
        "(MeshForge Gateway (moc)) [LXMF_DELIVERY]\n"
        "May 11 16:46:26 host py[1]: 2026-05-11 16:46:26 | "
        "gateway.node_tracker | INFO | Discovered RNS node: 627fa566 "
        "(MeshAnchor Broadcast) [LXMF_DELIVERY]\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "2 live" in r.headline
    assert "MeshForge Gateway" in r.headline or "MeshAnchor Broadcast" in r.headline


def test_probe_peer_gateways_handles_nested_parens_in_name(monkeypatch):
    """Production names have nested parens — must not truncate at inner ).

    Real journal line on moc3:
        Discovered RNS node: 3dfbdb5d (MeshForge Gateway (moc)) [LXMF_DELIVERY]

    Parser must extract `MeshForge Gateway (moc)` (with closing paren),
    not `MeshForge Gateway (moc` (truncated at the inner close).
    """
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "Discovered RNS node: 3dfbdb5d (MeshForge Gateway (moc)) "
        "[LXMF_DELIVERY]\n"
        "Discovered RNS node: f68c2f56 (MeshForge Gateway (moc3)) "
        "[LXMF_DELIVERY]\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    # Both names should appear in full, with their closing parens.
    assert "MeshForge Gateway (moc)" in r.headline
    assert "MeshForge Gateway (moc3)" in r.headline


def test_probe_peer_gateways_ignores_non_gateway_rns_nodes(monkeypatch):
    """RNS announces from non-gateway destinations must NOT count.

    The fleet has lots of LXMF identities — NomadNet clients, lab
    echo daemons, validator scripts. None should be counted as peer
    gateways. Only display names matching 'Gateway' or 'Broadcast'
    qualify.
    """
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "Discovered RNS node: aaa (lab-echo (volcanoai)) [LXMF_DELIVERY]\n"
        "Discovered RNS node: bbb (random nomadnet user) [LXMF_DELIVERY]\n"
        "Discovered RNS node: ccc (validator) [LXMF_DELIVERY]\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "warn"
    assert "no peer-gateway log entries" in r.headline


def test_probe_peer_gateways_recovery(monkeypatch):
    """A peer marked DOWN then RECOVERED comes back to live count."""
    monkeypatch.setattr(
        "utils.service_check.check_service",
        lambda name: MagicMock(available=True),
    )
    journal = (
        "May 11 08:20 ma-daemon[123]: Discovered peer gateway: bouncy (role=t)\n"
        "May 11 08:21 ma-daemon[123]: GATEWAY HEARTBEAT: peer bouncy is DOWN\n"
        "May 11 08:25 ma-daemon[123]: GATEWAY HEARTBEAT: peer bouncy RECOVERED\n"
    )
    monkeypatch.setattr(
        FleetHealthHandler, "_run",
        staticmethod(lambda *a, **k: journal),
    )
    r = _handler()._probe_peer_gateways()
    assert r.status == "ok"
    assert "1 live" in r.headline


# ----------------------------------------------------------------- Map DB


def test_probe_map_db_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    # MeshAnchor's DB path uses "meshanchor", not "meshforge".
    dbdir = tmp_path / ".local" / "share" / "meshanchor"
    dbdir.mkdir(parents=True)
    db = dbdir / "node_history.db"
    with open(db, "wb") as f:
        f.seek(int(5.5 * 1024**3))
        f.write(b"\0")
    wal = db.with_name(db.name + "-wal")
    with open(wal, "wb") as f:
        f.seek(250 * 1024 * 1024)
        f.write(b"\0")
    r = _handler()._probe_map_db()
    assert r.status == "fail"
    assert "5.5 GB" in r.headline
    assert "wal 250 MB" in r.headline


def test_probe_map_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.paths.get_real_user_home", lambda: tmp_path)
    r = _handler()._probe_map_db()
    assert r.status == "info"
    assert "no node_history.db" in r.headline


# ------------------------------------------------------------------ handler


def test_handler_registration_shape():
    h = _handler()
    assert h.handler_id == "fleet_health"
    assert h.menu_section == "dashboard"
    items = h.menu_items()
    assert len(items) == 1
    tag, label, gate = items[0]
    assert tag == "datapath"
    assert "Stack Health" in label
    assert gate is None


def test_render_overview_does_not_raise(monkeypatch, capsys):
    """Smoke: with all probes mocked to fixed results, render the screen."""
    h = _handler()
    ctx = MagicMock()
    ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
    ctx.wait_for_enter = lambda *_: None
    h.set_context(ctx)

    def _fake(label, status="ok"):
        return ProbeResult(label=label, status=status, headline=f"{label} headline")

    monkeypatch.setattr(h, "_probe_rnsd", lambda: _fake("rnsd"))
    monkeypatch.setattr(h, "_probe_rns_path_table", lambda: _fake("path"))
    monkeypatch.setattr(h, "_probe_rns_hub_peers", lambda: _fake("hub"))
    monkeypatch.setattr(h, "_probe_nomadnet", lambda: _fake("nomadnet", "warn"))
    monkeypatch.setattr(h, "_probe_lxmf_queue", lambda: _fake("queue"))
    monkeypatch.setattr(h, "_probe_meshanchor_daemon", lambda: _fake("daemon"))
    monkeypatch.setattr(h, "_probe_peer_gateways", lambda: _fake("peers"))
    monkeypatch.setattr(h, "_probe_map_db", lambda: _fake("db"))
    monkeypatch.setattr(h, "_probe_meshtasticd_radio", lambda: _fake("radio"))

    with patch("backend.clear_screen", lambda: None):
        h.execute("datapath")

    out = capsys.readouterr().out
    assert "Stack Health" in out
    assert "[ OK ]" in out
    assert "[WARN]" in out


def test_probe_exception_is_isolated(monkeypatch, capsys):
    """If one probe raises, the screen still renders the others."""
    h = _handler()
    ctx = MagicMock()
    ctx.safe_call = lambda name, fn, *a, **kw: fn(*a, **kw)
    ctx.wait_for_enter = lambda *_: None
    h.set_context(ctx)

    def boom():
        raise RuntimeError("bang")

    monkeypatch.setattr(h, "_probe_rnsd", boom)
    for name in (
        "_probe_rns_path_table", "_probe_rns_hub_peers", "_probe_nomadnet",
        "_probe_lxmf_queue", "_probe_meshanchor_daemon",
        "_probe_peer_gateways", "_probe_map_db", "_probe_meshtasticd_radio",
    ):
        monkeypatch.setattr(
            h, name,
            lambda label=name: ProbeResult(
                label=label, status="ok", headline="ok"
            ),
        )

    with patch("backend.clear_screen", lambda: None):
        h.execute("datapath")

    out = capsys.readouterr().out
    assert "probe error" in out
    assert "[ OK ]" in out
