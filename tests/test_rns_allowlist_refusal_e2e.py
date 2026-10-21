"""#151 closing proof: under ``default_route: allowlist`` an RNS sender OUTSIDE
the rules' ``source_filter`` is refused and one INSIDE is bridged.

KN4YDL could not run this leg (no second identity); we promised it from our
side (2026-10-21). End to end through the real code, not a unit call:
real RNS identities, real LXMF pack/unpack (``signature_validated`` is LXMF's
own verdict), the real ``GatewayConfig`` loaded from a gateway.json file, the
real ``MessageRouter`` built from it, and the real ``_on_lxmf_receive``
ingress. Nothing leaves the process: no RNS instance, no announce — the
refusal happens after delivery, so transport is not what this pins.
"""
import json
import os
import sys
import threading
import types
from pathlib import Path
from queue import Queue

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
RNS = pytest.importorskip("RNS")
pytest.importorskip("LXMF")
from LXMF import APP_NAME, LXMessage  # noqa: E402


def _delivery(identity):
    return RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                           APP_NAME, "delivery")


@pytest.fixture
def world():
    inside, outside, gateway = RNS.Identity(), RNS.Identity(), RNS.Identity()
    dests = {k: _delivery(i) for k, i in
             (("inside", inside), ("outside", outside), ("gateway", gateway))}
    for k, i in (("inside", inside), ("outside", outside), ("gateway", gateway)):
        # what an announce would teach the gateway: the sender's public key
        RNS.Identity.remember(packet_hash=os.urandom(32), destination_hash=dests[k].hash,
                              public_key=i.get_public_key())
    return types.SimpleNamespace(**dests)


def _received(w, sender, text):
    m = LXMessage(w.gateway, getattr(w, sender), text, "t", desired_method=LXMessage.DIRECT)
    m.defer_stamp = True
    m.pack()
    got = LXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated is True, "fixture: LXMF must validate a genuine sender"
    return got


def _router(tmp_path, monkeypatch, inside_hex):
    """The real GatewayConfig, loaded from a real gateway.json, and the real
    MessageRouter built from it — the shape KN4YDL ran (allowlist + a
    source_filter rule per allowed RNS sender)."""
    import gateway.config as gc
    from gateway.message_routing import MessageRouter
    home = tmp_path / "home"
    cfg_dir = home / ".config" / "meshanchor"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "gateway.json").write_text(json.dumps({
        "enabled": True,
        "default_route": "allowlist",
        "routing_rules": [
            {"name": "rns-allowed-sender", "enabled": True, "direction": "rns_to_mesh",
             "source_filter": f"^{inside_hex}$", "priority": 10},
        ],
    }))
    monkeypatch.setattr(gc, "get_real_user_home", lambda: home)
    config = gc.GatewayConfig.load()
    assert config.default_route == "allowlist" and len(config.routing_rules) == 1
    stats, lock = {}, threading.Lock()
    return MessageRouter(config, stats, lock), stats


def _host(monkeypatch, router):
    import gateway.rns_bridge as hub
    from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
    import commands.messaging as messaging
    monkeypatch.setattr(hub, "HAS_RNS_SNIFFER", False)
    monkeypatch.setattr(messaging, "store_incoming", lambda **kw: None)
    h = types.SimpleNamespace(nodes=[])
    h.node_tracker = types.SimpleNamespace(add_node=h.nodes.append)
    h.stats = {"errors": 0}
    h._stats_lock = threading.Lock()
    h._rns_to_mesh_queue = Queue()
    h._router = router
    h._notify_message = lambda m: None
    h._meshtastic_reemit = None
    for name in BridgeRnsEventsMixin.INGRESS_METHODS + ("_on_lxmf_receive",):
        setattr(h, name, getattr(BridgeRnsEventsMixin, name).__get__(h))
    return h


def _bridged(h):
    out = []
    while not h._rns_to_mesh_queue.empty():
        out.append(h._rns_to_mesh_queue.get_nowait())
    return out


def test_an_rns_sender_outside_the_filter_is_refused_and_one_inside_is_bridged(
        world, tmp_path, monkeypatch):
    router, stats = _router(tmp_path, monkeypatch, world.inside.hash.hex())
    assert "ALLOW-LISTED" in router.describe_ingress_policy()
    h = _host(monkeypatch, router)

    h._on_lxmf_receive(_received(world, "outside", "from outside the filter"))
    assert _bridged(h) == [], "an RNS sender outside source_filter was bridged"
    assert stats.get("routing_refused_no_rule") == 1

    h._on_lxmf_receive(_received(world, "inside", "from inside the filter"))
    out = _bridged(h)
    assert [m.source_id for m in out] == [world.inside.hash.hex()]
    assert out[0].content == "from inside the filter"
    assert stats.get("routing_refused_no_rule") == 1, "the allowed sender was counted refused"


def test_the_same_senders_both_bridge_when_the_route_is_open(world, tmp_path, monkeypatch):
    """The control that can fail: with default_route open, the 'outside'
    sender IS bridged — so the refusal above is the allowlist's, not a
    harness that bridges nothing."""
    router, _ = _router(tmp_path, monkeypatch, world.inside.hash.hex())
    router.config.default_route = "bidirectional"
    h = _host(monkeypatch, router)
    h._on_lxmf_receive(_received(world, "outside", "x"))
    h._on_lxmf_receive(_received(world, "inside", "y"))
    assert sorted(m.source_id for m in _bridged(h)) == sorted(
        [world.outside.hash.hex(), world.inside.hash.hex()])
