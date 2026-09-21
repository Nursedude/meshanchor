"""Chat flows for the MeshCore TUI handler.

Extracted from ``meshcore.py`` (2026-09-21, roadmap 1a) — one module per
pane, the same mixin pattern as ``_meshcore_radio_ops.py`` and the
daemon-side ``gateway/meshcore_*_mixin.py`` family.

The methods here run on a ``MeshCoreHandler`` instance that already
provides ``self.ctx`` and ``self.CHAT_API_BASE``.

``CHAT_POLL_INTERVAL`` travels with this mixin because the tail loop is
its only consumer. ``CHAT_API_BASE`` stays on the host class, which has
several consumers (radio config, this pane, ``meshcore_cli``,
``meshcore_positions``, and a test) — one constant, one home.
"""

from __future__ import annotations

from backend import clear_screen


class MeshCoreChatMixin:
    """Mixin: chat menu, message fetch/send, recent view, live tail."""

    CHAT_POLL_INTERVAL = 2.0

    def _meshcore_chat(self):
        """Interactive chat menu against the daemon's chat API."""
        clear_screen()
        print("=== MeshCore Chat ===\n")

        if not self._chat_api_reachable():
            print("  Daemon's chat API on :8081 is not reachable.")
            print("  Start the daemon: sudo systemctl start meshanchor-daemon.service")
            self.ctx.wait_for_enter()
            return

        while True:
            choices = [
                ("recent", "View Recent Messages   Last ~200 entries"),
                ("send_chan", "Send to Channel       Broadcast on slot N"),
                ("send_dm", "Send DM               Direct-message a node"),
                ("watch", "Watch (poll every 2s)  Live tail until Ctrl-C"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "MeshCore Chat",
                "Daemon-mediated chat through p4 (via :8081)",
                choices,
            )
            if choice is None or choice == "back":
                return
            if choice == "recent":
                self._chat_view_recent()
            elif choice == "send_chan":
                self._chat_send_channel()
            elif choice == "send_dm":
                self._chat_send_dm()
            elif choice == "watch":
                self._chat_watch_tail()

    def _chat_api_reachable(self) -> bool:
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.CHAT_API_BASE}/chat/messages?since=0",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2):
                return True
        except urllib.error.HTTPError as e:
            # 503 means daemon is up but MeshCore not active — still
            # "reachable" enough that the menu is useful for diagnosis.
            return e.code in (503,)
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def _chat_fetch_messages(self, since_id: int = 0):
        import json as _json
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.CHAT_API_BASE}/chat/messages?since={since_id}",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return _json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"error": f"connection: {e}"}

    def _chat_post_send(self, text: str, channel: int = 0,
                        destination=None) -> dict:
        import json as _json
        import urllib.error
        import urllib.request
        body = _json.dumps({
            "text": text, "channel": channel, "destination": destination,
        }).encode()
        try:
            req = urllib.request.Request(
                f"{self.CHAT_API_BASE}/chat/send",
                data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return _json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return {"error": f"connection: {e}"}

    def _chat_format_entry(self, entry: dict) -> str:
        from datetime import datetime as _dt
        ts = entry.get("ts", 0)
        ts_str = _dt.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "??:??:??"
        direction = entry.get("direction", "?")
        arrow = "<<" if direction == "rx" else ">>"
        chan = entry.get("channel")
        chan_str = f"CHAN{chan}" if chan is not None else "DM"
        sender = entry.get("sender") or entry.get("destination") or "?"
        text = entry.get("text", "")
        return f"[{ts_str}] {arrow} {chan_str:<6} {str(sender)[:12]:<12} {text}"

    def _chat_view_recent(self):
        clear_screen()
        print("=== Recent MeshCore Chat ===\n")
        result = self._chat_fetch_messages(0)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            self.ctx.wait_for_enter()
            return
        messages = result.get("messages", [])
        if not messages:
            print("  (no messages yet)")
        for entry in messages[-50:]:
            print(f"  {self._chat_format_entry(entry)}")
        print(f"\n  Showing last {min(len(messages), 50)} of {len(messages)} entries.")
        self.ctx.wait_for_enter()

    def _chat_send_channel(self):
        slot = self.ctx.dialog.inputbox(
            "Send to Channel",
            "Channel slot (0=Public, 1/2=meshanchor on p4):",
            "1",
        )
        if not slot:
            return
        try:
            slot_int = int(slot.strip())
        except ValueError:
            self.ctx.dialog.msgbox("Send to Channel", "Slot must be an integer.")
            return
        text = self.ctx.dialog.inputbox(
            "Send to Channel",
            f"Message text (broadcast on slot {slot_int}):",
            "",
        )
        if not text:
            return
        result = self._chat_post_send(text, channel=slot_int)
        if "error" in result:
            self.ctx.dialog.msgbox("Send Result", f"FAILED: {result['error']}")
        else:
            self.ctx.dialog.msgbox(
                "Send Result",
                f"Queued on CHAN{slot_int}: {text[:60]}",
            )

    def _chat_send_dm(self):
        dest = self.ctx.dialog.inputbox(
            "Send DM",
            "Destination (pubkey-prefix hex or contact name):",
            "",
        )
        if not dest:
            return
        text = self.ctx.dialog.inputbox(
            "Send DM",
            f"Message text (DM to {dest}):",
            "",
        )
        if not text:
            return
        result = self._chat_post_send(text, destination=dest.strip())
        if "error" in result:
            self.ctx.dialog.msgbox("Send Result", f"FAILED: {result['error']}")
        else:
            self.ctx.dialog.msgbox(
                "Send Result",
                f"Queued DM to {dest}: {text[:60]}",
            )

    def _chat_watch_tail(self):
        """Live-tail messages until Ctrl-C."""
        import time
        clear_screen()
        print("=== Watching MeshCore Chat (Ctrl-C to stop) ===\n")
        last_id = 0
        try:
            while True:
                result = self._chat_fetch_messages(last_id)
                if "error" in result:
                    print(f"  ERROR: {result['error']}")
                    time.sleep(self.CHAT_POLL_INTERVAL)
                    continue
                for entry in result.get("messages", []):
                    print(f"  {self._chat_format_entry(entry)}")
                    last_id = max(last_id, entry.get("id", last_id))
                time.sleep(self.CHAT_POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n  (stopped)")
            self.ctx.wait_for_enter()
