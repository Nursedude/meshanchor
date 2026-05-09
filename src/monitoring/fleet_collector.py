"""Always-on fleet collector — the canonical writer for fleet_history.

S5a of the fleet observability charter. Replaces the bootstrap-record
side effect baked into ``_serve_fleet_slo`` with a dedicated process
that polls ``/fleet/{slo,activity,federation}`` on a fixed cadence
and persists every cycle to the history DB.

Design choices
--------------
- **Polls the map service, not the daemon directly.** The map already
  composes the canonical view (slo + activity + federation). One source
  of truth per protocol shape.
- **Heartbeat per cycle is the load-bearing artifact.** S5b's watchdog
  reads the most recent heartbeat row to detect silence; the collector
  must record one every cycle even when downstream sources fail.
- **Failure-tolerant.** A 5xx from any /fleet/* endpoint logs, leaves
  the cycle field blank, and continues. The collector itself never
  crashes the loop.
- **No threading.** A single sleep/poll loop. systemd `Restart=always`
  handles the "process died" case in S5b. Local manual runs can
  Ctrl-C cleanly.

Intended deployment (S5b adds the unit):

    systemd unit calls:  python3 -m monitoring.fleet_collector

Manual smoke:

    cd /opt/meshanchor/src && python3 -m monitoring.fleet_collector \
        --base-url http://127.0.0.1:5000 --interval 60
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("fleet.collector")


DEFAULT_BASE_URL = "http://127.0.0.1:5000"
"""Canonical NOC layout. VolcanoAI dev surface uses :5002; the operator
overrides via ``--base-url``."""

DEFAULT_INTERVAL_S = 60.0
"""60s polling cadence — matches the S4 schema's native resolution
and keeps the load light (~1 fetch every 12 polls vs the dashboard's
5s cadence). Faster wastes systemd shell-outs; slower loses sparkline
fidelity."""

DEFAULT_HTTP_TIMEOUT_S = 10.0
"""Per-fetch timeout. Pi-class /fleet/rollup can be 1.6s under load,
so 10s is generous. The collector's overall cycle still finishes in
well under interval even when one endpoint is slow."""

PRUNE_EVERY_N_CYCLES = 100
"""Run prune_history every Nth successful cycle. At 60s × 100 = 100
min, retention pruning effectively runs hourly without a separate
timer. Cheap when nothing's expired."""


# ──────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────


def _fetch_json(url: str, *, timeout: float) -> Tuple[Optional[Any], Optional[str]]:
    """GET JSON from ``url``. Returns ``(parsed_or_None, error_or_None)``.
    Mirrors the soft-fail contract from ``fleet_aggregator._http_get_json``
    so the collector behaves identically to the dashboard's own
    fetches.
    """
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body.decode("utf-8")), None
    except urllib.error.URLError as e:
        return None, f"url error: {getattr(e, 'reason', e)}"
    except (TimeoutError, socket.timeout) as e:
        return None, f"timeout: {e}"
    except json.JSONDecodeError as e:
        return None, f"bad json: {e}"
    except Exception as e:
        return None, f"unexpected: {e}"


# ──────────────────────────────────────────────────────────────────────
# One cycle
# ──────────────────────────────────────────────────────────────────────


def collect_once(
    base_url: str,
    *,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    host: Optional[str] = None,
    db_path=None,
) -> Dict[str, Any]:
    """Single poll cycle — fetches each /fleet/* JSON and writes one
    snapshot. Returns a dict with per-endpoint status + the record
    counts from `record_snapshot`.

    Importing inside the function keeps the collector usable as a
    library (e.g. tests) without paying the import cost when the
    process starts; matters when a future S6 wires the collector
    into an existing TUI handler.
    """
    from monitoring import fleet_history

    host_name = host or socket.gethostname()
    result: Dict[str, Any] = {
        "ts": time.time(),
        "host": host_name,
        "errors": [],
        "fetched": {"slo": False, "activity": False, "federation": False},
        "recorded": {"heartbeat": 0, "boundaries": 0, "events": 0},
    }

    slo, err = _fetch_json(f"{base_url}/fleet/slo", timeout=timeout)
    if err is not None or not isinstance(slo, dict):
        result["errors"].append({"source": "slo", "error": err or "non-dict"})
        slo = {}
    else:
        result["fetched"]["slo"] = True

    activity, err = _fetch_json(f"{base_url}/fleet/activity", timeout=timeout)
    if err is not None or not isinstance(activity, dict):
        result["errors"].append({"source": "activity", "error": err or "non-dict"})
        activity = {}
    else:
        result["fetched"]["activity"] = True

    federation, err = _fetch_json(
        f"{base_url}/fleet/federation", timeout=timeout
    )
    if err is not None or not isinstance(federation, dict):
        result["errors"].append({"source": "federation", "error": err or "non-dict"})
        federation = {}
    else:
        result["fetched"]["federation"] = True

    # Even on partial failure, record what we have. The heartbeat row
    # serves as the "I am alive" signal for the watchdog regardless of
    # whether downstream sources answered.
    try:
        counts = fleet_history.record_snapshot(
            slo, activity, federation,
            host=host_name, ts=result["ts"], db_path=db_path,
        )
        result["recorded"] = counts
    except Exception as e:
        # record_snapshot is supposed to swallow SQLite errors. If
        # something here is unexpected, log and continue rather than
        # crash the loop.
        logger.error("record_snapshot raised: %s", e)
        result["errors"].append({"source": "record", "error": str(e)})

    return result


