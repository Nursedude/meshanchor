"""LXMF Broadcast Bridge TUI handler.

Diagnostic + operator-action surface for the bridge in
``src/gateway/lxmf_broadcast_bridge.py``. The bridge fans MeshCore
broadcasts out as LXMF DMs to subscribers in its SQLite store; before
this handler existed, the only way for a local NomadNet to land in
that store was a remote subscribe-DM round-trip. Now the operator can
see the bridge state at a glance and one-tap subscribe the local
NomadNet (or MeshChatX, or any client surfaced by
`_lxmf_clients_discovery`).

The TUI never opens the subscriber DB directly — every call goes
through the daemon's HTTP surface on :8081 so the bridge's in-process
SubscriberStore lock stays authoritative.

Section: ``meshcore``. Gated by the ``meshcore`` deployment profile.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from handler_protocol import BaseHandler

from handlers._lxmf_clients_discovery import (
    LocalLXMFClient,
    discover_local_lxmf_clients,
)

logger = logging.getLogger(__name__)


class LXMFBroadcastHandler(BaseHandler):
    """TUI surface for the MeshCore→LXMF broadcast bridge."""

    handler_id = "lxmf_broadcast"
    menu_section = "meshcore"

    # Same daemon HTTP base as the existing /chat /radio TUI consumers.
    DAEMON_API_BASE = "http://127.0.0.1:8081"
    HTTP_TIMEOUT = 10

    def menu_items(self):
        return [
            (
                "lxmf_broadcast",
                "LXMF Broadcast Bridge   Status, subscribe local LXMF clients",
                "meshcore",
            ),
        ]

    def execute(self, action):
        if action == "lxmf_broadcast":
            self._bridge_menu()

    # ── Top-level menu ──────────────────────────────────────────────────

    def _bridge_menu(self) -> None:
        while True:
            choices = [
                ("status", "Bridge Status           Destination, channels, subscribers"),
                ("subscribe", "Subscribe Local Client  Add NomadNet / MeshChatX hash"),
                ("stale", "Stale Subscribers       Review + prune failing subscribers"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "LXMF Broadcast Bridge",
                "MeshCore broadcasts → LXMF DMs to subscribers.\n"
                "Diagnostic + operator-action surface.",
                choices,
            )
            if choice is None or choice == "back":
                return
            if choice == "status":
                self.ctx.safe_call("Bridge status", self._show_status)
            elif choice == "subscribe":
                self.ctx.safe_call("Subscribe local client", self._subscribe_local)
            elif choice == "stale":
                self.ctx.safe_call("Stale subscribers", self._stale_subscribers)

    # ── Actions ─────────────────────────────────────────────────────────

    def _show_status(self) -> None:
        result = self._get_json("/lxmf-broadcast/status")
        if not result.get("ok"):
            self.ctx.dialog.msgbox(
                "Bridge Unreachable",
                self._unreachable_body(result),
            )
            return
        status = result.get("data") or {}
        self.ctx.dialog.msgbox(
            "LXMF Broadcast Bridge — Status",
            self._format_status(status),
        )

    def _subscribe_local(self) -> None:
        clients = discover_local_lxmf_clients()
        if not clients:
            self.ctx.dialog.msgbox(
                "No Local LXMF Clients Found",
                "No NomadNet logfile and no entries in\n"
                "  ~/.config/meshanchor/local_lxmf_clients.json\n\n"
                "Launch NomadNet at least once so it writes its identity to\n"
                "  ~/.nomadnetwork/logfile\n"
                "or drop a JSON list of {name, hash} entries into the path\n"
                "above to register MeshChatX or another LXMF client.",
            )
            return

        choices: List[Tuple[str, str]] = [
            (
                client.hash,
                f"{client.name}  {client.hash[:8]}…  ({client.source})",
            )
            for client in clients
        ]
        choices.append(("back", "Back"))
        picked = self.ctx.dialog.menu(
            "Subscribe Local LXMF Client",
            "Choose a client to receive MeshCore broadcast fan-outs.\n"
            "Idempotent — re-selecting the same client is a no-op.",
            choices,
        )
        if picked is None or picked == "back":
            return

        client = next((c for c in clients if c.hash == picked), None)
        if client is None:
            return

        post = self._post_json(
            "/lxmf-broadcast/subscribers",
            {"lxmf_hash": client.hash},
        )
        if not post.get("ok"):
            self.ctx.dialog.msgbox(
                "Subscribe Failed",
                self._unreachable_body(post),
            )
            return
        data = post.get("data") or {}
        added = data.get("added")
        total = data.get("total", "?")
        if added:
            verdict = f"Added {client.name} ({client.hash[:8]}…)."
        else:
            verdict = f"{client.name} was already subscribed."
        self.ctx.dialog.msgbox(
            "Subscription Updated",
            f"{verdict}\n\nTotal subscribers: {total}\n\n"
            f"Send a MeshCore broadcast from a peer node to verify\n"
            f"fan-out to this client.",
        )

    def _stale_subscribers(self) -> None:
        """List subscribers in `stale` or `dead` state; let operator prune one.

        Auto-prune is intentionally off — the rule is "dead row is better
        than silent prune." This action gives the operator the surface to
        decide, with a double-confirm before DELETE actually fires.
        """
        result = self._get_json("/lxmf-broadcast/status")
        if not result.get("ok"):
            self.ctx.dialog.msgbox(
                "Bridge Unreachable",
                self._unreachable_body(result),
            )
            return
        status = result.get("data") or {}
        subs = status.get("subscribers") or []
        stale = [
            s for s in subs
            if s.get("state") in ("stale", "dead")
        ]
        if not stale:
            self.ctx.dialog.msgbox(
                "No Stale Subscribers",
                "All subscribers are healthy or in active retry.\n\n"
                "Stale subscribers (24+ hours failing) and dead\n"
                "subscribers (7+ days failing) would appear here for\n"
                "operator review and manual prune.",
            )
            return

        choices: List[Tuple[str, str]] = []
        for s in stale:
            h = s.get("lxmf_hash") or "?"
            choices.append((
                h,
                f"{h[:16]}…  state={s.get('state', '?'):<5}  "
                f"fails={s.get('consecutive_failures', 0):<3}  "
                f"last_fanout={s.get('last_fanout_enqueued') or 'never'}",
            ))
        choices.append(("back", "Back"))

        picked = self.ctx.dialog.menu(
            "Stale Subscribers",
            "These subscribers have been failing for an extended period.\n"
            "Select one to remove from the fan-out list. This is permanent —\n"
            "the row can be re-added later via Subscribe Local Client.",
            choices,
        )
        if picked is None or picked == "back":
            return
        target = next((s for s in stale if s.get("lxmf_hash") == picked), None)
        if target is None:
            return

        confirmed = self.ctx.dialog.yesno(
            "Confirm Prune",
            f"Remove subscriber {picked[:16]}…?\n\n"
            f"State:    {target.get('state', '?')}\n"
            f"Failures: {target.get('consecutive_failures', 0)}\n"
            f"Last fan-out (enqueued): {target.get('last_fanout_enqueued') or 'never'}\n"
            f"Last fail: {target.get('last_failure_at') or 'never'}\n\n"
            f"This is logged but cannot be undone here.",
        )
        if not confirmed:
            return

        post = self._delete_json(f"/lxmf-broadcast/subscribers/{picked}")
        if not post.get("ok"):
            self.ctx.dialog.msgbox("Prune Failed", self._unreachable_body(post))
            return
        data = post.get("data") or {}
        removed = data.get("removed")
        total = data.get("total", "?")
        verdict = (
            f"Removed {picked[:8]}…."
            if removed else
            f"{picked[:8]}… was not in the subscriber list."
        )
        self.ctx.dialog.msgbox(
            "Subscriber Pruned",
            f"{verdict}\n\nRemaining subscribers: {total}",
        )

    # ── Renderers ───────────────────────────────────────────────────────

    def _format_status(self, status: Dict[str, Any]) -> str:
        running = status.get("running")
        dest_hex = status.get("destination_hash") or "(not yet registered)"
        display_name = status.get("display_name") or "(no display name)"
        channels = status.get("channels") or []
        autosub = status.get("autosubscribe")
        announce_interval = status.get("announce_interval_sec")
        started_at = status.get("started_at_iso")
        stats = status.get("stats") or {}
        subs = status.get("subscribers") or []

        if started_at:
            age = self._age_seconds(started_at)
            age_line = (
                f"Started:        {started_at} ({age}s ago)"
                if age is not None else
                f"Started:        {started_at}"
            )
        else:
            age_line = "Started:        (not started)"

        sub_block = self._format_subscriber_rows(subs)

        return (
            f"Running:        {bool(running)}\n"
            f"Destination:    {dest_hex}\n"
            f"Display Name:   {display_name}\n"
            f"Channels:       {channels}\n"
            f"Announce every: {announce_interval}s\n"
            f"Autosubscribe:  {bool(autosub)}\n"
            f"{age_line}\n"
            f"\n"
            f"--- Stats ---\n"
            f"  fanouts:                {stats.get('fanouts', 0)}\n"
            f"  subscribes:             {stats.get('subscribes', 0)}\n"
            f"  unsubscribes:           {stats.get('unsubscribes', 0)}\n"
            f"  filtered_channel:       {stats.get('filtered_channel', 0)}\n"
            f"  filtered_non_meshcore:  {stats.get('filtered_non_meshcore', 0)}\n"
            f"  errors:                 {stats.get('errors', 0)}\n"
            f"\n"
            f"--- Subscribers ({len(subs)}) ---\n"
            f"  (last_fanout = last successful ENQUEUE to the LXMF router;\n"
            f"   delivery is best-effort and NOT confirmed — #16)\n"
            f"{sub_block}"
        )

    def _format_subscriber_rows(self, subs: List[Dict[str, Any]]) -> str:
        if not subs:
            return (
                "  (none — use 'Subscribe Local Client' to add NomadNet)"
            )
        lines: List[str] = []
        for s in subs:
            last_fanout = s.get("last_fanout_enqueued") or "never"
            last_fail = s.get("last_failure_at") or "never"
            tier = s.get("tier") or "external"
            state = s.get("state") or "healthy"
            fails = s.get("consecutive_failures", 0)
            lines.append(
                f"  {s.get('lxmf_hash', '?'):<32}  "
                f"tier={tier:<10}  state={state:<8}  fails={fails:<3}  "
                f"added={s.get('added_at') or '?'}  "
                f"last_fanout={last_fanout}  last_fail={last_fail}"
            )
        return "\n".join(lines)

    @staticmethod
    def _age_seconds(iso: str) -> Optional[int]:
        try:
            started = datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return None
        delta = datetime.utcnow() - started
        return max(0, int(delta.total_seconds()))

    @staticmethod
    def _unreachable_body(result: Dict[str, Any]) -> str:
        status = result.get("status")
        error = result.get("error") or "(no detail)"
        if status == 503:
            return (
                f"Daemon responded 503 — the bridge is not active.\n\n"
                f"Detail: {error}\n\n"
                f"Check that meshanchor-daemon.service is running and\n"
                f"that lxmf_broadcast.enabled is True in gateway.json."
            )
        if status is None:
            return (
                f"Could not reach the daemon on {LXMFBroadcastHandler.DAEMON_API_BASE}.\n\n"
                f"Detail: {error}\n\n"
                f"Is meshanchor-daemon.service running?"
            )
        return f"Daemon responded {status}: {error}"

    # ── HTTP helpers ────────────────────────────────────────────────────

    def _get_json(self, path: str) -> Dict[str, Any]:
        """GET <DAEMON_API_BASE><path> → {ok, data|status, error}."""
        url = f"{self.DAEMON_API_BASE}{path}"
        try:
            req = urllib.request.Request(
                url, headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self.HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                return {"ok": True, "data": payload}
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = payload.get("error") or str(e)
            except (ValueError, OSError):
                msg = str(e)
            return {"ok": False, "status": e.code, "error": msg}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": None, "error": str(e)}

    def _delete_json(self, path: str) -> Dict[str, Any]:
        """DELETE <DAEMON_API_BASE><path> → {ok, data|status, error}."""
        url = f"{self.DAEMON_API_BASE}{path}"
        try:
            req = urllib.request.Request(
                url,
                method="DELETE",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                return {"ok": True, "data": payload}
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = payload.get("error") or str(e)
            except (ValueError, OSError):
                msg = str(e)
            return {"ok": False, "status": e.code, "error": msg}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": None, "error": str(e)}

    def _post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.DAEMON_API_BASE}{path}"
        data = json.dumps(body).encode("utf-8")
        try:
            req = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=self.HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                return {"ok": True, "data": payload}
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                msg = payload.get("error") or str(e)
            except (ValueError, OSError):
                msg = str(e)
            return {"ok": False, "status": e.code, "error": msg}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"ok": False, "status": None, "error": str(e)}
