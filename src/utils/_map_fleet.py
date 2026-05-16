"""Fleet endpoints for MapRequestHandler.

Serves `/fleet/health`, `/fleet/slo`, `/fleet/activity` — the always-on
web surface for the engineering-grade NOC dashboard. The aggregator
itself lives in `monitoring.fleet_aggregator`; this mixin is just the
HTTP glue that turns a request into a snapshot + JSON response.

Extracted from map_http_handler.py for file size compliance (CLAUDE.md #6).

Expects the following attributes/methods on the host class:
- self.collector              : MapDataCollector instance (or None)
- self.path                   : current request path
- self._serve_json(obj, ...)  : method
- self._send_cors_header()    : method
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class FleetEndpointsMixin:
    """Adds `/fleet/*` JSON endpoints to MapRequestHandler.

    All three endpoints are LAN-readable like the rest of the map's GET
    surface. The data exposed is observability-grade — service states,
    boundary timings, recent chat (no secrets), and the radio's
    advertised name/preset. No mutating operations live here.
    """

    def _serve_fleet_dashboard(self) -> None:
        """Serve the always-on `/fleet` HTML dashboard.

        Single-file vanilla HTML+CSS+JS that polls `/fleet/{slo,
        activity,rollup,federation}` every 5s. Same dark theme as
        `node_map.html`. Lives under `web/fleet.html`."""
        from pathlib import Path
        if self.web_dir:
            file_path = Path(self.web_dir) / "fleet.html"
        else:
            file_path = Path(__file__).parent.parent.parent / "web" / "fleet.html"
        try:
            file_path = file_path.resolve()
        except Exception:
            self.send_error(400, "Invalid path")
            return
        if not file_path.exists():
            self.send_error(404, "fleet.html not found")
            return
        with open(file_path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def _serve_fleet_health(self) -> None:
        """Full snapshot — services + boundaries + daemon health + radio
        + recent chat + collector stats. This is the diagnostic deep
        dive; the daemon `/health` fetch in here is currently a ~2s
        serial-systemctl walk, so the always-on dashboard polls
        `/fleet/slo` and `/fleet/activity` instead."""
        snap = self._collect_fleet_snapshot(include_daemon_health=True)
        self._serve_json(snap.to_dict())

    def _serve_fleet_slo(self) -> None:
        """Top-line SLO rollup for the dashboard's upper panel.

        Skips the slow daemon `/health` fetch — `slo_view` derives
        `overall_status` from the local `check_service` rollup when
        `daemon_health` is absent.

        Read-only: the canonical writer is the S5 collector
        (`monitoring.fleet_collector`, shipped in PR #108 / installed
        as `meshanchor-fleet-collector.service` per S5b). Earlier
        revisions of this method also wrote a heartbeat row
        opportunistically as a bootstrap; that path was removed
        2026-05-09 once the collector had soaked cleanly so dashboard
        polls and the collector don't compete for the heartbeat
        sequence.
        """
        from monitoring.fleet_aggregator import slo_view
        snap = self._collect_fleet_snapshot(include_daemon_health=False)
        self._serve_json(slo_view(snap))

    def _serve_fleet_activity(self) -> None:
        """Live-feed surface for the dashboard's lower panel."""
        from monitoring.fleet_aggregator import activity_view
        snap = self._collect_fleet_snapshot(include_daemon_health=False)
        self._serve_json(activity_view(snap))

    def _serve_fleet_rollup(self) -> None:
        """Multi-host rollup — self + every peer in `~/.config/meshanchor/
        fleet.json` + RNS federation peers from the directory cache.
        The dashboard polls this for the cross-host SLO grid."""
        from monitoring.fleet_config import load_fleet_config
        from monitoring.fleet_rollup import collect_fleet_rollup
        try:
            config = load_fleet_config()
            rollup = collect_fleet_rollup(config, collector=self.collector)
        except Exception as e:
            logger.error("fleet rollup failed: %s", e)
            self._serve_json({"error": str(e), "peers": [], "federation_peers": []},
                             status=500)
            return
        self._serve_json(rollup.to_dict())

    def _serve_fleet_lab_rollup(self) -> None:
        """Federation round-trip rollup — fetches MeshForge's /lab/rollup
        from the tester box and exposes the markdown for the NOC panel.

        The data path is:

          tracer (every 10 min on tester boxes) -> journalctl lines
            -> meshforge-lab-rollup.service (every 10 min, offset)
            -> ~/.local/state/meshforge/lab-traffic-rollup-leaderboard.md
            -> MF /lab/rollup HTTP endpoint
            -> THIS endpoint
            -> fleet.html "Federation Round-Trip" panel

        Configurable via env var ``MESHANCHOR_LAB_ROLLUP_URL`` (default:
        ``http://VolcanoAI:5000/lab/rollup``). Set to empty string to
        disable the panel cleanly without 500-ing.

        Response shape (JSON):
          {
            "enabled": true,
            "source_url": "http://VolcanoAI:5000/lab/rollup",
            "fetched_at": <unix ts>,
            "rollup_md": "## Lab traffic rollup\\n...",
            "synth_md": "..." or null,
            "error": null or "<reason>"
          }

        The fix-hint pattern (from the MF side) is preserved end-to-end:
        when no tester box has the rollup timer installed, the upstream
        404 emits markdown explaining how to install it. The dashboard
        renders that hint verbatim — the operator sees actionable
        instructions, not a silent empty panel.
        """
        import os
        import time as _time
        import urllib.error
        import urllib.request

        source_url = os.environ.get(
            "MESHANCHOR_LAB_ROLLUP_URL",
            "http://VolcanoAI:5000/lab/rollup",
        ).strip()

        if not source_url:
            self._serve_json({
                "enabled": False,
                "source_url": "",
                "fetched_at": _time.time(),
                "rollup_md": None,
                "synth_md": None,
                "error": "MESHANCHOR_LAB_ROLLUP_URL is empty (panel disabled)",
            })
            return

        result: Dict[str, Any] = {
            "enabled": True,
            "source_url": source_url,
            "fetched_at": _time.time(),
            "rollup_md": None,
            "synth_md": None,
            "error": None,
        }

        # Fetch the primary traffic rollup. Treat 404 as a soft case: the
        # MF side returns a markdown fix-hint there, and the dashboard
        # surfaces it. Treat connect errors as the "tester box is offline"
        # case — error string goes into the response so the panel can
        # show "could not reach tester box at <url>" instead of empty.
        for label, url_key in (("rollup_md", source_url),
                               ("synth_md", source_url.replace("/lab/rollup",
                                                               "/lab/synth-rollup"))):
            try:
                req = urllib.request.Request(
                    url_key, headers={"Accept": "text/markdown, text/plain"},
                )
                with urllib.request.urlopen(req, timeout=4.0) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                result[label] = body
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Read the body — MF returns a markdown fix-hint here.
                    try:
                        result[label] = e.read().decode("utf-8", errors="replace")
                    except Exception:
                        result[label] = (
                            f"# {label} unavailable\n\n"
                            f"`{url_key}` returned 404 with no body.\n"
                        )
                else:
                    msg = f"http {e.code}: {e.reason}"
                    if label == "rollup_md":
                        # Primary failure — surface in `error` so dashboard
                        # paints the panel red.
                        result["error"] = f"{url_key}: {msg}"
                    logger.warning("lab rollup fetch failed: %s: %s", url_key, msg)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if label == "rollup_md":
                    result["error"] = f"{url_key}: {msg}"
                logger.warning("lab rollup fetch failed: %s: %s", url_key, msg)

        self._serve_json(result)

    def _serve_fleet_blackouts(self) -> None:
        """Active + recent blackout intervals.

        Active blackouts power the dashboard's red banner; the recent
        list (default last 24h, includes already-ended rows) shows
        the operator the platform's reliability over the day.

        Query params (optional):
          since=<unix>   default: now - 24h
          until=<unix>   default: now
          active_only=1  return just the active blackouts (cheaper)
        """
        from urllib.parse import urlparse, parse_qs
        import time as _time

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        from monitoring import fleet_history
        try:
            if params.get("active_only", ["0"])[0] == "1":
                rows = fleet_history.query_active_blackouts()
                self._serve_json({"active": rows, "history": []})
                return

            try:
                since = float(params.get("since", [str(_time.time() - 86400)])[0])
                until = float(params.get("until", [str(_time.time())])[0])
            except (ValueError, TypeError):
                self._serve_json({"error": "since/until must be numbers"},
                                 status=400)
                return

            history = fleet_history.query_blackout_history(
                since=since, until=until, include_active=True,
            )
            active = [r for r in history if r.get("ts_ended") is None]
            self._serve_json({
                "active": active,
                "history": history,
                "since": since,
                "until": until,
            })
        except Exception as e:
            logger.error("fleet_blackouts query failed: %s", e)
            self._serve_json({"error": str(e), "active": [], "history": []},
                             status=500)

    def _serve_fleet_history(self) -> None:
        """Historical timeseries for the dashboard's sparklines.

        Query params:
          metric=boundary&label=<label>&since=<unix>&until=<unix>&resolution=<s>
          metric=heartbeat&since=<unix>&until=<unix>&resolution=<s>
          metric=service_events&since=<unix>&until=<unix>
          metric=labels                          (returns distinct labels)

        ``since`` defaults to (now - 1h) when omitted. ``until`` defaults
        to now. ``resolution`` defaults to native (60s) — pass larger
        values for compressed views (5min/1h) on long windows.

        All queries are read-only; the canonical writer is
        ``monitoring.fleet_collector`` (shipped S5a, deployed S5b as
        ``meshanchor-fleet-collector.service``).
        """
        from urllib.parse import urlparse, parse_qs
        import time as _time

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        metric = params.get("metric", ["heartbeat"])[0]
        try:
            since = float(params.get("since", [str(_time.time() - 3600)])[0])
            until = float(params.get("until", [str(_time.time())])[0])
            resolution = int(params.get("resolution", ["60"])[0])
        except (ValueError, TypeError):
            self._serve_json({"error": "since/until/resolution must be numbers"},
                             status=400)
            return

        # Bound the window size to keep the response small. 24h × 60s =
        # 1440 points per series — plenty for a sparkline. Caller can
        # pass `resolution` to widen the window proportionally.
        max_points = 5000
        if (until - since) / max(resolution, 1) > max_points:
            self._serve_json({
                "error": "window too large for resolution",
                "max_points": max_points,
            }, status=400)
            return

        from monitoring import fleet_history
        try:
            if metric == "labels":
                rows = fleet_history.list_boundary_labels(since=since)
                self._serve_json({"labels": rows})
                return
            if metric == "boundary":
                label = params.get("label", [""])[0]
                if not label:
                    self._serve_json({"error": "label required for metric=boundary"},
                                     status=400)
                    return
                rows = fleet_history.query_boundary_history(
                    label=label, since=since, until=until,
                    resolution_s=resolution,
                )
                self._serve_json({
                    "metric": "boundary", "label": label,
                    "since": since, "until": until,
                    "resolution_s": resolution, "points": rows,
                })
                return
            if metric == "heartbeat":
                rows = fleet_history.query_heartbeat_history(
                    since=since, until=until, resolution_s=resolution,
                )
                self._serve_json({
                    "metric": "heartbeat",
                    "since": since, "until": until,
                    "resolution_s": resolution, "points": rows,
                })
                return
            if metric == "service_events":
                rows = fleet_history.query_service_events(
                    since=since, until=until,
                )
                self._serve_json({
                    "metric": "service_events",
                    "since": since, "until": until,
                    "events": rows,
                })
                return
            self._serve_json({"error": f"unknown metric: {metric}"}, status=400)
        except Exception as e:
            logger.error("fleet_history query failed: %s", e)
            self._serve_json({"error": str(e), "points": []}, status=500)

    def _serve_fleet_metrics(self) -> None:
        """Prometheus exposition format at the bare ``/metrics`` path.

        Localhost-only: scrapers run on-box or behind an authenticated
        proxy. Exposing all node positions, service states, and MQTT
        broker addresses to the LAN gives a passive attacker a full
        asset map.

        Body is the concatenation of two surfaces:
          1. ``PrometheusExporter().export()`` — hand-rolled exposition
             covering node counts, service health, MQTT/TCP/RNS stats,
             env sensors, queue depth (10 collectors).
          2. ``map_metrics.render()`` — HTTP-side metrics from the
             reverse port (PR after #113): request counters labeled
             by method/endpoint/status_class, latency histogram
             labeled by endpoint. No-op when ``prometheus_client``
             isn't installed.

        Both are valid Prom exposition; concatenating with a newline
        separator produces a single valid body. Gzip is honored via
        ``_maybe_gzip`` because the exposition compresses ~5×.
        """
        if not self._is_localhost():
            self.send_error(403, "Metrics only available from localhost")
            return
        try:
            from utils.prometheus_exporter import PrometheusExporter
            from utils import map_metrics
            primary = PrometheusExporter().export().encode("utf-8")
            secondary, _ = map_metrics.render()
            body = primary + b"\n" + secondary if secondary else primary
        except Exception as e:
            logger.error("metrics export failed: %s", e)
            self.send_error(500, "metrics export failed")
            return
        payload, encoding = self._maybe_gzip(body)
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; version=0.0.4; charset=utf-8",
        )
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_fleet_federation(self) -> None:
        """Federation peers only — useful when the dashboard wants to
        refresh the RNS-side panel without paying for the full peer
        rollup.

        Two data sources, tried in order (issue #102):

        1. The daemon's `/fleet/federation` endpoint — the canonical
           source for RNS announces, which live in the daemon's
           `rns_services._service_registry` and never touch the map's
           directory cache.
        2. The map's directory cache filtered to `network=="rns"` —
           a fallback for hosts that DO end up persisting RNS
           announces into the directory (e.g. via a future collector).

        Each peer carries a `source` field so the dashboard can show
        provenance ("from daemon" vs "from directory")."""
        from monitoring.fleet_config import load_fleet_config
        from monitoring.fleet_rollup import _collect_federation_peers
        from monitoring.fleet_aggregator import (
            _http_get_json, DEFAULT_DAEMON_URL, DEFAULT_HTTP_TIMEOUT_S,
        )
        from dataclasses import asdict

        config = load_fleet_config()
        if not config.federation.scrape_rns_announces:
            self._serve_json({"enabled": False, "peers": [], "sources": []})
            return

        peers_out = []
        sources = []
        errors = []
        fresh_window = config.federation.fresh_window_s

        # 1. Daemon-side registry (canonical).
        body, err = _http_get_json(
            f"{DEFAULT_DAEMON_URL}/fleet/federation",
            timeout=DEFAULT_HTTP_TIMEOUT_S,
        )
        if err is None and isinstance(body, dict):
            sources.append("daemon")
            for peer in body.get("peers") or []:
                if not isinstance(peer, dict):
                    continue
                age = peer.get("last_seen_age_s")
                if fresh_window > 0 and (age is None or age > fresh_window):
                    continue
                peers_out.append({
                    "node_id": peer.get("hash_hex", ""),
                    "name": peer.get("name") or peer.get("hash_hex", ""),
                    "network": "rns",
                    "service_type": peer.get("service_type", ""),
                    "aspect": peer.get("aspect", ""),
                    "source_origin": "rns_announce",
                    "source": "daemon",
                    "last_seen": peer.get("last_seen"),
                    "last_seen_age_s": age,
                    "first_seen_age_s": peer.get("first_seen_age_s"),
                })
        elif err is not None:
            errors.append({"source": "daemon", "error": err})

        # 2. Directory cache fallback. Even when the daemon answered,
        # we union with directory entries — different hosts may be
        # configured differently and the panel should show whatever
        # data is available.
        directory_peers = _collect_federation_peers(
            self.collector,
            fresh_window_s=fresh_window,
        )
        if directory_peers:
            sources.append("directory")
            seen_ids = {p["node_id"] for p in peers_out}
            for fp in directory_peers:
                if fp.node_id in seen_ids:
                    # Daemon registry already covered it — skip.
                    continue
                row = asdict(fp)
                row["source"] = "directory"
                peers_out.append(row)

        # Newest first regardless of source.
        peers_out.sort(
            key=lambda p: p.get("last_seen_age_s")
            if p.get("last_seen_age_s") is not None else 1e18
        )

        self._serve_json({
            "enabled": True,
            "fresh_window_s": fresh_window,
            "peers": peers_out,
            "sources": sources,
            "errors": errors,
        })

    def _collect_fleet_snapshot(self, *, include_daemon_health: bool):
        """Single collection point so all three endpoints share the same
        defaults (daemon URL, timeout). Each request collects fresh —
        TTL caching can land in Session 4 alongside retention."""
        from monitoring.fleet_aggregator import collect_local_snapshot
        try:
            return collect_local_snapshot(
                collector=self.collector,
                include_daemon_health=include_daemon_health,
            )
        except Exception as e:
            # The aggregator is supposed to never raise; a hit here means
            # something deeper exploded. Return a minimal-but-valid
            # snapshot so the dashboard never breaks the demo.
            logger.error("fleet snapshot collection failed: %s", e)
            from monitoring.fleet_aggregator import FleetSnapshot
            import socket as _socket
            import time as _time
            snap = FleetSnapshot(
                generated_at=_time.time(),
                host=_socket.gethostname(),
                uptime_s=0.0,
            )
            snap.errors.append({"source": "aggregator", "error": str(e)})
            return snap

    # ─────────────────────────────────────────────────────────────────
    # /fleet/logs — T1 of fleet dashboard roadmap (ported from MF
    # 2026-05-15). Returns a recent slice of a systemd unit's journal
    # so the operator can scan ERROR/WARN across the fleet from the
    # dashboard. Unit allowlist bounds the surface; helper module
    # `utils.fleet_logs` handles the journalctl mechanics + 60s cache.
    #
    # MA's allowlist mirrors MF's plus the MA-side daemons. Shared
    # infra (rnsd, mosquitto, meshtasticd) is listed both places —
    # whichever box the dashboard targets gets the right log feed.
    # ─────────────────────────────────────────────────────────────────
    _FLEET_LOG_UNITS = {
        # MA-specific system units
        "meshanchor":         ("system", "MeshAnchor entry service"),
        "meshanchor-daemon":  ("system", "MeshAnchor headless daemon"),
        "meshanchor-map":     ("system", "MeshAnchor map :5000 / :5002"),
        # Shared infra
        "rnsd":         ("system", "Reticulum daemon"),
        "meshtasticd":  ("system", "Meshtastic radio daemon"),
        "mosquitto":    ("system", "MQTT broker"),
        # User-scope lab units (installed when the host is on the
        # tracer/echo rotation — see project_tracer_durable_state_rollout)
        "meshforge-tracer":     ("user", "Lab tracer (10-min fire)"),
        "meshforge-echo":       ("user", "Lab echo responder"),
        "meshforge-synth-soak": ("user", "Lab synth soak (hourly fire)"),
        "meshforge-lab-rollup": ("user", "Lab rollup writer"),
        "nomadnet":             ("user", "NomadNet TUI (tmux)"),
    }

    _FLEET_LOG_MAX_N = 200
    _FLEET_LOG_DEFAULT_N = 50

    def _serve_fleet_logs(self) -> None:
        """GET /fleet/logs?unit=<id>&n=<int>&priority=<level>

        Same shape as MF's endpoint — the dashboard's renderLogs() is
        unit-aware but otherwise agnostic to which daemon answered.
        """
        from urllib.parse import urlparse, parse_qs

        qs = parse_qs(urlparse(self.path).query)
        unit = (qs.get("unit", [""])[0] or "").strip()
        if unit not in self._FLEET_LOG_UNITS:
            self._serve_json(
                {"error": "unit not allowlisted",
                 "allowed": sorted(self._FLEET_LOG_UNITS.keys())},
                status=400,
            )
            return
        scope, _desc = self._FLEET_LOG_UNITS[unit]

        try:
            n = int(qs.get("n", [self._FLEET_LOG_DEFAULT_N])[0])
        except (ValueError, TypeError):
            n = self._FLEET_LOG_DEFAULT_N
        n = max(1, min(self._FLEET_LOG_MAX_N, n))

        priority = (qs.get("priority", ["warn"])[0] or "warn").lower()
        if priority not in ("err", "warning", "warn", "info", "debug",
                            "notice", "crit", "alert", "emerg"):
            priority = "warn"
        journal_priority = "warning" if priority == "warn" else priority

        from utils.fleet_logs import fetch_unit_logs
        payload = fetch_unit_logs(
            unit=unit, scope=scope, n=n, priority=journal_priority,
        )
        self._serve_json(payload)

    # ─────────────────────────────────────────────────────────────────
    # /fleet/tests + /fleet/run-test — T1.5 manual lab fires.
    # GET /fleet/tests          → list available tests + last-fire info
    # POST /fleet/run-test      → fire one (body: {"test": "<id>"})
    #
    # Same path/units as MF's allowlist so dashboard test buttons render
    # consistently whether the targeted host is MF or MA.
    # ─────────────────────────────────────────────────────────────────
    _FLEET_TESTS = {
        "tracer": (
            "meshforge-tracer.service", "user",
            "Tracer fire",
            "Send one LXMF PING to each fleet peer and record ACK RTTs.",
        ),
        "synth-soak": (
            "meshforge-synth-soak.service", "user",
            "Synth soak fire",
            "Multi-user LXMF load fire (~60s).",
        ),
        "lab-rollup": (
            "meshforge-lab-rollup.service", "user",
            "Refresh lab rollup",
            "Re-aggregate tracer state files into the markdown panel.",
        ),
    }

    def _serve_fleet_tests_list(self) -> None:
        """Return the test allowlist + last-fire metadata per unit."""
        from monitoring.fleet_aggregator import (
            _list_timers_scope, _normalize_timer,
        )
        import socket as _socket
        import time as _time

        now = _time.time()
        timer_index: Dict[str, Dict[str, Any]] = {}
        for scope in ("system", "user"):
            for raw in _list_timers_scope(scope):
                entry = _normalize_timer(raw, scope, now)
                if entry is None:
                    continue
                activates = raw.get("activates") or ""
                if activates:
                    timer_index[activates] = entry

        tests = []
        for test_id, (unit, scope, label, desc) in self._FLEET_TESTS.items():
            paired = timer_index.get(unit)
            tests.append({
                "id": test_id,
                "unit": unit,
                "scope": scope,
                "label": label,
                "description": desc,
                "last_fire_unix": paired["last_fire_unix"] if paired else None,
                "next_fire_unix": paired["next_fire_unix"] if paired else None,
                "age_s": paired["age_s"] if paired else None,
            })

        self._serve_json({"tests": tests, "host": _socket.gethostname()})

    def _serve_fleet_run_test(self) -> None:
        """POST /fleet/run-test — fire an allowlisted lab unit.

        Body: {"test": "<id>"} where id ∈ self._FLEET_TESTS. No
        localhost gate at this entry point — the allowlist is the
        authorization surface, and the units are oneshots with no
        write side-effects beyond their own logging.
        """
        import json as _json
        import socket as _socket

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body_raw = self.rfile.read(length) if length > 0 else b""
            body = _json.loads(body_raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._serve_json(
                {"ok": False, "error": "body must be valid JSON"},
                status=400,
            )
            return

        test_id = (body.get("test") or "").strip()
        if test_id not in self._FLEET_TESTS:
            self._serve_json(
                {"ok": False, "error": "test not allowlisted",
                 "allowed": sorted(self._FLEET_TESTS.keys())},
                status=400,
            )
            return

        unit, scope, _label, _desc = self._FLEET_TESTS[test_id]
        from utils.fleet_test_runner import fire_unit
        result = fire_unit(unit=unit, scope=scope)
        result["test"] = test_id
        result["host"] = _socket.gethostname()
        self._serve_json(result, status=200 if result.get("ok") else 500)
