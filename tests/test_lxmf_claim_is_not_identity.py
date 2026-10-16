"""A claimed LXMF source hash is not an identity until LXMF validated its
signature (non-author pass on the MF 10-07 arc, 2026-10-15, finding 2).

MeshAnchor has no RNS ingress allowlist (a posture decision, queued); this
pins the narrower floor: a FORGED claim of the broadcast-bridge identity is
never re-emitted onto MeshCore, never registered as a node, and is stored
as an unverified sender. Real RNS identities and real LXMF pack/unpack with
the source field rewritten; skipped where RNS/LXMF are absent.
"""
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

DL = LXMessage.DESTINATION_LENGTH


def _dest(i):
    return RNS.Destination(i, RNS.Destination.OUT, RNS.Destination.SINGLE, APP_NAME, "delivery")


@pytest.fixture
def world():
    A, B, G = RNS.Identity(), RNS.Identity(), RNS.Identity()
    a, b, g = _dest(A), _dest(B), _dest(G)
    for d, i in ((a, A), (b, B), (g, G)):
        RNS.Identity.remember(packet_hash=os.urandom(32), destination_hash=d.hash,
                              public_key=i.get_public_key())
    return types.SimpleNamespace(a=a, b=b, g=g)


def _genuine(w, text):
    m = LXMessage(w.g, w.a, text, "t", desired_method=LXMessage.DIRECT)
    m.defer_stamp = True
    m.pack()
    return m.packed


def _host(monkeypatch, stored):
    import gateway.rns_bridge as hub
    from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
    import commands.messaging as messaging
    monkeypatch.setattr(hub, "HAS_RNS_SNIFFER", False)
    monkeypatch.setattr(messaging, "store_incoming", lambda **kw: stored.append(kw))
    h = types.SimpleNamespace(nodes=[], reemitted=[])
    h.node_tracker = types.SimpleNamespace(add_node=h.nodes.append)
    h.stats = {"errors": 0}
    h._stats_lock = threading.Lock()
    h._rns_to_mesh_queue = Queue()
    h._router = types.SimpleNamespace(should_bridge=lambda m: False)
    h._notify_message = lambda m: None
    h._meshtastic_reemit = types.SimpleNamespace(
        on_lxmf_message=lambda src, text: h.reemitted.append(src) or True)
    for name in BridgeRnsEventsMixin.INGRESS_METHODS + ("_on_lxmf_receive",):
        setattr(h, name, getattr(BridgeRnsEventsMixin, name).__get__(h))
    return h


def test_a_forged_claim_is_not_reemitted_tracked_or_attributed(world, monkeypatch):
    stored = []
    h = _host(monkeypatch, stored)
    raw = _genuine(world, "[meshtastic ch0:evil] hello MeshCore")
    forged = LXMessage.unpack_from_bytes(raw[:DL] + world.b.hash + raw[2 * DL:])
    assert forged.signature_validated is False
    h._on_lxmf_receive(forged)
    assert h.reemitted == [], "a forged claim reached the re-emit allowlist"
    assert h.nodes == []
    assert stored and stored[0]["content"].startswith("[unverified sender] ")


def test_a_genuine_sender_is_reemitted_and_tracked(world, monkeypatch):
    stored = []
    h = _host(monkeypatch, stored)
    h._on_lxmf_receive(LXMessage.unpack_from_bytes(_genuine(world, "hi")))
    assert len(h.reemitted) == 1 and len(h.nodes) == 1
    assert not stored[0]["content"].startswith("[unverified")


def test_a_forgery_that_bridges_travels_marked(world, monkeypatch):
    stored = []
    h = _host(monkeypatch, stored)
    h._router = types.SimpleNamespace(should_bridge=lambda m: True)
    raw = _genuine(world, "hello RF")
    forged = LXMessage.unpack_from_bytes(raw[:DL] + world.b.hash + raw[2 * DL:])
    h._on_lxmf_receive(forged)
    msg = h._rns_to_mesh_queue.get_nowait()
    assert msg.metadata["source_verified"] is False
    assert msg.content.startswith("[unverified] ")


def test_the_rf_copy_of_a_claim_is_marked_once():
    import inspect
    from gateway import rns_bridge
    src = inspect.getsource(rns_bridge)
    # the in-app "[unverified] " mark is replaced by the RF tag's "?"
    assert '_body[len("[unverified] "):]' in src
