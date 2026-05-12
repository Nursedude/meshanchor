"""Stack Health — local-stack visibility for "why is this not working?"

T0 of the cmd/diag/analyzer triad — port of MeshForge's FleetHealthHandler
(`feat(tui): FleetHealthHandler` commit 6812fd2). Same shape: each probe
answers a yes/no question in operator language with a status icon
(ok/warn/fail/info) and a single-line "why" hint. The goal is for
silence-mode failures (daemon up, no activity for days) to surface
without manual ssh-grep-dump investigation.

MeshAnchor differences from the MeshForge twin:
- Map DB lives at ``~/.local/share/meshanchor/`` (not ``meshforge``).
- The "bridge" probe checks ``meshanchor-daemon`` (MeshAnchor's main NOC
  daemon) instead of ``meshforge-gateway``. MeshAnchor is MeshCore-primary
  and doesn't have the Mesh↔RNS bridge as a separate service in the same
  shape — the daemon is the closest equivalent.

Scope is the local box only — cross-box fleet rollup is T1 work
(``FleetMonitorHandler`` in Batch 22 is the multi-host rollup view;
this handler is the "is my local stack stuck" view).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

STATUS_ICON = {
    "ok": "[ OK ]",
    "warn": "[WARN]",
    "fail": "[FAIL]",
    "info": "[ -- ]",
}

NOMADNET_QUIET_WARN_HOURS = 6
NOMADNET_QUIET_FAIL_HOURS = 24
WAL_WARN_MB = 50
WAL_FAIL_MB = 200
DB_WARN_GB = 2.0
DB_FAIL_GB = 5.0


@dataclass
class ProbeResult:
    """One row in the Stack Health screen."""

    label: str
    status: str  # "ok" | "warn" | "fail" | "info"
    headline: str  # one-line state
    hint: Optional[str] = None  # one-line "why this matters / what to do"


class FleetHealthHandler(BaseHandler):
    """Stack Health — local stack snapshot for "is anything stuck?"

    Sister handler to MeshForge's FleetHealthHandler. Same probe shape,
    MeshAnchor-specific service names and DB path.
    """

    handler_id = "fleet_health"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            (
                "datapath",
                "Stack Health        Local: RNS path, NomadNet, daemon, DB",
                None,
            ),
        ]

    def execute(self, action):
        if action == "datapath":
            self.ctx.safe_call("Stack Health", self._render_overview)

    def _render_overview(self):
        from backend import clear_screen

        clear_screen()
        print("Stack Health — local stack snapshot")
        print(f"Host: {self._hostname()}    {self._now_utc()}")
        print("=" * 72)

        probes: List[Callable[[], ProbeResult]] = [
            self._probe_rnsd,
            self._probe_rns_path_table,
            self._probe_rns_hub_peers,
            self._probe_nomadnet,
            self._probe_lxmf_queue,
            self._probe_meshanchor_daemon,
            self._probe_peer_gateways,
            self._probe_map_db,
            self._probe_meshtasticd_radio,
        ]

        for fn in probes:
            try:
                result = fn()
            except Exception as exc:  # one bad probe must not break the screen
                logger.warning("probe %s raised: %s", fn.__name__, exc)
                result = ProbeResult(
                    label=fn.__name__.replace("_probe_", "").replace("_", " "),
                    status="info",
                    headline=f"probe error: {type(exc).__name__}",
                )
            self._print_row(result)

        print("=" * 72)
        print("Status legend: [ OK ] healthy  [WARN] worth a look  "
              "[FAIL] action recommended  [ -- ] not applicable")
        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()

    # ------------------------------------------------------------------ probes

    def _probe_rnsd(self) -> ProbeResult:
        from utils.service_check import check_service

        state = check_service("rnsd")
        if not state.available:
            return ProbeResult(
                label="rnsd",
                status="fail",
                headline="rnsd is not running",
                hint="No RNS transport — nothing routes. "
                     "Start with: sudo systemctl start rnsd",
            )
        uptime_s = self._service_uptime_seconds("rnsd")
        uptime_label = self._humanize_duration(uptime_s) if uptime_s else "uptime unknown"
        return ProbeResult(
            label="rnsd",
            status="ok",
            headline=f"active, {uptime_label}",
        )

    def _probe_rns_path_table(self) -> ProbeResult:
        rnpath = shutil.which("rnpath")
        if not rnpath:
            return ProbeResult(
                label="RNS path table",
                status="info",
                headline="rnpath command not installed",
            )
        out = self._run([rnpath, "--config", "/etc/reticulum", "-t"], timeout=10)
        if out is None:
            return ProbeResult(
                label="RNS path table",
                status="warn",
                headline="rnpath query timed out",
                hint="rnsd may be unresponsive to RPC — check 'systemctl status rnsd'",
            )
        lines = [ln for ln in out.splitlines() if " is " in ln and " away via " in ln]
        if not lines:
            return ProbeResult(
                label="RNS path table",
                status="warn",
                headline="path table is empty",
                hint="No destinations learned yet — wait for announces or check interfaces",
            )
        # "LocalInterface" rows are shared-instance IPC peers (other
        # local processes that connected to rnsd), not network destinations.
        ipc = sum(1 for ln in lines if "LocalInterface" in ln)
        network = len(lines) - ipc
        return ProbeResult(
            label="RNS path table",
            status="ok",
            headline=f"{network} network destinations, {ipc} local IPC peers",
        )

    def _probe_rns_hub_peers(self) -> ProbeResult:
        # `ss -tn` keeps the state column so the column indices below match
        # both the live output and the test fixtures.
        out = self._run(["ss", "-tn"], timeout=5)
        if out is None:
            return ProbeResult(
                label="RNS hub peers",
                status="info",
                headline="ss query failed",
            )
        inbound = []  # connections TO local :4242 (we are the hub)
        outbound = []  # connections FROM local TO remote :4242 (we are a client)
        for ln in out.splitlines():
            if ":4242" not in ln:
                continue
            cols = ln.split()
            if len(cols) < 5:
                continue
            local_ep = cols[3]
            peer_ep = cols[4]
            if local_ep.endswith(":4242"):
                inbound.append(peer_ep)
            elif peer_ep.endswith(":4242"):
                outbound.append(peer_ep)
        if not inbound and not outbound:
            return ProbeResult(
                label="RNS hub peers",
                status="info",
                headline="no :4242 TCP sessions",
                hint="Either an auto-only RNS box, or no RNS peers configured",
            )
        parts = []
        if inbound:
            parts.append(f"{len(inbound)} inbound (we are hub)")
        if outbound:
            parts.append(f"{len(outbound)} outbound to {self._first_host(outbound[0])}")
        return ProbeResult(
            label="RNS hub peers",
            status="ok",
            headline=", ".join(parts),
        )

    def _probe_nomadnet(self) -> ProbeResult:
        from utils.paths import get_real_user_home

        home = get_real_user_home()
        logfile = home / ".nomadnetwork" / "logfile"
        if not logfile.exists():
            return ProbeResult(
                label="NomadNet",
                status="info",
                headline="not installed on this box",
            )
        try:
            mtime = logfile.stat().st_mtime
        except OSError as exc:
            return ProbeResult(
                label="NomadNet",
                status="warn",
                headline=f"logfile unreadable: {exc}",
            )
        age_s = time.time() - mtime
        age_label = self._humanize_duration(age_s)
        running = self._pgrep_count("nomadnet") > 0
        if not running:
            return ProbeResult(
                label="NomadNet",
                status="fail",
                headline=f"daemon NOT running (logfile last touched {age_label} ago)",
                hint="Texts will not send or receive — restart NomadNet",
            )
        if age_s > NOMADNET_QUIET_FAIL_HOURS * 3600:
            return ProbeResult(
                label="NomadNet",
                status="fail",
                headline=f"daemon running but QUIET for {age_label}",
                hint="If you're texting and seeing delay, this is likely why — "
                     "check propagation config or restart NomadNet",
            )
        if age_s > NOMADNET_QUIET_WARN_HOURS * 3600:
            return ProbeResult(
                label="NomadNet",
                status="warn",
                headline=f"running, last activity {age_label} ago",
                hint="Idle but not dead. OK if you haven't been using it.",
            )
        return ProbeResult(
            label="NomadNet",
            status="ok",
            headline=f"running, last activity {age_label} ago",
        )

    def _probe_lxmf_queue(self) -> ProbeResult:
        from utils.paths import get_real_user_home

        home = get_real_user_home()
        outdir = home / ".nomadnetwork" / "storage" / "messages" / "outbound"
        if not outdir.is_dir():
            return ProbeResult(
                label="LXMF outbound queue",
                status="info",
                headline="no NomadNet storage on this box",
            )
        try:
            pending = sum(1 for _ in outdir.iterdir())
        except OSError as exc:
            return ProbeResult(
                label="LXMF outbound queue",
                status="warn",
                headline=f"outbound dir unreadable: {exc}",
            )
        if pending == 0:
            return ProbeResult(
                label="LXMF outbound queue",
                status="ok",
                headline="empty (nothing waiting)",
            )
        if pending > 10:
            return ProbeResult(
                label="LXMF outbound queue",
                status="fail",
                headline=f"{pending} messages stuck pending",
                hint="Destinations may be unreachable, or propagation node is down",
            )
        return ProbeResult(
            label="LXMF outbound queue",
            status="warn",
            headline=f"{pending} pending (in flight)",
            hint="Normal mid-send; check again in a minute",
        )

    def _probe_meshanchor_daemon(self) -> ProbeResult:
        from utils.service_check import check_service

        state = check_service("meshanchor-daemon")
        if not state.available:
            return ProbeResult(
                label="MeshAnchor daemon",
                status="info",
                headline="meshanchor-daemon not running on this box",
            )
        uptime_s = self._service_uptime_seconds("meshanchor-daemon")
        uptime_label = self._humanize_duration(uptime_s) if uptime_s else "uptime unknown"
        # Pull recent log activity — quiet daemon is a silence-mode failure.
        out = self._run(
            ["journalctl", "-u", "meshanchor-daemon",
             "--since", "10 minutes ago", "--no-pager"],
            timeout=10,
        )
        if out is not None and not out.strip():
            return ProbeResult(
                label="MeshAnchor daemon",
                status="warn",
                headline=f"active, {uptime_label}, no recent journal output",
                hint="Daemon silent for 10+ min — check 'systemctl status meshanchor-daemon'",
            )
        return ProbeResult(
            label="MeshAnchor daemon",
            status="ok",
            headline=f"active, {uptime_label}",
        )

    def _probe_peer_gateways(self) -> ProbeResult:
        """Surface peer-gateway visibility across the fleet.

        Answers the load-bearing question: "does THIS box see the OTHER
        gateways yet?" That's the precondition for any cross-stack
        bridge traffic (MeshCore↔MA↔RNS↔MF↔Meshtastic).

        Reads the daemon journal for TWO signals (whichever is producing
        data), since heartbeat MQTT is feature-flagged off by default
        and the load-bearing peer-discovery in production is RNS
        announce reception:

        1. ``node_tracker`` log lines: ``Discovered RNS node: <hash>
           (<name>) [LXMF_DELIVERY]`` — filtered to names matching
           gateway patterns (``Gateway``, ``Broadcast``). Fires
           whenever the gateway hears a peer's LXMF announce; this is
           the path that actually carries bridge traffic.
        2. ``gateway_heartbeat`` log lines: ``Discovered peer gateway:
           <id>`` + DOWN/RECOVERED transitions. Only fires when the
           heartbeat MQTT feature is enabled
           (``gateway_heartbeat_enabled=True`` in gateway config).

        If both signals are silent, the gateway is isolated — that's
        the diagnostic surface the operator wants.
        """
        from utils.service_check import check_service

        # If the daemon's not up, there's nothing to read from.
        state = check_service("meshanchor-daemon")
        if not state.available:
            return ProbeResult(
                label="Peer gateways",
                status="info",
                headline="not applicable (meshanchor-daemon not running)",
            )

        out = self._run(
            ["journalctl", "-u", "meshanchor-daemon",
             "--since", "1 hour ago", "--no-pager"],
            timeout=15,
        )
        if not out:
            return ProbeResult(
                label="Peer gateways",
                status="warn",
                headline="no daemon journal output in last hour",
                hint="Daemon may have gone silent — check 'journalctl -u "
                     "meshanchor-daemon --since \"1 hour ago\"'",
            )

        peers_seen: dict = {}  # name -> latest log line containing it
        peers_down: set = set()  # MQTT-heartbeat peer IDs marked DOWN

        for ln in out.splitlines():
            # Signal 1: node_tracker RNS announces — load-bearing path.
            # "Discovered RNS node: <hash> (<name>) [LXMF_DELIVERY]"
            idx = ln.find("Discovered RNS node: ")
            if idx >= 0:
                tail = ln[idx + len("Discovered RNS node: "):]
                # Extract name from the parenthesized portion.
                lp = tail.find("(")
                rp = tail.find(")", lp + 1) if lp >= 0 else -1
                if lp >= 0 and rp > lp:
                    name = tail[lp + 1:rp].strip()
                    # Filter to peer-gateway-style names. Liberal match
                    # so we catch "MeshForge Gateway (moc3)", "MeshAnchor
                    # Broadcast", "MeshAnchor Gateway (xxx)", etc.
                    if any(k in name for k in ("Gateway", "Broadcast")):
                        peers_seen[name] = ln
                continue

            # Signal 2: gateway_heartbeat MQTT (only if enabled).
            for marker in ("Discovered peer gateway:",
                           "GATEWAY HEARTBEAT: peer "):
                hidx = ln.find(marker)
                if hidx < 0:
                    continue
                tail = ln[hidx + len(marker):].strip()
                peer_id = tail.split()[0].rstrip(":,")
                peers_seen[peer_id] = ln
                if "is DOWN" in ln:
                    peers_down.add(peer_id)
                elif "RECOVERED" in ln or "Discovered peer gateway" in ln:
                    peers_down.discard(peer_id)
                break

        if not peers_seen:
            return ProbeResult(
                label="Peer gateways",
                status="warn",
                headline="no peer-gateway log entries in last hour",
                hint="This gateway is running but isolated — peers may not be "
                     "announcing on RNS (path table?) or heartbeat is off",
            )

        live = [p for p in peers_seen if p not in peers_down]
        if not live:
            return ProbeResult(
                label="Peer gateways",
                status="fail",
                headline=f"{len(peers_seen)} peer(s) known, all marked DOWN",
                hint="Heartbeat broker may be unreachable or all peers offline",
            )

        # Build a short summary. Truncate long identifiers for the
        # one-line headline; full list goes to the hint.
        def _short(pid: str) -> str:
            return pid[:24] + "…" if len(pid) > 25 else pid

        live_short = ", ".join(_short(p) for p in sorted(live)[:3])
        more = "" if len(live) <= 3 else f" (+{len(live) - 3} more)"

        down_note = ""
        if peers_down:
            down_note = f", {len(peers_down)} DOWN"

        return ProbeResult(
            label="Peer gateways",
            status="ok",
            headline=f"{len(live)} live{down_note} — {live_short}{more}",
        )

    def _probe_map_db(self) -> ProbeResult:
        from utils.paths import get_real_user_home

        home = get_real_user_home()
        db = home / ".local" / "share" / "meshanchor" / "node_history.db"
        if not db.exists():
            return ProbeResult(
                label="Map DB (node_history)",
                status="info",
                headline="no node_history.db on this box",
            )
        size_b = db.stat().st_size
        size_gb = size_b / (1024**3)
        wal = db.with_name(db.name + "-wal")
        wal_mb = (wal.stat().st_size / (1024**2)) if wal.exists() else 0
        status = "ok"
        hint = None
        bits = [f"db {size_gb:.1f} GB", f"wal {wal_mb:.0f} MB"]
        if size_gb >= DB_FAIL_GB:
            status = "fail"
            hint = "Backlog draining — VACUUM after convergence to reclaim disk"
        elif size_gb >= DB_WARN_GB:
            status = "warn"
            hint = "DB is on the heavy side — watch for prune cap-hits in journal"
        if wal_mb >= WAL_FAIL_MB:
            status = "fail" if status != "fail" else status
            hint = (hint or "") + (" | " if hint else "") + \
                "WAL is large — checkpoint stalled or prune mid-cycle"
        elif wal_mb >= WAL_WARN_MB:
            if status == "ok":
                status = "warn"
                hint = "WAL accumulating between checkpoint cycles"
        return ProbeResult(
            label="Map DB (node_history)",
            status=status,
            headline=", ".join(bits),
            hint=hint,
        )

    def _probe_meshtasticd_radio(self) -> ProbeResult:
        from utils.service_check import check_service

        state = check_service("meshtasticd")
        if not state.available:
            return ProbeResult(
                label="Local mesh radio",
                status="info",
                headline="meshtasticd not running on this box",
            )
        uptime_s = self._service_uptime_seconds("meshtasticd")
        uptime_label = self._humanize_duration(uptime_s) if uptime_s else "uptime unknown"
        return ProbeResult(
            label="Local mesh radio",
            status="ok",
            headline=f"meshtasticd active, {uptime_label}",
        )

    # --------------------------------------------------------------- rendering

    def _print_row(self, r: ProbeResult) -> None:
        icon = STATUS_ICON.get(r.status, "[ ?? ]")
        print(f"{icon}  {r.label:<24}  {r.headline}")
        if r.hint:
            print(f"        ↳ {r.hint}")

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _hostname() -> str:
        try:
            return Path("/etc/hostname").read_text().strip() or "(unknown)"
        except OSError:
            return "(unknown)"

    @staticmethod
    def _now_utc() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _run(args, timeout: int = 10) -> Optional[str]:
        """Run a command and return stdout, or None on error/timeout."""
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return proc.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("run failed: %s -> %s", args, exc)
            return None

    @staticmethod
    def _pgrep_count(pattern: str) -> int:
        try:
            proc = subprocess.run(
                ["pgrep", "-fc", pattern],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return int((proc.stdout or "0").strip() or "0")
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            return 0

    @classmethod
    def _service_uptime_seconds(cls, unit: str) -> Optional[float]:
        out = cls._run(
            ["systemctl", "show", unit, "-p", "ActiveEnterTimestamp"],
            timeout=5,
        )
        if not out:
            return None
        _, _, ts = out.strip().partition("=")
        if not ts:
            return None
        out2 = cls._run(["date", "-d", ts, "+%s"], timeout=3)
        if not out2:
            return None
        try:
            return max(0.0, time.time() - int(out2.strip()))
        except ValueError:
            return None

    @staticmethod
    def _humanize_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        if seconds < 3600:
            return f"{int(seconds / 60)} min"
        if seconds < 86400:
            return f"{seconds / 3600:.1f} hr"
        return f"{seconds / 86400:.1f} days"

    @staticmethod
    def _first_host(endpoint: str) -> str:
        return endpoint.rsplit(":", 1)[0]
