"""Tests for the mesh-oracle responder (Phase 1) — the read-only I/O edge.

The responder takes all side-effects as injected callables, so these tests use
fakes: a snapshot fn returning a fixture ``NocSnapshot``, a send fn recording
calls, a log fn capturing records, and a controllable clock. They pin: query
gating, the sender allowlist (fail-closed), per-sender cooldown, audit logging,
consumed-vs-passed-through semantics, send-failure handling, and the ``from_env``
factory (default OFF).
"""
from __future__ import annotations

from oracle import NocSnapshot
from oracle.responder import MeshOracleResponder


def _snap():
    return NocSnapshot(now=1000.0, box="boxA", wd_installed=True, wd_ok=True,
                       wd_signals=[], mini_installed=True, mini_ok=True)


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _make(allowlist=None, answer_all=False, cooldown_s=30.0, send_ok=True,
          clock=None, allowed_channels=None):
    sent, logs = [], []

    def send_fn(text, dest, channel):
        sent.append((text, dest, channel))
        return send_ok

    r = MeshOracleResponder(
        snapshot_fn=_snap, send_fn=send_fn, log_fn=logs.append,
        now_fn=clock or _Clock(), allowlist=allowlist, answer_all=answer_all,
        cooldown_s=cooldown_s, allowed_channels=allowed_channels)
    return r, sent, logs


def test_query_from_allowlisted_node_is_answered():
    r, sent, logs = _make(allowlist={"!a1b2c3d4"})
    reply = r.handle("!a1b2c3d4", "status")
    assert reply and reply.startswith("dude-AI@boxA: ")
    assert len(sent) == 1 and sent[0][1] == "!a1b2c3d4"
    assert logs[-1]["delivered"] is True and logs[-1]["intent"] == "status"


def test_non_query_is_ignored():
    r, sent, _ = _make(answer_all=True)
    assert r.handle("!a1", "good morning everyone") is None
    assert sent == []


def test_non_allowlisted_is_declined_and_logged():
    r, sent, logs = _make(allowlist={"!known"})
    assert r.handle("!stranger", "status") is None
    assert sent == []
    assert logs[-1]["reason"] == "not_allowlisted"


def test_answer_all_bypasses_allowlist():
    r, sent, _ = _make(answer_all=True)
    assert r.handle("!anyone", "status")
    assert len(sent) == 1


def test_allowlist_normalizes_id_forms():
    r, sent, _ = _make(allowlist={"A1B2C3D4"})  # no '!', uppercase
    assert r.handle("!a1b2c3d4", "status")
    assert len(sent) == 1


def test_cooldown_blocks_rapid_repeat():
    clk = _Clock(1000.0)
    r, sent, logs = _make(allowlist={"!n"}, cooldown_s=30.0, clock=clk)
    assert r.handle("!n", "status")           # answered
    clk.t = 1010.0                             # 10s later — within cooldown
    assert r.handle("!n", "status") is None    # blocked
    assert len(sent) == 1
    assert logs[-1]["reason"] == "cooldown"


def test_cooldown_expires():
    clk = _Clock(1000.0)
    r, sent, _ = _make(allowlist={"!n"}, cooldown_s=30.0, clock=clk)
    assert r.handle("!n", "status")
    clk.t = 1031.0
    assert r.handle("!n", "status")
    assert len(sent) == 2


def test_send_failure_still_consumes_and_logs():
    r, sent, logs = _make(allowlist={"!n"}, send_ok=False)
    reply = r.handle("!n", "status")
    assert reply  # consumed (not bridged onward) even though delivery failed
    assert logs[-1]["delivered"] is False


def test_send_exception_is_caught():
    def boom(*a):
        raise RuntimeError("radio down")

    r = MeshOracleResponder(snapshot_fn=_snap, send_fn=boom, log_fn=None,
                            now_fn=_Clock(), answer_all=True)
    assert r.handle("!n", "status")  # must not raise


def test_log_fn_optional():
    r = MeshOracleResponder(snapshot_fn=_snap, send_fn=lambda *a: True,
                            log_fn=None, now_fn=_Clock(), answer_all=True)
    assert r.handle("!n", "status")  # no crash without a log fn


def test_facts_stale_flagged_in_log():
    def stale_snap():
        return NocSnapshot(now=1000.0, box="b", wd_installed=True, wd_stale=True,
                           wd_ok=False, mini_installed=False)

    logs = []
    r = MeshOracleResponder(snapshot_fn=stale_snap, send_fn=lambda *a: True,
                            log_fn=logs.append, now_fn=_Clock(), answer_all=True)
    r.handle("!n", "wd")
    assert logs[-1]["facts_stale"] is True


