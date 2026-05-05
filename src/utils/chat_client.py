"""Interactive MeshCore chat client (HTTP-backed, urllib only).

This is the long-lived client that runs inside the ``meshcore-chat``
tmux session managed by the ChatPaneHandler. It talks to the gateway
daemon's local HTTP API on 127.0.0.1:8081 — never to the radio
directly — so it coexists with the daemon without serial contention.

Endpoints consumed:
  GET  /chat/messages?since=<id>    — poll loop (chat ring buffer)
  GET  /chat/channels               — slots seen on the wire (last_seen)
  GET  /radio                       — slot list with names (idx, name, hash)
  POST /chat/send                   — outbound message → {"queued": true, …}

Slash commands inside the pane:
  /ch <n>            switch channel slot for outbound messages
  /dm <hex> <text>   send a direct message
  /channels          show slot list with names + last-seen
  /help              command cheatsheet
  /quit              exit the client (systemd will restart it)

All other input is sent as a channel message on the active slot.

UX choices:
  * The prompt always shows the active channel: ``[ch1 meshanchor]>``
  * Sends print an immediate ``>>> queued on ch1 …`` confirmation; the
    poll loop then renders the daemon's ring-buffer record a moment
    later (with the real timestamp + ``→`` arrow). Two lines per send
    is intentional — the first proves the daemon accepted it, the
    second proves the daemon recorded it.
  * Channel names come from ``/radio`` (the same source the TUI's
    radio menu uses) and are cached at startup; ``/channels`` refreshes
    the cache.

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
POLL_INTERVAL_S_DEFAULT = 2.0
POLL_INTERVAL_S_MIN = 0.5
HTTP_TIMEOUT_S = 5.0


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """Parse an env-var float with a floor; fall back on garbage input."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return max(v, minimum)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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


def _channel_label(idx: Optional[int], names: Optional[Dict[int, str]] = None) -> str:
    """Render a channel slot as ``ch{idx}`` or ``ch{idx} ({name})``."""
    if idx is None:
        return ""
    if names:
        name = names.get(idx)
        if name:
            return f"ch{idx} ({name})"
    return f"ch{idx}"


