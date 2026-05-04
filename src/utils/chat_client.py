"""Interactive MeshCore chat client (HTTP-backed, urllib only).

This is the long-lived client that runs inside the ``meshcore-chat``
tmux session managed by the ChatPaneHandler. It talks to the gateway
daemon's local HTTP API on 127.0.0.1:8081 — never to the radio
directly — so it coexists with the daemon without serial contention.

Endpoints consumed:
  GET  /chat/messages?since=<id>    — poll loop
  GET  /chat/channels               — slot enumeration for /channels
  POST /chat/send                   — outbound message

Slash commands inside the pane:
  /ch <n>            switch channel slot for outbound messages
  /dm <hex_dest>     send a single direct message
  /channels          list channels seen on the wire
  /quit              exit the client (systemd will restart it)

All other input is sent as a channel message on the active slot.

The client is deliberately single-threaded: a polling loop on a
background thread renders incoming messages, while the main thread
blocks on stdin. urllib is the only HTTP dependency — no requests,
no aiohttp — so a fresh MeshAnchor box can run the client without
any pip installs.
"""

from __future__ import annotations

import json
import os
import select
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


CHAT_API_DEFAULT = "http://127.0.0.1:8081"
POLL_INTERVAL_S = 2.0
HTTP_TIMEOUT_S = 5.0


