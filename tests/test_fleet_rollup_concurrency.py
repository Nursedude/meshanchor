"""Peer fan-out runs concurrently, without breaking the row contract.

`collect_fleet_rollup` fetched every peer SEQUENTIALLY, so rollup latency was
`len(peers) x PEER_HTTP_TIMEOUT_S` worst case — 24s with 5 peers, against a
client that (before b395e1b2) allowed 5s. Measured on meshanchor-server
2026-07-25: 2.88s-5.39s, 1 fetch in 4 over budget. fleet_rollup's own module
docstring called concurrent fetching "a Session 4 perf concern".

Concurrency must not cost the guarantees the serial loop provided:
  - every configured peer still produces exactly one row (never a dropped row)
  - rows stay in CONFIG order, not completion order (a dashboard grid that
    reshuffles on every poll is unreadable, and row identity is positional
    in the truth schema)
  - a slow/failed peer still degrades soft into its own row
  - gateway-kind peers still skip the HTTP hop entirely
"""

import threading
import time
from unittest.mock import patch

import monitoring.fleet_rollup as fr
from monitoring.fleet_config import FederationConfig, FleetConfig, PeerConfig


def _make_config(peers, scrape=False):
    return FleetConfig(
        peers=peers,
        federation=FederationConfig(scrape_rns_announces=scrape),
        source_path="/test/fleet.json",
    )


def _peers(n, kind="noc"):
    return [PeerConfig(name=f"p{i}", host=f"p{i}.example", port=5001, kind=kind)
            for i in range(n)]


class TestPeerFanOutIsConcurrent:

    def test_added_peers_cost_almost_nothing(self):
        """Differential: the marginal cost of 7 extra peers must be ~0.

        Measured as a DELTA on purpose. The patched `_http_get_json` also
        serves the local snapshot's 3 sequential daemon fetches, so absolute
        wall time carries a fixed ~3*delay of self-cost that has nothing to
        do with peer concurrency. Subtracting a 1-peer baseline isolates the
        fan-out: serial would add 7*delay, concurrent adds ~0.
        """
        delay = 0.3

        def slow_http(url, timeout=3.0):
            time.sleep(delay)
            return {"ok": True}, None

        def run(n):
            cfg = _make_config(_peers(n))
            with patch("monitoring.fleet_aggregator._http_get_json",
                       side_effect=slow_http):
                t0 = time.monotonic()
                rollup = fr.collect_fleet_rollup(cfg)
                return time.monotonic() - t0, rollup

        baseline, _ = run(1)
        elapsed, rollup = run(8)
        marginal = elapsed - baseline

        assert len(rollup.peers) == 8
        serial_marginal = 7 * delay
        assert marginal < serial_marginal / 2, (
            f"7 extra peers added {marginal:.2f}s (baseline {baseline:.2f}s, "
            f"total {elapsed:.2f}s); serial would add {serial_marginal:.2f}s "
            f"— fan-out is not concurrent"
        )

    def test_fetches_actually_overlap_in_time(self):
        """Direct evidence: more than one fetch in flight simultaneously."""
        lock = threading.Lock()
        state = {"in_flight": 0, "peak": 0}

        def tracking_http(url, timeout=3.0):
            with lock:
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
            time.sleep(0.15)
            with lock:
                state["in_flight"] -= 1
            return {"ok": True}, None

        cfg = _make_config(_peers(6))
        with patch("monitoring.fleet_aggregator._http_get_json",
                   side_effect=tracking_http):
            fr.collect_fleet_rollup(cfg)

        assert state["peak"] > 1, "fetches never overlapped — still serial"