# --------------------------------------------------------------------------- #
# from_env factory — default OFF, fail-closed allowlist
# --------------------------------------------------------------------------- #
def test_from_env_disabled_by_default():
    assert MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True, env={}) is None


def test_from_env_enabled_with_allowlist():
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True,
        env={"MESHANCHOR_ORACLE_ENABLED": "1",
             "MESHANCHOR_ORACLE_ALLOWLIST": "!a1b2c3d4, !deadbeef",
             "MESHANCHOR_ORACLE_COOLDOWN_S": "5"})
    assert r is not None and r._cooldown_s == 5.0
    assert r.handle("!a1b2c3d4", "status")


def test_from_env_star_answers_all():
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True,
        env={"MESHANCHOR_ORACLE_ENABLED": "yes", "MESHANCHOR_ORACLE_ALLOWLIST": "*"})
    assert r is not None and r._answer_all is True


def test_consume_defaults_true():
    # back-compat: unset MESHANCHOR_ORACLE_CONSUME ⇒ per-mesh-local (consume)
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True,
        env={"MESHANCHOR_ORACLE_ENABLED": "1", "MESHANCHOR_ORACLE_ALLOWLIST": "*"})
    assert r is not None and r.consume is True


def test_consume_false_is_bridge_through():
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True,
        env={"MESHANCHOR_ORACLE_ENABLED": "1", "MESHANCHOR_ORACLE_ALLOWLIST": "*",
             "MESHANCHOR_ORACLE_CONSUME": "0"})
    assert r is not None and r.consume is False
    # handle() still ANSWERS in bridge-through mode (returns the reply); the
    # consume flag is the CALLER's signal to keep bridging, not handle's job.
    reply = r.handle("!a1b2c3d4", "status")
    assert reply and reply.startswith("dude-AI@boxA: ")


def test_from_env_enabled_empty_allowlist_is_fail_closed():
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True,
        env={"MESHANCHOR_ORACLE_ENABLED": "1"})  # enabled, no allowlist
    assert r is not None
    assert r.handle("!anyone", "status") is None  # answers no one


# --------------------------------------------------------------------------- #
# channel allowlist — additive with the per-node allowlist (any node on a
# whitelisted channel is answered without being listed individually)
# --------------------------------------------------------------------------- #
def test_channel_allow_answers_unlisted_node_on_that_channel():
    r, sent, _ = _make(allowed_channels={2})  # no node allowlist
    reply = r.handle("!stranger", "status", channel=2)
    assert reply and len(sent) == 1
    assert sent[0][2] == 2  # reply goes back on the same channel


def test_node_on_non_whitelisted_channel_is_declined():
    r, sent, logs = _make(allowed_channels={2})
    assert r.handle("!stranger", "status", channel=3) is None
    assert sent == []
    assert logs[-1]["reason"] == "not_allowlisted"


def test_empty_channel_set_never_matches_even_channel_zero():
    # Fail-closed: enabled with empty node-list AND empty channel-set answers no
    # one — and an empty set can't be matched by RNS's hard-coded channel=0.
    r, sent, _ = _make()  # answer_all=False, no allowlist, no channels
    assert r.handle("!anyone", "status", channel=0) is None
    assert sent == []


def test_channel_and_node_allow_are_additive():
    r, sent, _ = _make(allowlist={"!known"}, allowed_channels={2})
    assert r.handle("!known", "status", channel=3)      # node match (any chan)
    assert r.handle("!stranger", "status", channel=2)   # channel match
    assert r.handle("!stranger2", "status", channel=3) is None  # neither
    assert len(sent) == 2


def test_from_env_passes_allowed_channels():
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda *a: True,
        env={"MESHANCHOR_ORACLE_ENABLED": "1"},  # empty node allowlist
        allowed_channels={2})
    assert r is not None and r._allowed_channels == {2}
    assert r.handle("!unlisted", "status", channel=2)        # channel grants it
    assert r.handle("!unlisted", "status", channel=9) is None  # other channel


def test_from_env_rns_leg_uses_separate_allowlist_and_transport():
    # The RNS leg shares ENABLED but reads its own allowlist + tags transport.
    sent, logs = [], []
    env = {"MESHANCHOR_ORACLE_ENABLED": "1",
           "MESHANCHOR_ORACLE_ALLOWLIST": "!meshnode",          # mesh leg only
           "MESHANCHOR_ORACLE_RNS_ALLOWLIST": "deadbeefcafe"}    # rns leg only
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda t, d, c: sent.append((t, d)) or True,
        log_fn=logs.append, env=env, transport="rns",
        allowlist_env="MESHANCHOR_ORACLE_RNS_ALLOWLIST")
    assert r is not None
    # answers the RNS source-hash, NOT the mesh-only id
    assert r.handle("deadbeefcafe", "status")
    assert r.handle("!meshnode", "status") is None
    assert logs[-1]["transport"] == "rns"