# ──────────────────────────────────────────────────────────────────────
# Loop
# ──────────────────────────────────────────────────────────────────────


_stop_event_default = None


def run_loop(
    *,
    base_url: str = DEFAULT_BASE_URL,
    interval_s: float = DEFAULT_INTERVAL_S,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    host: Optional[str] = None,
    db_path=None,
    stop_event=None,
    max_cycles: Optional[int] = None,
) -> int:
    """Main loop. Returns the number of cycles completed (useful for
    tests). ``stop_event`` is a ``threading.Event``-shaped object —
    the loop exits cleanly on .set(). ``max_cycles`` caps the loop
    for tests; production runs leave it ``None`` (run forever).

    A stop_event is ALWAYS in play (one is created internally when the
    caller didn't pass one) so the inter-cycle wait can use
    ``stop_event.wait(sleep_for)`` instead of ``time.sleep`` —
    matches the daemon-loop contract from CLAUDE.md (MF010) and lets
    SIGTERM / Ctrl-C unblock immediately.
    """
    import threading
    if stop_event is None:
        stop_event = threading.Event()

    cycles = 0
    last_prune_cycle = 0
    while True:
        if stop_event.is_set():
            break
        if max_cycles is not None and cycles >= max_cycles:
            break

        cycle_start = time.monotonic()
        try:
            result = collect_once(
                base_url, timeout=timeout, host=host, db_path=db_path,
            )
            cycles += 1
            duration = time.monotonic() - cycle_start
            logger.info(
                "cycle %d: fetched=%s recorded=%s errors=%d duration=%.2fs",
                cycles,
                "/".join(k for k, v in result["fetched"].items() if v) or "none",
                f"hb={result['recorded']['heartbeat']} "
                f"bd={result['recorded']['boundaries']} "
                f"ev={result['recorded']['events']}",
                len(result["errors"]),
                duration,
            )
        except Exception as e:
            # Catch-all so the loop never dies. systemd Restart=always
            # would catch a true crash, but a transient hiccup
            # shouldn't take the whole process down either.
            logger.exception("cycle raised: %s", e)

        # Opportunistic retention prune. Cheap when nothing's aged out.
        if cycles - last_prune_cycle >= PRUNE_EVERY_N_CYCLES:
            try:
                from monitoring import fleet_history
                deleted = fleet_history.prune_history(db_path=db_path)
                last_prune_cycle = cycles
                logger.info("prune: %s", deleted)
            except Exception as e:
                logger.warning("prune failed: %s", e)

        sleep_for = max(0.0, interval_s - (time.monotonic() - cycle_start))
        stop_event.wait(sleep_for)

    return cycles


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fleet_collector",
        description="MeshAnchor fleet observability collector — polls /fleet/* "
                    "and persists to the history DB on a fixed cadence.",
    )
    p.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Map service base URL (default: {DEFAULT_BASE_URL})",
    )
    p.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"Cycle interval seconds (default: {DEFAULT_INTERVAL_S})",
    )
    p.add_argument(
        "--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT_S,
        help=f"Per-fetch HTTP timeout seconds (default: {DEFAULT_HTTP_TIMEOUT_S})",
    )
    p.add_argument(
        "--host",
        help="Override hostname recorded in heartbeat (default: socket.gethostname())",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Run a single cycle and exit (useful for cron-style or smoke testing)",
    )
    p.add_argument(
        "--db-path",
        help="Override the fleet_history DB path (default: get_history_db_path())",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    db_path = args.db_path  # None → get_history_db_path() default

    if args.once:
        result = collect_once(
            args.base_url, timeout=args.timeout, host=args.host,
            db_path=db_path,
        )
        logger.info(
            "one-shot: fetched=%s recorded=%s errors=%d",
            result["fetched"], result["recorded"], len(result["errors"]),
        )
        return 0 if not result["errors"] else 1

    # Install SIGTERM/SIGINT handler so systemd / Ctrl-C exit cleanly.
    import threading
    stop = threading.Event()

    def _on_signal(signum, frame):
        logger.info("received signal %d; stopping after current cycle", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    logger.info(
        "starting fleet collector base_url=%s interval=%.1fs",
        args.base_url, args.interval,
    )
    cycles = run_loop(
        base_url=args.base_url,
        interval_s=args.interval,
        timeout=args.timeout,
        host=args.host,
        db_path=db_path,
        stop_event=stop,
    )
    logger.info("collector stopped after %d cycles", cycles)
    return 0


if __name__ == "__main__":
    sys.exit(main())
