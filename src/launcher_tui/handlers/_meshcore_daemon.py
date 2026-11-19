"""Daemon control flows for the MeshCore TUI handler.

Extracted from ``meshcore.py`` (2026-09-21, roadmap 1a) to keep that file
under the 1500-line cap and to give each pane its own module — the same
mixin pattern as ``_meshcore_radio_ops.py`` and the daemon-side
``gateway/meshcore_*_mixin.py`` family.

The methods here run on a ``MeshCoreHandler`` instance that already
provides ``self.ctx``.

``DAEMON_SERVICE`` travels with this mixin because these methods are its
only consumers. ``CHAT_API_BASE`` deliberately stays on the host class,
which has several (radio config, chat, ``meshcore_cli``,
``meshcore_positions``) — one constant, one home.
"""

from __future__ import annotations

from backend import clear_screen


class MeshCoreDaemonMixin:
    """Mixin: meshanchor-daemon.service status, lifecycle, journal."""

    # ─────────────────────────────────────────────────────────────────
    # Daemon control — meshanchor-daemon.service hosts the gateway
    # bridge, MeshCore handler, MQTT subscriber, config_api, etc.
    # Operators were dropping to a shell to manage it; bring the basics
    # into the TUI so the chat menu and the daemon controls live in
    # one place.
    # ─────────────────────────────────────────────────────────────────

    DAEMON_SERVICE = "meshanchor-daemon.service"

    def _meshcore_daemon_control(self):
        while True:
            unit = self._daemon_unit_state()
            status_line = (f"{self.DAEMON_SERVICE}: {unit}\n"
                           f"{self._observer_status_summary(unit_state=unit)}")
            choices = [
                ("status", "Service Status      systemctl is-active + show"),
                ("observer", "CS-1 Observer       witness (daemon_status.json)"),
                ("start", "Start               sudo systemctl start"),
                ("stop", "Stop                sudo systemctl stop (kills chat)"),
                ("restart", "Restart             sudo systemctl restart"),
                ("journal", "Journal (last 50)   journalctl -n 50"),
                ("tail", "Live Tail           journalctl -f (Ctrl-C to stop)"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "Daemon Control",
                status_line,
                choices,
            )
            if choice is None or choice == "back":
                return
            dispatch = {
                "status": self._daemon_show_status,
                "observer": self._daemon_show_observer,
                "start": self._daemon_start,
                "stop": self._daemon_stop,
                "restart": self._daemon_restart,
                "journal": self._daemon_journal_recent,
                "tail": self._daemon_journal_tail,
            }
            fn = dispatch.get(choice)
            if fn:
                fn()
            else:
                self.ctx.notify_unwired(choice, "MeshCoreHandler._meshcore_daemon_control")

    def _daemon_status_summary(self) -> str:
        """One-line status for the menu subtitle. is-active is fast and
        works without sudo, so we use it instead of a full status dump."""
        return f"{self.DAEMON_SERVICE}: {self._daemon_unit_state()}"

    def _daemon_unit_state(self) -> str:
        """The literal is-active word (also the observer line's unit fact)."""
        import subprocess
        try:
            # Raw is-active (allowlisted in TestServiceCheckContract): this
            # subtitle prints the literal systemctl state, keeping 'activating'/
            # 'reloading' granularity that check_service() collapses.
            result = subprocess.run(
                ["systemctl", "is-active", self.DAEMON_SERVICE],
                capture_output=True, text=True, timeout=5,
            )
            state = result.stdout.strip() or result.stderr.strip() or "unknown"
        except Exception as e:
            state = f"error: {e}"
        return state

    # CS-1 observer witness: the daemon exports observer_snapshot() into
    # daemon_status.json (gateway_bridge.meshcore_observer); this is its
    # one reader in the TUI. Degraded reads say UNKNOWN, never healthy.
    _OBSERVER_TAGS = {"ok": "OK", "off": "OFF", "bad": "FAULT", "unknown": "UNKNOWN"}

    def _observer_status_summary(self, unit_state: str = None) -> str:
        try:
            from gateway.meshcore_observer_status import observer_status_line
            tone, line = observer_status_line(unit_state=unit_state)
        except Exception as e:
            tone, line = "unknown", f"UNKNOWN — {type(e).__name__}: {e}"
        tag = self._OBSERVER_TAGS.get(tone, "UNKNOWN")
        # One line: daemon-supplied text (an exception message) may carry
        # newlines, which would push the whiptail menu (review r3).
        out = " ".join(f"CS-1 observer [{tag}]: {line}".split())
        try:
            from gateway.meshcore_observer_status import SUBTITLE_MAX as cap
        except Exception:
            cap = 76
        return out if len(out) <= cap else out[:cap - 1] + "…"

    def _daemon_show_observer(self):
        import json
        clear_screen()
        print("=== CS-1 MeshCore observer ===\n")
        unit = self._daemon_unit_state()
        try:
            from gateway.meshcore_observer_status import read_observer
            view = read_observer(unit_state=unit)
            tag = self._OBSERVER_TAGS.get(view.tone, "UNKNOWN")
            line, raw, why, ctx = view.line, view.raw, view.raw_reason, view.context
        except Exception as e:
            tag, line, raw, why, ctx = "UNKNOWN", f"UNKNOWN — {type(e).__name__}: {e}", \
                None, f"{type(e).__name__}: {e}", {}
        print(f"  CS-1 observer [{tag}]: {line}\n")
        # The facts the verdict was derived from — the same single read.
        age = ctx.get("file_age_s")
        print(f"  status file age : {'?' if age is None else f'{int(age)}s'}")
        print(f"  daemon status   : {ctx.get('daemon_status')!r}")
        print(f"  gateway bridge  : running={ctx.get('bridge_running')!r} "
              f"alive={ctx.get('bridge_alive')!r}")
        print(f"  systemd unit    : {ctx.get('unit_state', unit)!r}\n")
        if raw is None:
            print(f"  (no exported witness: {why})")
        else:
            print("  exported witness, as written (judge it by the facts above):")
            print(json.dumps(raw, indent=2, sort_keys=True, default=str))
        print("\n  Turn on: meshcore.observer_enabled = true in the gateway config,"
              "\n  then restart meshanchor-daemon. 'ok' = queued to the local broker,"
              "\n  not proof that CoreScope stored the packet.")
        self.ctx.wait_for_enter()

    def _daemon_show_status(self):
        import subprocess
        clear_screen()
        print(f"=== {self.DAEMON_SERVICE} status ===\n")
        try:
            result = subprocess.run(
                ["systemctl", "status", self.DAEMON_SERVICE,
                 "--no-pager", "--lines", "10"],
                capture_output=True, text=True, timeout=10,
            )
            # systemctl status returns nonzero when the service is inactive
            # but stdout still has useful info; print regardless.
            output = result.stdout.strip() or result.stderr.strip()
            print(output or "  (no output)")
        except FileNotFoundError:
            print("  systemctl not found (not a systemd box?)")
        except subprocess.TimeoutExpired:
            print("  systemctl timed out")
        except Exception as e:
            print(f"  Error: {e}")
        self.ctx.wait_for_enter()

    def _daemon_start(self):
        self._daemon_run_action("start", confirm=False)

    def _daemon_stop(self):
        # Stopping the daemon kills the chat API and the gateway bridge.
        # Don't surprise the operator with that.
        confirmed = self.ctx.dialog.yesno(
            "Stop Daemon",
            f"Stopping {self.DAEMON_SERVICE} kills the chat API, gateway "
            f"bridge, and node tracker until restarted. Continue?",
        )
        if not confirmed:
            return
        self._daemon_run_action("stop", confirm=False)

    def _daemon_restart(self):
        self._daemon_run_action("restart", confirm=False)

    def _daemon_run_action(self, action: str, confirm: bool = False):
        try:
            from utils.service_check import (
                start_service, stop_service, restart_service,
            )
        except ImportError as e:
            self.ctx.dialog.msgbox(
                "Daemon Control",
                f"service_check helpers unavailable: {e}",
            )
            return

        clear_screen()
        print(f"=== Daemon {action} ===\n")
        fn = {
            "start": start_service, "stop": stop_service,
            "restart": restart_service,
        }[action]
        try:
            ok, msg = fn(self.DAEMON_SERVICE)
        except Exception as e:
            ok, msg = False, str(e)
        marker = "OK" if ok else "FAILED"
        print(f"  [{marker}] {msg}")
        # Re-show is-active so the operator gets confirmation without
        # navigating back to the menu.
        print(f"\n  {self._daemon_status_summary()}")
        self.ctx.wait_for_enter()

    def _daemon_journal_recent(self):
        import subprocess
        clear_screen()
        print(f"=== {self.DAEMON_SERVICE} — last 50 ===\n")
        try:
            result = subprocess.run(
                ["journalctl", "-u", self.DAEMON_SERVICE,
                 "-n", "50", "--no-pager"],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stdout.strip()
            print(output or "  (no journal entries)")
        except FileNotFoundError:
            print("  journalctl not found (not a systemd box?)")
        except subprocess.TimeoutExpired:
            print("  journalctl timed out")
        except Exception as e:
            print(f"  Error: {e}")
        self.ctx.wait_for_enter()

    def _daemon_journal_tail(self):
        import subprocess
        clear_screen()
        print(f"=== {self.DAEMON_SERVICE} — live tail (Ctrl-C to stop) ===\n")
        try:
            # Stream until interrupted; subprocess.run inherits the
            # terminal so Ctrl-C reaches journalctl directly.
            subprocess.run(
                ["journalctl", "-u", self.DAEMON_SERVICE,
                 "-f", "-n", "20", "--no-pager"],
                timeout=None,
            )
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            print("  journalctl not found (not a systemd box?)")
            self.ctx.wait_for_enter()
        except Exception as e:
            print(f"  Error: {e}")
            self.ctx.wait_for_enter()