def _format_entry(entry: Dict[str, Any],
                  channel_names: Optional[Dict[int, str]] = None) -> str:
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
        tag = _color("35", _channel_label(channel, channel_names))
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

    def __init__(
        self,
        base_url: str = CHAT_API_DEFAULT,
        channel: int = 0,
        poll_interval: float = POLL_INTERVAL_S_DEFAULT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._last_id = 0
        self._stop = threading.Event()
        self._channel = channel
        self._poll_interval = max(poll_interval, POLL_INTERVAL_S_MIN)
        self._poll_thread: Optional[threading.Thread] = None
        # idx → name map populated from /radio. Used for the prompt,
        # the render helpers, and /channels.
        self._channel_names: Dict[int, str] = {}

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
                self._render(_format_entry(m, self._channel_names))
        while not self._stop.is_set():
            try:
                for msg in self._poll_once():
                    self._render(_format_entry(msg, self._channel_names))
            except Exception as e:  # defensive: never let the loop die
                self._render(_color("31", f"[poll error] {type(e).__name__}: {e}"))
            self._stop.wait(self._poll_interval)

    # ------------------------------------------------------------------
    # Channel name cache — populated from /radio (the same source the
    # TUI's radio menu uses). Falls back to "ch{idx}" if /radio is
    # unreachable, so a missing or restarting daemon never blocks chat.
    # ------------------------------------------------------------------

    def _refresh_channel_names(self) -> Dict[int, str]:
        body = self._http_get("/radio")
        if not body:
            return self._channel_names
        radio = body.get("radio") or {}
        channels = radio.get("channels") or []
        names: Dict[int, str] = {}
        for c in channels:
            try:
                idx = int(c.get("idx"))
            except (TypeError, ValueError):
                continue
            name = c.get("name")
            if isinstance(name, str) and name:
                names[idx] = name
        self._channel_names = names
        return names

    def _prompt(self) -> str:
        """Channel-aware prompt suffix shown after each render."""
        return f"[{_channel_label(self._channel, self._channel_names)}]> "

    def _render(self, line: str) -> None:
        # Print on its own line + reprint the channel-aware prompt for
        # ergonomics. tmux + a line-buffered terminal handles this
        # without explicit readline rewrites.
        sys.stdout.write(f"\r{line}\n{self._prompt()}")
        sys.stdout.flush()

    def _render_tx_echo(self, *, channel: Optional[int],
                        destination: Optional[str], text: str) -> None:
        """Confirm the daemon accepted a /chat/send POST.

        Distinct from the polled TX entry the daemon eventually records
        in its ring buffer. The polled echo arrives later with a real
        timestamp + ``→`` arrow; this immediate echo proves the POST
        succeeded so the operator never wonders whether their input
        landed.
        """
        if destination:
            tag = _color("36", f"DM[{destination[:8]}]")
        else:
            tag = _color("35", _channel_label(channel, self._channel_names))
        prefix = _color("33", ">>>")  # yellow, distinct from polled "→"
        marker = _color("32", "queued")  # green
        self._render(f"{prefix} {marker} on {tag}: {text}")

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def _send_channel(self, text: str) -> None:
        ok, body = self._http_post_json(
            "/chat/send", {"text": text, "channel": self._channel},
        )
        if not ok:
            self._render(_color("31", f"send failed: {body}"))
            return
        # Body is "queued: true …" JSON on success — confirm to the operator
        # immediately. The polled TX entry arrives later with a real ts.
        self._render_tx_echo(
            channel=self._channel, destination=None, text=text,
        )

    def _send_dm(self, dest: str, text: str) -> None:
        ok, body = self._http_post_json(
            "/chat/send", {"text": text, "destination": dest},
        )
        if not ok:
            self._render(_color("31", f"DM send failed: {body}"))
            return
        self._render_tx_echo(channel=None, destination=dest, text=text)

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
                self._render(
                    f"current channel: {_channel_label(self._channel, self._channel_names)}"
                )
                return True
            try:
                self._channel = int(parts[1])
            except ValueError:
                self._render("usage: /ch <integer slot>")
                return True
            self._render(
                f"channel → {_channel_label(self._channel, self._channel_names)}"
            )
            return True

        if cmd == "/dm":
            if len(parts) < 3:
                self._render("usage: /dm <hex_dest> <text>")
                return True
            self._send_dm(parts[1], parts[2])
            return True

        if cmd == "/channels":
            self._render_channels_table()
            return True

        if cmd in ("/help", "/?"):
            self._render(
                "commands:  /ch <n>   /dm <dest> <text>   /channels   /quit"
            )
            return True

        self._render(f"unknown command: {cmd}  (try /help)")
        return True

    def _render_channels_table(self) -> None:
        """Cross-reference /radio (slot list with names) with /chat/channels
        (last-seen timestamps from the chat ring buffer)."""
        # Refresh first so renames / re-flashes show up.
        self._refresh_channel_names()
        seen = self._http_get("/chat/channels") or {}
        last_seen: Dict[int, float] = {}
        for c in seen.get("channels") or []:
            try:
                last_seen[int(c["channel"])] = float(c.get("last_seen", 0.0))
            except (KeyError, TypeError, ValueError):
                continue
        slots = sorted(self._channel_names) or sorted(last_seen)
        if not slots:
            self._render("(no channels — /radio empty and no traffic seen)")
            return
        lines = ["channels:"]
        for idx in slots:
            name = self._channel_names.get(idx, "")
            ts = last_seen.get(idx)
            seen_str = _format_ts(ts) if ts else "(never)"
            active = " ←" if idx == self._channel else ""
            lines.append(f"  ch{idx:<2} {name:<16} last_seen {seen_str}{active}")
        self._render("\n".join(lines))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> int:
        # Channel names first so the banner can show the active slot's
        # name. Best-effort: if /radio is unreachable we fall back to
        # bare ch{n} labels and the banner still works.
        self._refresh_channel_names()

        # Banner
        print(_color("1", "MeshAnchor MeshCore chat"))
        print(f"  api:     {self.base_url}")
        print(f"  channel: {_channel_label(self._channel, self._channel_names)}"
              "  (type to send, /ch <n> to switch)")
        if self._channel_names:
            slot_summary = ", ".join(
                f"{idx}={n}" for idx, n in sorted(self._channel_names.items())
            )
            print(f"  slots:   {slot_summary}")
        print(f"  detach:  Ctrl-b d  (tmux session 'meshcore-chat')")
        print("  commands: /ch <n>  /dm <hex> <text>  /channels  /help  /quit\n")

        # Initial prompt so the operator sees where they're typing even
        # before the first inbound message lands (the poll loop reprints
        # the prompt after every render, but the first render may be
        # minutes away on a quiet channel).
        sys.stdout.write(self._prompt())
        sys.stdout.flush()

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
    channel = _env_int("MESHANCHOR_CHAT_CHANNEL", 0)
    poll = _env_float(
        "MESHANCHOR_CHAT_POLL", POLL_INTERVAL_S_DEFAULT,
        minimum=POLL_INTERVAL_S_MIN,
    )
    if argv:
        for a in argv:
            if a.startswith("--api="):
                base = a.split("=", 1)[1]
            elif a.startswith("--channel="):
                try:
                    channel = int(a.split("=", 1)[1])
                except ValueError:
                    pass
            elif a.startswith("--poll="):
                try:
                    poll = max(float(a.split("=", 1)[1]), POLL_INTERVAL_S_MIN)
                except ValueError:
                    pass
    client = ChatClient(base_url=base, channel=channel, poll_interval=poll)
    return client.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
