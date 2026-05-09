"""Fleet Monitor — engineering-grade NOC dashboard surface.

Source-of-truth UI: the operator opens this menu and gets the same
JSON shapes the always-on web dashboard renders. Each menu action
fetches one of the map service's `/fleet/*` endpoints and pretty-
prints the result.

The map service is the data layer (see `monitoring.fleet_aggregator`
and `monitoring.fleet_rollup`); the TUI stays a thin client. Fetches
hit the loopback HTTP listener on the same host so they don't depend
on rnsd or any radio being up — the only requirement is that
`meshanchor-map.service` is running.

Batch 22 — added 2026-05-09 alongside the multi-host fleet rollup
shipped in PRs #100 (S1) and the follow-up Session 2 PR.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


DEFAULT_MAP_URL = "http://127.0.0.1:5001"
"""Canonical NOC layout. Dev surfaces (e.g. VolcanoAI :5002) override
through the env var below — keeps the handler portable across hosts
without hardcoding a development port."""

MAP_URL_ENV = "MESHANCHOR_MAP_URL"
HTTP_TIMEOUT_S = 5.0


class FleetMonitorHandler(BaseHandler):
    """NOC fleet monitor — SLO snapshot, activity feed, peer rollup,
    federation peers. Reads the map service's `/fleet/*` JSON."""

    handler_id = "fleet_monitor"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("fleet_monitor",
             "Fleet Monitor       NOC SLO + activity + peers",
             None),
        ]

    def execute(self, action: str) -> None:
        if action == "fleet_monitor":
            self._top_menu()

    # ──────────────────────────────────────────────────────────────
    # Menu
    # ──────────────────────────────────────────────────────────────

    def _top_menu(self) -> None:
        while True:
            choices = [
                ("slo", "SLO Snapshot        Top-line health rollup"),
                ("activity", "Activity Feed       Recent chat + boundary anomalies"),
                ("rollup", "Peer Rollup         Multi-host fleet status"),
                ("federation", "Federation Peers    RNS / LXMF announce table"),
                ("config", "Edit Peer Config    ~/.config/meshanchor/fleet.json"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "Fleet Monitor",
                "Engineering-grade NOC observability — same JSON\n"
                "the web dashboard renders.",
                choices,
            )
            if choice is None or choice == "back":
                break
            if choice == "slo":
                self.ctx.safe_call("fleet slo", self._show_slo)
            elif choice == "activity":
                self.ctx.safe_call("fleet activity", self._show_activity)
            elif choice == "rollup":
                self.ctx.safe_call("fleet rollup", self._show_rollup)
            elif choice == "federation":
                self.ctx.safe_call("fleet federation", self._show_federation)
            elif choice == "config":
                self.ctx.safe_call("fleet config", self._show_config_path)

    # ──────────────────────────────────────────────────────────────
    # Renderers
    # ──────────────────────────────────────────────────────────────

    def _show_slo(self) -> None:
        body = self._fetch_json("/fleet/slo")
        if body is None:
            return
        services = body.get("services") or {}
        radio = body.get("radio") or {}
        top = body.get("boundaries_top") or []
        lines = [
            f"Host:       {body.get('host', '?')}",
            f"Status:     {body.get('overall_status', '?')}",
            f"Uptime:     {self._format_uptime(body.get('uptime_s', 0))}",
            "",
            f"Services:   {services.get('available', 0)}/{services.get('total', 0)} available",
        ]
        for state, count in (services.get("by_state") or {}).items():
            lines.append(f"  {state:14s} {count}")
        lines.append("")
        lines.append(f"Radio:      {'connected' if radio.get('connected') else 'offline'}")
        if radio.get("name"):
            lines.append(f"  name      {radio['name']}")
        if radio.get("preset"):
            lines.append(f"  preset    {radio['preset']}")
        if top:
            lines.append("")
            lines.append("Top boundaries (count, p95):")
            for row in top[:6]:
                lines.append(
                    f"  {row['label'][:36]:36s} {row['count']:5d}  "
                    f"{row['p95_s']*1000:7.1f}ms"
                )
        self._render(body, "Fleet SLO", lines)

    def _show_activity(self) -> None:
        body = self._fetch_json("/fleet/activity")
        if body is None:
            return
        chat = body.get("chat_recent") or []
        slow = body.get("slow_boundaries") or []
        lines = [
            f"Host:        {body.get('host', '?')}",
            f"Chat total:  {body.get('chat_total', 0)}",
            "",
            "Recent chat:" if chat else "Recent chat: (empty)",
        ]
        for entry in chat[-12:]:
            text = entry.get("text", "")
            sender = entry.get("sender") or entry.get("from") or ""
            lines.append(f"  {sender[:14]:14s} {text[:60]}")
        lines.append("")
        if slow:
            lines.append("Boundary anomalies:")
            for row in slow[:8]:
                lines.append(
                    f"  {row['label'][:36]:36s} "
                    f"slow={row['slow_count']:3d}  "
                    f"err={row['error_count']:3d}"
                )
        else:
            lines.append("Boundary anomalies: (none)")
        self._render(body, "Fleet Activity", lines)

    def _show_rollup(self) -> None:
        body = self._fetch_json("/fleet/rollup")
        if body is None:
            return
        self_snap = body.get("self_snapshot") or {}
        peers = body.get("peers") or []
        lines = [
            f"Self:       {body.get('self_host', '?')}",
            f"Config:     {body.get('config_source', '(none)')}",
            "",
        ]
        for entry in [{"name": "self (this host)", "snapshot": self_snap, "error": None}] + peers:
            name = entry.get("name", "?")
            err = entry.get("error")
            snap = entry.get("snapshot") or {}
            # Each entry's snapshot is a slo_view shape:
            # `services` is a rollup dict {total, available, by_state},
            # NOT the per-service dict. Be defensive in case a peer is
            # running an older build that returned the full snapshot.
            services = snap.get("services") if isinstance(snap, dict) else None
            if isinstance(services, dict) and "total" in services:
                available = services.get("available", 0)
                total = services.get("total", 0)
            elif isinstance(services, dict):
                available = sum(
                    1 for s in services.values()
                    if isinstance(s, dict) and s.get("available")
                )
                total = len(services)
            else:
                available = total = 0
            radio = (snap.get("radio") or {}) if isinstance(snap, dict) else {}
            radio_state = "online" if radio.get("connected") else "offline"
            status = snap.get("overall_status", "?") if isinstance(snap, dict) else "?"
            if err:
                lines.append(f"  {name[:24]:24s}  ERROR  {err[:42]}")
            else:
                lines.append(
                    f"  {name[:24]:24s}  {status:9s}  "
                    f"svc={available}/{total}  radio={radio_state}"
                )
        self._render(body, "Fleet Rollup", lines)

    def _show_federation(self) -> None:
        body = self._fetch_json("/fleet/federation")
        if body is None:
            return
        if not body.get("enabled"):
            self.ctx.dialog.msgbox(
                "Federation Peers",
                "Federation scraping disabled in fleet.json.\n"
                "Set federation.scrape_rns_announces = true to enable.",
            )
            return
        peers = body.get("peers") or []
        sources = body.get("sources") or []
        lines = [
            f"Window:     {body.get('fresh_window_s', 0)}s freshness",
            f"Peers:      {len(peers)}",
            f"Sources:    {', '.join(sources) if sources else '(none)'}",
            "",
        ]
        for peer in peers[:30]:
            name = peer.get("name") or peer.get("node_id", "")
            age = peer.get("last_seen_age_s")
            age_str = self._format_age(age)
            src = peer.get("source", "?")
            kind = peer.get("service_type") or peer.get("source_origin", "")
            lines.append(f"  {name[:20]:20s}  {age_str:>10s}  [{src}]  {kind}")
        if len(peers) > 30:
            lines.append(f"  ... and {len(peers) - 30} more")
        self._render(body, "Federation Peers", lines)

    def _show_config_path(self) -> None:
        from monitoring.fleet_config import get_fleet_config_path
        path = get_fleet_config_path()
        exists = path.exists()
        lines = [
            f"Path:    {path}",
            f"Status:  {'present' if exists else 'NOT FOUND'}",
            "",
            "Edit this file to add peer hosts. Schema:",
            "  {",
            '    "peers": [',
            '      {"name": "...", "host": "...", "port": 5001, "kind": "noc"}',
            "    ],",
            '    "federation": {"scrape_rns_announces": true,',
            '                   "fresh_window_s": 7200}',
            "  }",
        ]
        self.ctx.dialog.msgbox("Fleet Config", "\n".join(lines))

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _fetch_json(self, path: str) -> Optional[Dict[str, Any]]:
        import os
        base = os.environ.get(MAP_URL_ENV, DEFAULT_MAP_URL).rstrip("/")
        url = f"{base}{path}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                body = resp.read()
            return json.loads(body.decode("utf-8"))
        except urllib.error.URLError as e:
            self.ctx.dialog.msgbox(
                "Fleet Fetch Failed",
                f"Could not reach {url}\n\n"
                f"{e}\n\n"
                f"Make sure meshanchor-map.service is running.\n"
                f"Override the URL with {MAP_URL_ENV}.",
            )
            return None
        except (json.JSONDecodeError, ValueError) as e:
            self.ctx.dialog.msgbox("Fleet Fetch Failed",
                                   f"Bad JSON from {url}:\n{e}")
            return None

    def _render(self, body: Dict[str, Any], title: str, lines: List[str]) -> None:
        errors = body.get("errors") or []
        if errors:
            lines.append("")
            lines.append("Errors:")
            for entry in errors[:6]:
                lines.append(f"  {entry.get('source', '?')}: {entry.get('error', '')[:60]}")
        self.ctx.dialog.msgbox(title, "\n".join(lines))

    @staticmethod
    def _format_uptime(uptime_s: float) -> str:
        try:
            uptime_s = float(uptime_s)
        except (TypeError, ValueError):
            return "?"
        h = int(uptime_s // 3600)
        m = int((uptime_s % 3600) // 60)
        return f"{h}h {m}m"

    @staticmethod
    def _format_age(age_s) -> str:
        if age_s is None:
            return "(never)"
        try:
            age = float(age_s)
        except (TypeError, ValueError):
            return "?"
        if age < 60:
            return f"{int(age)}s ago"
        if age < 3600:
            return f"{int(age / 60)}m ago"
        return f"{age / 3600:.1f}h ago"