# --------------------------------------------------------------------------- #
# 2026-09-01 port of MeshForge a3ce083a: decline() for leg-excluded principals
# + channel witness in the audit record
# --------------------------------------------------------------------------- #
def test_decline_records_a_query_without_consulting_the_allow_logic():
    r, sent, logs = _make(answer_all=True)      # answer_all would have answered
    assert r.decline("aa" * 16, "status", reason="peer_gateway_relay") is True
    assert sent == []
    assert logs[-1]["delivered"] is False
    assert logs[-1]["reason"] == "peer_gateway_relay"
    assert logs[-1]["from"] == "aa" * 16 and logs[-1]["intent"] is None


def test_decline_ignores_non_queries_and_writes_nothing():
    r, sent, logs = _make(answer_all=True)
    assert r.decline("aa" * 16, "hello fleet", reason="peer_gateway_relay") is False
    assert logs == [] and sent == []


def test_decline_does_not_touch_the_cooldown_map():
    r, sent, logs = _make(answer_all=True, cooldown_s=30.0)
    r.decline("!abc", "status", reason="peer_gateway_relay")
    assert r.handle("!abc", "status")            # answered, not cooldown-declined
    assert logs[-1]["delivered"] is True


def test_audit_record_carries_the_inbound_channel_token():
    # MeshCore channel hook passes the channel NAME; the DM hook passes None
    r, _, logs = _make(allowed_channels={"meshanchor"})
    assert r.handle("p3", "status", channel="meshanchor")
    assert logs[-1]["channel"] == "meshanchor"
    r2, _, logs2 = _make(allowlist={"!a"})
    r2.handle("!zz", "status", channel=None)
    assert logs2[-1]["reason"] == "not_allowlisted" and "channel" not in logs2[-1]


def test_decline_reasons_vocabulary_is_closed_and_named():
    from oracle.responder import ORACLE_DECLINE_REASONS
    assert set(ORACLE_DECLINE_REASONS) == {"cooldown", "not_allowlisted",
                                           "peer_gateway_relay"}


# --------------------------------------------------------------------------- #
# 2026-09-01 port of two MeshForge responder fixes: wildcard-anywhere and the
# monotonic cooldown clock
# --------------------------------------------------------------------------- #
def test_wildcard_anywhere_in_the_list_means_answer_all():
    # '*,!abc' used to parse as the dead literal node key '!*' — no wildcard,
    # no warning. Now '*' anywhere wins and the allowlist is dropped.
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda t, d, c: True, log_fn=None,
        env={"MESHANCHOR_ORACLE_ENABLED": "1",
             "MESHANCHOR_ORACLE_ALLOWLIST": "*,!abc"})
    assert r is not None and r._answer_all is True and r._allowlist == set()
    assert r.handle("!nobody", "status")           # answered under the wildcard


def test_cooldown_uses_the_monotonic_clock_not_wall_clock():
    wall, mono = _Clock(1000.0), _Clock(50.0)
    sent, logs = [], []
    r = MeshOracleResponder(
        snapshot_fn=_snap, send_fn=lambda t, d, c: sent.append(d) or True,
        log_fn=logs.append, now_fn=wall, monotonic_fn=mono,
        allowlist={"!n"}, cooldown_s=30.0)
    assert r.handle("!n", "status")
    wall.t = 5000.0                                # wall-clock leapt an hour
    assert r.handle("!n", "status") is None        # mono did not move: cooldown
    assert logs[-1]["reason"] == "cooldown"
    mono.t = 90.0                                  # 40 s of monotonic time
    assert r.handle("!n", "status")                # expired on the clock that counts
    assert logs[-1]["ts"] == 5000.0                # records still wear wall-clock


def test_backward_clock_step_does_not_strand_the_sender():
    clk = _Clock(1000.0)
    r, sent, logs = _make(allowlist={"!n"}, cooldown_s=30.0, clock=clk)
    assert r.handle("!n", "status")
    clk.t = 400.0                                  # stepped BACK 10 minutes
    assert r.handle("!n", "status")                # negative delta = expired, not suppressed
    assert logs[-1]["delivered"] is True


def test_from_env_anchors_cooldown_on_time_monotonic():
    import time as _time
    r = MeshOracleResponder.from_env(
        snapshot_fn=_snap, send_fn=lambda t, d, c: True, log_fn=None,
        env={"MESHANCHOR_ORACLE_ENABLED": "1", "MESHANCHOR_ORACLE_ALLOWLIST": "*"})
    assert r is not None and r._mono is _time.monotonic
