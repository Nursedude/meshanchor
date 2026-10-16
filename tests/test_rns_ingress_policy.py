"""RNS→MeshCore ingress allowlist — port of MeshForge's RNS→RF policy
(2026-10-15, operator "go - MA ingress allowlist next").

Measured before building: meshanchor-server's RNS leg is LIVE (583 inbound
LXMF messages from 3 senders in 7 days) with ``default_route``
bidirectional, so any RNS identity that found its announced destination was
bridged onto MeshCore RF with no sender policy. The policy module is shared
BYTE-IDENTICAL with MeshForge (parity-locked); this file pins MeshAnchor's
wiring: the config fields, validation, the gate in ``_on_lxmf_receive``,
and the ledger path in MeshAnchor's own data dir.

MeshAnchor has no ``peer_gateway_destinations``: a MeshForge gateway that
relays here is listed in ``bridge_source_identities`` like any sender.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import types
from pathlib import Path
from queue import Queue

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gateway import rns_ingress_policy as pol  # noqa: E402
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin  # noqa: E402
from gateway.config import GatewayConfig, RNSConfig  # noqa: E402

LISTED = "3dfbdb5d" + "0" * 24
STRANGER = "627fa566" + "f" * 24


class _Tracker:
    def add_node(self, node):
        pass


def _fake_bridge(tmp_path, identities, policy):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.rns = RNSConfig(bridge_source_identities=identities,
                        bridge_source_policy=policy)
    b = types.SimpleNamespace()
    b.config = cfg
    b.stats = {"errors": 0}
    b._stats_lock = threading.Lock()
    b._rns_to_mesh_queue = Queue()
    b.node_tracker = _Tracker()
    b.notified = []
    b.reemitted = []
    b._notify_message = lambda m: b.notified.append(m)
    b._router = types.SimpleNamespace(should_bridge=lambda m: True)
    b._meshtastic_reemit = types.SimpleNamespace(
        on_lxmf_message=lambda src, text: b.reemitted.append(src))
    b._rns_ingress_ledger_path = tmp_path / "ledger.json"
    for name in BridgeRnsEventsMixin.INGRESS_METHODS + ("_on_lxmf_receive",):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


def _lxmf(source_hex, *, validated=True):
    return types.SimpleNamespace(source_hash=bytes.fromhex(source_hex),
                                 content=b"hello", title="", stamp=None,
                                 fields={}, signature_validated=validated,
                                 unverified_reason=None if validated else 0x02)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)
    monkeypatch.setattr("commands.messaging.store_incoming", lambda **kw: None)


# ── config ────────────────────────────────────────────────────────────────

def test_default_is_open_and_observe_is_the_declared_default():
    r = RNSConfig()
    assert r.get_bridge_source_identities() == []
    assert r.bridge_source_policy == "observe"


def test_identities_round_trip_through_gateway_json():
    # save() writes asdict(self.rns); load() reads RNSConfig(**data['rns']).
    from dataclasses import asdict
    r = RNSConfig(bridge_source_identities=[LISTED.upper()], bridge_source_policy="enforce")
    back = RNSConfig(**json.loads(json.dumps(asdict(r))))
    assert back.bridge_source_policy == "enforce"
    assert back.get_bridge_source_identities() == [LISTED]


def test_a_malformed_identity_is_named_not_swallowed():
    cfg = GatewayConfig()
    cfg.rns = RNSConfig(bridge_source_identities=[LISTED, "not-a-hash"])
    _ok, errs = cfg.validate()
    assert any("not-a-hash" in e.message for e in errs)


def test_enforce_with_no_list_is_named_as_open():
    cfg = GatewayConfig()
    cfg.rns = RNSConfig(bridge_source_policy="enforce")
    _ok, errs = cfg.validate()
    assert any("OPEN" in e.message for e in errs)


def test_a_dict_shaped_list_is_named():
    cfg = GatewayConfig()
    cfg.rns = RNSConfig(bridge_source_identities={"a": 1})
    _ok, errs = cfg.validate()
    assert any("must be a list" in e.message for e in errs)


# ── the gate, through the real mixin ─────────────────────────────────────

def test_open_bridges_a_stranger_and_touches_no_ledger(tmp_path):
    b = _fake_bridge(tmp_path, [], "enforce")
    b._on_lxmf_receive(_lxmf(STRANGER))
    assert b._rns_to_mesh_queue.qsize() == 1
    assert not (tmp_path / "ledger.json").exists()
    assert b.rns_ingress_posture()["policy"] == "open"


def test_observe_bridges_but_witnesses_and_ledgers(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    b = _fake_bridge(tmp_path, [LISTED], "observe")
    b._on_lxmf_receive(_lxmf(STRANGER))
    assert b._rns_to_mesh_queue.qsize() == 1           # still bridged
    assert b.stats["rns_ingress_unlisted"] == 1
    assert "rns_to_mesh_refused_unlisted" not in b.stats
    snap = b._rns_ingress_ledger().snapshot()
    assert snap["unlisted_recent"][0]["hash"] == STRANGER and snap["refused_total"] == 0
    line = [r.getMessage() for r in caplog.records if "RNS ingress" in r.getMessage()]
    assert len(line) == 1 and STRANGER in line[0] and "unlisted" in line[0]


def test_enforce_refuses_a_stranger_rf_and_reemit(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    b = _fake_bridge(tmp_path, [LISTED], "enforce")
    b._on_lxmf_receive(_lxmf(STRANGER))
    assert b._rns_to_mesh_queue.qsize() == 0
    assert b.reemitted == []                            # no other way onto RF
    assert b.stats["rns_to_mesh_refused_unlisted"] == 1
    assert len(b.notified) == 1                         # still visible in-app
    assert b._rns_ingress_ledger().snapshot()["refused_total"] == 1
    assert any("REFUSED" in r.getMessage() and STRANGER in r.getMessage()
               for r in caplog.records)


def test_enforce_admits_a_listed_sender_and_notes_its_use(tmp_path):
    b = _fake_bridge(tmp_path, [LISTED], "enforce")
    b._on_lxmf_receive(_lxmf(LISTED))
    assert b._rns_to_mesh_queue.qsize() == 1
    snap = b._rns_ingress_ledger().snapshot()
    assert snap["unlisted_total"] == 0 and set(snap["use"]["last_inbound"]) == {LISTED}


def test_a_claim_of_a_listed_hash_without_a_valid_signature_is_unlisted(tmp_path):
    b = _fake_bridge(tmp_path, [LISTED], "enforce")
    b._on_lxmf_receive(_lxmf(LISTED, validated=False))
    assert b._rns_to_mesh_queue.qsize() == 0
    assert b.stats["rns_to_mesh_refused_unlisted"] == 1


def test_policy_error_admits_as_open_and_says_so(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    b = _fake_bridge(tmp_path, [LISTED], "enforce")
    b.rns_ingress_posture = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    b._on_lxmf_receive(_lxmf(STRANGER))
    assert b._rns_to_mesh_queue.qsize() == 1
    assert any("as if OPEN" in r.getMessage() for r in caplog.records)


def test_declared_gateway_stamps_open_one_does_not(tmp_path):
    b = _fake_bridge(tmp_path, [], "observe")
    b.rns_ingress_stamp()
    assert not (tmp_path / "ledger.json").exists()
    b2 = _fake_bridge(tmp_path, [LISTED], "observe")
    b2.rns_ingress_stamp()
    assert (tmp_path / "ledger.json").exists()


def test_the_ledger_lives_in_meshanchors_data_dir(monkeypatch, tmp_path):
    import gateway.bridge_rns_events_mixin as m
    monkeypatch.setattr(m, "get_real_user_home", lambda: tmp_path)
    assert m.default_ma_ledger_path() == (
        tmp_path / ".local" / "share" / "meshanchor" / pol.LEDGER_FILENAME)


def test_the_policy_module_is_byte_identical_with_meshforge():
    mf = Path("/opt/meshforge/src/gateway/rns_ingress_policy.py")
    if not mf.exists():
        pytest.skip("MeshForge checkout absent — parity_check carries this on the lead box")
    ma = Path(__file__).resolve().parent.parent / "src" / "gateway" / "rns_ingress_policy.py"
    assert ma.read_bytes() == mf.read_bytes()
