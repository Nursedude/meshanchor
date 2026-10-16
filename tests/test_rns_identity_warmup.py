"""After a daemon restart, MA must VERIFY its LXMF sources in seconds, not at
each source's next announce (≤10 min).

Live 2026-10-16: the MA daemon restarted 01:13:33; moc's Meshtastic broadcast
bridge (32ee84c3…, a ``meshtastic_reemit.source_identities`` member) stayed
UNVERIFIED until its announce at 01:17:26. The re-emit only takes verified
sources, so every bot reply in that window (7 copies, 01:14:36→01:17:25) never
reached the MeshCore "meshanchor" channel. Earlier the same night the window
also re-radiated those copies onto moc's LongFast (fixed in ee404ae9).

MeshForge closed this on its side 2026-10-08 (``rns_ingress_warmup`` +
``_rns_ingress_path_nudge``), but its member set is the RNS→RF allowlist and
it returns early when the policy is OPEN — MA's posture. MA's members are the
re-emit sources ∪ the listed identities, ALWAYS: re-emit needs a verified
source whatever the ingress policy says (non-author review I4).
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from tests.test_rns_bridge import bridge  # noqa: F401 — shared fixture

MOC_BCAST = "32ee84c3e0d18def4dee1ab39c02db2a"
MOC3_BCAST = "aaa2365f799cc28aa7697df943096074"
LISTED = "3dfbdb5d0f0e0d0c0b0a090807060504"


def _with_members(bridge, reemit=(MOC_BCAST, MOC3_BCAST), listed=()):
    reemit_bridge = MagicMock()
    reemit_bridge._allowed_identities = {h.lower() for h in reemit}
    bridge._meshtastic_reemit = reemit_bridge
    bridge.rns_ingress_posture = lambda: {
        "policy": "open" if not listed else "observe",
        "listed": len(listed), "identities": list(listed)}
    return bridge


class TestMembers:

    def test_reemit_sources_are_members_even_when_ingress_is_open(self, bridge):
        _with_members(bridge)
        assert set(bridge._rns_identity_warmup_members()) == {MOC_BCAST, MOC3_BCAST}

    def test_listed_identities_join_the_reemit_sources(self, bridge):
        _with_members(bridge, listed=(LISTED,))
        assert set(bridge._rns_identity_warmup_members()) == {
            MOC_BCAST, MOC3_BCAST, LISTED}

    def test_no_reemit_and_nothing_listed_is_empty(self, bridge):
        bridge._meshtastic_reemit = None
        bridge.rns_ingress_posture = lambda: {
            "policy": "open", "listed": 0, "identities": []}
        assert bridge._rns_identity_warmup_members() == []


class TestWarmup:

    def _run(self, bridge, known=(), request_result="sent", after=()):
        recalled = {"phase": 0}

        def recall(h: bytes):
            hx = h.hex()
            pool = known if recalled["phase"] == 0 else tuple(known) + tuple(after)
            return object() if hx in pool else None

        requested = []

        def request(h: bytes):
            requested.append(h.hex())
            if isinstance(request_result, BaseException):
                raise request_result
            return request_result

        def settle(_s):
            recalled["phase"] = 1
        bridge._rns_recall_identity = recall
        bridge._rns_request_path = request
        with patch.object(bridge._stop_event, "wait", side_effect=settle):
            out = bridge.rns_identity_warmup(settle_s=5.0)
        return out, requested

    def test_requests_only_what_it_cannot_recall_then_RE_MEASURES(self, bridge, caplog):
        _with_members(bridge)
        with caplog.at_level("INFO"):
            out, requested = self._run(bridge, known=(MOC3_BCAST,),
                                       after=(MOC_BCAST,))
        assert requested == [MOC_BCAST]
        assert out == {"members": 2, "known": 1, "requested": 1,
                       "blocked": 0, "failed": 0, "after": 2}
        assert "1/2 recallable" in caplog.text and "2/2" in caplog.text, caplog.text

    def test_a_request_that_does_not_cure_is_named(self, bridge, caplog):
        """'request sent' is not the END: rnsd ignores a request whose announce
        cache entry is gone (MF review 2026-10-08) — say who is STILL unknown."""
        _with_members(bridge)
        with caplog.at_level("INFO"):
            out, _ = self._run(bridge)
        assert out["after"] == 0
        assert "still unknown" in caplog.text and MOC_BCAST[:8] in caplog.text

    def test_tx_guard_block_is_counted_never_raised(self, bridge):
        _with_members(bridge)
        out, _ = self._run(bridge, request_result="blocked")
        assert out["blocked"] == 2 and out["requested"] == 0

    def test_a_failing_request_is_counted_never_raised(self, bridge):
        _with_members(bridge)
        out, _ = self._run(bridge, request_result=OSError("rnsd gone"))
        assert out["failed"] == 2

    def test_nothing_to_do_is_quiet_and_cheap(self, bridge):
        bridge._meshtastic_reemit = None
        bridge.rns_ingress_posture = lambda: {
            "policy": "open", "listed": 0, "identities": []}
        assert bridge.rns_identity_warmup(settle_s=5.0) == {}

    def test_the_rns_loop_starts_it_beside_the_reemit_bridge(self):
        """Wiring pin (BELIEVED until the live journal shows the line after a
        restart): the warm-up thread starts where the re-emit bridge does."""
        import inspect
        from gateway import rns_bridge
        src = inspect.getsource(rns_bridge)
        i = src.find("self._maybe_start_meshtastic_reemit()")
        assert i != -1
        assert "rns_identity_warmup" in src[i:i + 600], src[i:i + 600]


class TestPathNudge:

    def _lxmf(self, source, reason=0x01, validated=False):
        m = MagicMock()
        m.source_hash = bytes.fromhex(source)
        m.signature_validated = validated
        m.unverified_reason = reason
        m.content = "[meshtastic ch2:!0daee001] SunRise: Fri 16 06:14AM"
        m.title = ""
        m.stamp = time.time()
        return m

    def _receive(self, bridge, msg, clock=0.0):
        from gateway import bridge_rns_events_mixin as mx
        spawned = []
        with patch.object(mx, "_spawn", lambda fn, *a: spawned.append(a[0].hex())), \
             patch.object(mx, "_monotonic", lambda: clock):
            bridge._on_lxmf_receive(msg)
        return spawned

    def test_an_unverified_member_gets_one_path_request(self, bridge):
        _with_members(bridge)
        assert self._receive(bridge, self._lxmf(MOC_BCAST)) == [MOC_BCAST]

    def test_the_nudge_is_rate_limited_per_identity(self, bridge):
        _with_members(bridge)
        self._receive(bridge, self._lxmf(MOC_BCAST), clock=100.0)
        assert self._receive(bridge, self._lxmf(MOC_BCAST), clock=130.0) == []
        assert self._receive(bridge, self._lxmf(MOC_BCAST), clock=170.0) == [MOC_BCAST]

    def test_a_stranger_is_never_nudged(self, bridge):
        """A forger must not make us storm path requests for arbitrary hashes."""
        _with_members(bridge)
        stranger = "deadbeefdeadbeefdeadbeefdeadbeef"
        assert self._receive(bridge, self._lxmf(stranger)) == []

    def test_a_bad_signature_is_not_nudged(self, bridge):
        """Only 'source never announced' is cured by a path request; an
        invalid signature is a forgery, not a stale cache."""
        _with_members(bridge)
        assert self._receive(bridge, self._lxmf(MOC_BCAST, reason=0x02)) == []

    def test_a_verified_member_is_not_nudged(self, bridge):
        _with_members(bridge)
        assert self._receive(
            bridge, self._lxmf(MOC_BCAST, validated=True, reason=None)) == []