class TestRowContractSurvivesConcurrency:

    def test_rows_stay_in_config_order_not_completion_order(self):
        """Peer 0 is slowest, peer 4 fastest — order must still be p0..p4."""
        def staggered_http(url, timeout=3.0):
            idx = int(url.split("//p")[1].split(".")[0])
            time.sleep(0.05 * (5 - idx))          # p0 slowest
            return {"idx": idx}, None

        cfg = _make_config(_peers(5))
        with patch("monitoring.fleet_aggregator._http_get_json",
                   side_effect=staggered_http):
            rollup = fr.collect_fleet_rollup(cfg)

        assert [p.name for p in rollup.peers] == ["p0", "p1", "p2", "p3", "p4"]
        assert [p.snapshot["idx"] for p in rollup.peers] == [0, 1, 2, 3, 4]

    def test_every_peer_gets_exactly_one_row(self):
        cfg = _make_config(_peers(9))
        with patch("monitoring.fleet_aggregator._http_get_json",
                   side_effect=lambda url, timeout=3.0: ({"ok": True}, None)):
            rollup = fr.collect_fleet_rollup(cfg)
        assert len(rollup.peers) == 9
        assert len({p.name for p in rollup.peers}) == 9

    def test_one_failing_peer_does_not_poison_the_others(self):
        def mixed_http(url, timeout=3.0):
            if "p2." in url:
                return None, "timeout"
            return {"ok": True}, None

        cfg = _make_config(_peers(5))
        with patch("monitoring.fleet_aggregator._http_get_json", side_effect=mixed_http):
            rollup = fr.collect_fleet_rollup(cfg)

        by_name = {p.name: p for p in rollup.peers}
        assert by_name["p2"].error == "timeout"
        assert by_name["p2"].snapshot is None
        for name in ("p0", "p1", "p3", "p4"):
            assert by_name[name].error is None
            assert by_name[name].snapshot == {"ok": True}

    def test_raising_peer_becomes_an_error_row_not_a_crash(self):
        """A worker exception must land in that row, not kill the rollup."""
        def exploding_http(url, timeout=3.0):
            if "p1." in url:
                raise RuntimeError("boom")
            return {"ok": True}, None

        cfg = _make_config(_peers(3))
        with patch("monitoring.fleet_aggregator._http_get_json",
                   side_effect=exploding_http):
            rollup = fr.collect_fleet_rollup(cfg)

        by_name = {p.name: p for p in rollup.peers}
        assert len(rollup.peers) == 3
        assert by_name["p1"].snapshot is None
        assert by_name["p1"].error and "boom" in by_name["p1"].error

    def test_gateway_peers_still_skip_the_http_hop(self):
        calls = []

        def counting_http(url, timeout=3.0):
            calls.append(url)
            return {"ok": True}, None

        cfg = _make_config([
            PeerConfig(name="noc1", host="noc1.example", port=5001, kind="noc"),
            PeerConfig(name="gw", host="gw.example", port=5001, kind="gateway"),
        ])
        with patch("monitoring.fleet_aggregator._http_get_json",
                   side_effect=counting_http):
            rollup = fr.collect_fleet_rollup(cfg)

        assert len(rollup.peers) == 2
        assert not any("gw.example" in u for u in calls)
        gw = [p for p in rollup.peers if p.name == "gw"][0]
        assert gw.error is None and gw.snapshot is None


class TestRollupWorstCaseIsExported:
    """The client budget must derive from the server's model, not guess it.

    This is the constant that drifted three times in fleet_monitor.py, so the
    server now owns the number and the TUI asks for it.
    """

    def test_worst_case_reflects_concurrency(self):
        seq = fr.PEER_HTTP_TIMEOUT_S * 20
        assert fr.rollup_worst_case_s(20) < seq, (
            "worst case still scales linearly — concurrency not accounted for")

    def test_worst_case_covers_one_full_wave(self):
        from monitoring.fleet_aggregator import DEFAULT_HTTP_TIMEOUT_S
        floor = (3 * DEFAULT_HTTP_TIMEOUT_S
                 + fr.PEER_HTTP_TIMEOUT_S      # at least one peer wave
                 + fr.PEER_HTTP_TIMEOUT_S)     # federation
        assert fr.rollup_worst_case_s(1) >= floor

    def test_worst_case_grows_past_the_worker_pool(self):
        """More peers than workers means more waves, so a bigger bound."""
        one_wave = fr.rollup_worst_case_s(fr.MAX_PEER_WORKERS)
        two_waves = fr.rollup_worst_case_s(fr.MAX_PEER_WORKERS * 2)
        assert two_waves > one_wave

    def test_zero_peers_is_not_negative_or_zero(self):
        assert fr.rollup_worst_case_s(0) > 0

    def test_tui_budget_uses_the_exported_bound(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
        from launcher_tui.handlers.fleet_monitor import _endpoint_timeout
        assert _endpoint_timeout("/fleet/rollup", peer_count=6) >= fr.rollup_worst_case_s(6)