def _color(code: str, text: str) -> str:
    """Wrap ``text`` in ANSI ``code`` if stdout is a tty, else return raw."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _format_ts(ts: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(ts))
    except (TypeError, ValueError, OSError):
        return "??:??:??"


def _format_entry(entry: Dict[str, Any]) -> str:
    """Render one chat entry as a single line for the operator."""
    ts = _format_ts(entry.get("ts", 0))
    direction = entry.get("direction", "rx")
    channel = entry.get("channel")
    sender = entry.get("sender") or "?"
    destination = entry.get("destination")
    text = entry.get("text", "")

    if direction == "tx":
        arrow = _color("33", "→")  # yellow
    else:
        arrow = _color("32", "←")  # green

    if destination:
        # DM
        tag = _color("36", f"DM[{destination[:8]}]")
    elif channel is not None:
        tag = _color("35", f"ch{channel}")
    else:
        tag = "?"

    name = _color("1", sender)
    return f"[{ts}] {arrow} {tag} {name}: {text}"


class ChatClient:
    """HTTP-backed MeshCore chat client.

    The polling thread updates ``self._last_id`` and calls ``self._on_entry``
    for each new entry; the main thread reads stdin and dispatches to
    ``self._send`` / slash commands.
    """

    def __init__(self, base_url: str = CHAT_API_DEFAULT) -> None:
        self.base_url = base_url.rstrip("/")
        self._last_id = 0
        self._stop = threading.Event()
        self._channel = 0
        self._poll_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # HTTP helpers (urllib only)
    # ------------------------------------------------------------------

    def _http_get(self, path: str, timeout: float = HTTP_TIMEOUT_S) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        try:
            with urllib_request.urlopen(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                raw = resp.read()
            return json.loads(raw)
        except (urllib_error.URLError, urllib_error.HTTPError,
                json.JSONDecodeError, OSError, ValueError):
            return None

    def _http_post_json(self, path: str, payload: Dict[str, Any],
                        timeout: float = HTTP_TIMEOUT_S) -> Tuple[bool, str]:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib_request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status < 400, body
        except urllib_error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.reason}"
        except (urllib_error.URLError, OSError, ValueError) as e:
            return False, f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    def _poll_once(self) -> List[Dict[str, Any]]:
        body = self._http_get(f"/chat/messages?since={self._last_id}")
        if not body:
            return []
        msgs = body.get("messages") or []
        for m in msgs:
            mid = m.get("id")
            if isinstance(mid, int) and mid > self._last_id:
                self._last_id = mid
        return msgs

    def _poll_loop(self) -> None:
        # Print backlog (last 10 messages) immediately on attach so the
        # operator has context, then switch to delta polling.
        backlog = self._http_get("/chat/messages?since=0")
        if backlog:
            recent = (backlog.get("messages") or [])[-10:]
            for m in recent:
                mid = m.get("id")
                if isinstance(mid, int) and mid > self._last_id:
                    self._last_id = mid
                self._render(_format_entry(m))
        while not self._stop.is_set():
            try:
                for msg in self._poll_once():
                    self._render(_format_entry(msg))
            except Exception as e:  # defensive: never let the loop die
                self._render(_color("31", f"[poll error] {type(e).__name__}: {e}"))
            self._stop.wait(POLL_INTERVAL_S)

    @staticmethod
    def _render(line: str) -> None:
        # Print on its own line + reprint a fake prompt for ergonomics.
        # tmux + a line-buffered terminal handles this without explicit
        # readline rewrites.
        sys.stdout.write(f"\r{line}\n> ")
        sys.stdout.flush()

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def _send_channel(self, text: str) -> None:
        ok, body = self._http_post_json(
            "/chat/send", {"text": text, "channel": self._channel},
        )
        if not ok:
            print(_color("31", f"send failed: {body}"))

    def _send_dm(self, dest: str, text: str) -> None:
        ok, body = self._http_post_json(
            "/chat/send", {"text": text, "destination": dest},
        )
        if not ok:
            print(_color("31", f"DM send failed: {body}"))

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _handle_command(self, line: str) -> bool:
        """Dispatch a slash command. Returns True if recognised."""
        parts = line.split(None, 2)
        cmd = parts[0]

        if cmd == "/quit":
            self._stop.set()
            return True

        if cmd == "/ch":
            if len(parts) < 2:
                print(f"current channel: {self._channel}")
                return True
            try:
                self._channel = int(parts[1])
                print(f"channel → {self._channel}")
            except ValueError:
                print("usage: /ch <integer slot>")
            return True

        if cmd == "/dm":
            if len(parts) < 3:
                print("usage: /dm <hex_dest> <text>")
                return True
            self._send_dm(parts[1], parts[2])
            return True

        if cmd == "/channels":
            body = self._http_get("/chat/channels")
            if not body:
                print(_color("31", "channels: API unreachable"))
                return True
            chans = body.get("channels") or []
            if not chans:
                print("(no channels seen yet)")
            else:
                for c in chans:
                    print(f"  ch{c.get('channel')} last_seen={_format_ts(c.get('last_seen', 0))}")
            return True

        if cmd in ("/help", "/?"):
            print("commands:  /ch <n>   /dm <dest> <text>   /channels   /quit")
            return True

        print(f"unknown command: {cmd}  (try /help)")
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> int:
        # Banner
        print(_color("1", "MeshAnchor MeshCore chat"))
        print(f"  api:     {self.base_url}")
        print(f"  channel: {self._channel}  (use /ch <n> to switch)")
        print(f"  detach:  Ctrl-b d  (tmux session 'meshcore-chat')")
        print("  /help for commands\n")

        # Background poller
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="chat-poll", daemon=True,
        )
        self._poll_thread.start()

        # stdin loop
        try:
            while not self._stop.is_set():
                # select() so KeyboardInterrupt isn't swallowed by readline
                ready, _, _ = select.select([sys.stdin], [], [], 0.5)
                if not ready:
                    continue
                line = sys.stdin.readline()
                if line == "":
                    # EOF
                    break
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.startswith("/"):
                    self._handle_command(line)
                    continue
                self._send_channel(line)
        except KeyboardInterrupt:
            print()
        finally:
            self._stop.set()
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    base = os.environ.get("MESHANCHOR_CHAT_API", CHAT_API_DEFAULT)
    if argv:
        for a in argv:
            if a.startswith("--api="):
                base = a.split("=", 1)[1]
    client = ChatClient(base_url=base)
    return client.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
