"""Restart rnsd in the order that cannot hand ``@rns/<instance>`` to a client.

Issue #69 repair order: **stop RNS clients → restart rnsd → verify rnsd OWNS
the listener → start clients.** Restarting rnsd with its clients still up
opens a window in which a client (NomadNet, lxmd, meshchat, a gateway that
outlived its wait) finds the shared-instance socket absent and hosts it
itself; rnsd then comes back as a client of the squatter, or dies on the
bind. If rnsd never takes the socket, the clients are LEFT STOPPED and
named: starting them is exactly what makes the squat.

Only units that are ACTIVE when the hold begins are touched, so a COMPLETED
deliberate stop (inactive/failed) stays a stop
(feedback_deploy_restarts_only_active_units). ``activating`` and
``deactivating`` count as active: both will be running again with nobody
starting them (a crashed unit under ``Restart=`` sits in ``deactivating``
for its whole TimeoutStopSec), so they are held and RESTARTED by release —
a stop still in flight when the hold begins is therefore undone; that window
is seconds and the alternative is a squatter (MF re-review R1/F3, 2026-09-29).

Ownership is read from the socket (``ss -xnpl`` → ``/proc/<pid>/cmdline``,
parsed by the byte-locked ``rns_init`` helpers, which handle spaced instance
names) — never from "the port answers", which a squatter satisfies too.

Twin of MeshForge ``src/utils/rnsd_restart_order.py`` (MF ``71705049``).
Differences are MA-shaped only: MeshAnchor has no ``watchdog_probes_rns``,
so the owner scan is local; ``check_service`` is system-scope only here, so
user units are read via ``is_user_unit_active`` (tri-state).
"""

import logging
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from utils.observation import Failed, Observation, Seen, Unobservable, assert_never
from utils.service_check import (_operator_user_prefix, is_system_unit_active,
                                 is_user_unit_active, start_service,
                                 stop_service)

logger = logging.getLogger(__name__)


def _systemctl_query_argv(args: List[str], user: bool = False) -> List[str]:
    """MA-shaped twin of MeshForge service_check._systemctl_query_argv: a
    user-scope call lands on the OPERATOR's manager under sudo."""
    return ((_operator_user_prefix() + ['systemctl', '--user']) if user
            else ['systemctl']) + list(args)

# (unit, user_scope). The FLOOR of the hold set, not the whole of it.
# The kernel cannot map a client to the socket (``ss -xnp`` shows peers as
# ``* 0``), so this list was curated — and on 2026-10-02 a fleet measurement
# showed it MISSING real clients: `lxmd` runs as a SYSTEM unit on one box (the
# list knew only a user one), `meshforge-lxmd` as a USER unit on another, and
# `meshcore-chat` on the MeshAnchor box. A TUI rnsd repair there would have
# left an lxmd up through the restart — the #69 window. The cure is not a
# longer list: hold_rns_clients() now MEASURES which live units load RNS
# (measure_rns_clients) and holds the union; this floor remains for when the
# measurement cannot run. Inactive/absent units are skipped.
RNS_CLIENT_UNITS: Tuple[Tuple[str, bool], ...] = (
    ("meshforge-gateway", False),
    ("meshforge-map", False),
    ("meshforge-daemon", False),
    ("meshanchor-daemon", False),
    ("meshanchor-map", False),
    ("nomadnet", True),
    ("meshforge-echo", True),
    ("lxmd", True),
    ("lxmd", False),
    ("meshforge-lxmd", True),
    ("meshcore-chat", True),
    ("meshanchor-echo", True),
    ("meshchatx", True),
)

#: The per-process substrate probe honest_status uses (it asks each live
#: python process's own interpreter where `import RNS` resolves). One
#: measurement, two consumers — never a second implementation (hfm #5).
SUBSTRATE_PROBE = (Path(__file__).resolve().parent.parent.parent
                   / "scripts" / "hs_substrate_skew.py")
MEASURE_TIMEOUT_S = 90

#: The per-process substrate probe honest_status uses (it asks each live
#: python process's own interpreter where `import RNS` resolves). One
#: measurement, two consumers — never a second implementation (hfm #5).
SUBSTRATE_PROBE = (Path(__file__).resolve().parent.parent.parent
                   / "scripts" / "hs_substrate_skew.py")
MEASURE_TIMEOUT_S = 90

DEFAULT_OWNERSHIP_WAIT_S = 30.0


def _scope(user: bool) -> str:
    return "user" if user else "system"


# Re-review F1 (2026-09-29, drilled on a throwaway unit): ``stop_service()``'s
# 30 s default is SHORTER than every RNS client's TimeoutStopSec (90 s on the
# fleet; the map 5 min), so a held unit that is slow to stop — which a
# ``deactivating`` one is BY DEFINITION — timed out the systemctl CLIENT while
# the stop JOB stayed queued in systemd and landed anyway: the unit ended
# ``failed``, sat in ``stop_failed``, and release (which starts only
# ``stopped``) never brought it back. Left down until a human noticed. Cure:
# wait as long as the unit itself may take (+ margin, capped), and when the
# client still gives up, RE-ASK THE UNIT — the message is not the state.
STOP_TIMEOUT_MARGIN_S = 10
STOP_TIMEOUT_CAP_S = 330         # the map's 5 min + margin; ``infinity`` lands here

_TIMESPAN_UNITS = {"y": 31557600.0, "month": 2629800.0, "w": 604800.0, "d": 86400.0,
                   "h": 3600.0, "min": 60.0, "s": 1.0, "ms": 0.001, "us": 1e-6,
                   "\u00b5s": 1e-6, "ns": 1e-9}


def parse_systemd_timespan(text: str) -> Optional[float]:
    """``systemctl show`` prints USec properties as ``1min 30s`` / ``5min`` /
    ``infinity``. Seconds; ``inf`` for infinity; None when unparseable."""
    t = (text or "").strip()
    if not t:
        return None
    if t == "infinity":
        return float("inf")
    parts = re.findall(r"(\d+(?:\.\d+)?)\s*([a-z\u00b5]+)", t)
    if not parts or not re.fullmatch(r"(?:\s*\d+(?:\.\d+)?\s*[a-z\u00b5]+)+\s*", t):
        return None
    total = 0.0
    for num, unit in parts:
        if unit not in _TIMESPAN_UNITS:
            return None
        total += float(num) * _TIMESPAN_UNITS[unit]
    return total


def unit_stop_timeout_s(unit: str, user: bool) -> Optional[float]:
    """The unit's own ``TimeoutStopSec`` (read-only ``systemctl show``);
    ``inf`` for infinity; None when the manager did not answer."""
    try:
        r = subprocess.run(
            (_operator_user_prefix() + ['systemctl', '--user'] if user else ['systemctl'])
            + ['show', unit, '-p', 'TimeoutStopUSec', '--value'],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("TimeoutStopUSec of %s unreadable: %s", unit, e)
        return None
    return parse_systemd_timespan(r.stdout) if r.returncode == 0 else None


def stop_deadline_s(unit: str, user: bool) -> int:
    """How long ``stop_service`` may block for this unit: its own stop timeout
    plus a margin, capped; the CAP when unreadable (F-B: too long costs
    seconds, too short costs a downed unit — the map's is 5 min)."""
    t = unit_stop_timeout_s(unit, user)
    if t is None:
        return STOP_TIMEOUT_CAP_S
    return int(min(t + STOP_TIMEOUT_MARGIN_S, STOP_TIMEOUT_CAP_S))


def show_unit(unit: str, user: bool = False) -> Dict[str, str]:
    """ActiveState / SubState / MainPID / NRestarts (read-only ``systemctl
    show``), ``{}`` when unobservable. ONE definition: the ordered restart and
    ``scripts/fork_roll_restart.py`` / ``rnsd_ordered_restart.py`` read it."""
    try:
        r = subprocess.run(_systemctl_query_argv(
            ["show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
             "-p", "NRestarts"], user=user), capture_output=True, text=True, timeout=10)
        return dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
    except (subprocess.SubprocessError, OSError):
        return {}


def _client_active(unit: str, user: bool) -> Optional[bool]:
    """Tri-state state of one client unit; None = unobservable (never skip)."""
    try:
        return is_user_unit_active(unit) if user else is_system_unit_active(unit)
    except Exception as e:
        logger.debug("client state check %s failed: %s", unit, e)
        return None


@dataclass
class ClientHold:
    """Clients this restart stopped, so exactly those are started again."""
    stopped: List[Tuple[str, bool]] = field(default_factory=list)
    stop_failed: List[Tuple[str, bool, str]] = field(default_factory=list)
    unobservable: List[Tuple[str, bool]] = field(default_factory=list)
    #: units the substrate probe could not look inside (unit, user, why):
    #: NOT stopped (an unprobeable root process may be anything — stopping
    #: unattended-upgrades mid-run is its own harm), but named to the operator.
    unprobed: List[Tuple[str, bool, str]] = field(default_factory=list)
    #: repo code that imports RNS but is not proven a client: named, NOT stopped.
    repo_only: List[Tuple[str, bool]] = field(default_factory=list)
    #: units that could import RNS but showed no evidence either way.
    unjudged: int = 0
    #: transient / oneshot units never considered (review D re-read: a one-off
    #: `python3 -m RNS.Utilities.rnpath` in a systemd-run unit IS a client and
    #: would stay up unmentioned). Never-hold system units are not listed.
    not_considered: List[str] = field(default_factory=list)
    #: where the hold set came from — "measured + floor" or "floor only: why".
    client_set: str = ""

    def names(self) -> List[str]:
        return [f"{u} ({_scope(user)})" for u, user in self.stopped]

    def report_lines(self) -> List[str]:
        """The ONE rendering of a hold for every TUI caller (three sites used
        to hand-format it; the fourth fact would have drifted)."""
        out = [f"  RNS client set: {self.client_set}"] if self.client_set else []
        out += [f"  Stopped RNS client: {label}" for label in self.names()]
        out += [f"  Warning: could NOT stop RNS client {u}: {m} — it may squat @rns"
                for u, _user, m in self.stop_failed]
        out += [f"  Warning: state of RNS client {u} UNKNOWN — "
                f"{'user' if user else 'system'} manager did not answer "
                f"(timeout or bus unreachable); not stopped"
                for u, user in self.unobservable]
        out += [f"  Note: {u} ({_scope(user)}) runs repo code that imports RNS but "
                f"is not proven a client — NOT stopped; if it holds @rns it may squat"
                for u, user in self.repo_only]
        # Privilege-shaped blindness is one fact, not 36 lines (review D7).
        priv = [x for x in self.unprobed
                if x[2] == "no-access" or x[2].startswith("uid-")]
        named = [x for x in self.unprobed if x not in priv]
        out += [f"  Warning: could not see inside {u} ({_scope(user)}): {why} — "
                f"not stopped; if it uses RNS it may squat @rns"
                for u, user, why in named]
        if priv:
            out.append(f"  Note: {len(priv)} unit(s) not inspectable without root "
                       f"— not stopped (run the repair with sudo to measure them)")
        if self.not_considered:
            out.append(f"  Note: {len(self.not_considered)} transient/oneshot unit(s) "
                       f"not considered and not stopped: "
                       + ", ".join(self.not_considered[:6])
                       + (" ..." if len(self.not_considered) > 6 else ""))
        if self.unjudged:
            out.append(f"  Note: {self.unjudged} unit row(s) could import RNS but "
                       f"showed no evidence of loading it — not stopped")
        return out


#: Units a hold must NEVER stop, whatever the measurement says. A hold leaves
#: units STOPPED when rnsd fails to take the socket, so a false positive here
#: is an outage: review D (2026-10-02) measured ssh.service classed as an RNS
#: client (an `ssh box 'cd /opt/meshforge && python3 ...'` under sshd) — on a
#: tunnel-only box that is a lock-out. Prefix match on the unit name.
NEVER_HOLD_PREFIXES = ("ssh", "sshd", "cron", "crond", "atd", "dbus", "systemd-",
                       "user@", "getty@", "serial-getty@", "polkit", "run-",
                       "session-")


@dataclass
class MeasuredClients:
    """What one measurement saw. Only ``clients`` is ever stopped."""
    clients: List[Tuple[str, bool]] = field(default_factory=list)
    #: repo code that imports RNS, but the unit's own entry point does not
    #: declare it — NOT proven a client: named, never stopped (review D).
    repo_only: List[Tuple[str, bool]] = field(default_factory=list)
    unprobed: List[Tuple[str, bool, str]] = field(default_factory=list)
    unjudged: int = 0            # SX rows: could import RNS, no evidence
    skipped: List[str] = field(default_factory=list)   # never-hold / transient / oneshot
    user_scope_seen: bool = True


def _active_units() -> Tuple[List[Tuple[bool, Dict[str, str]]], bool]:
    """Every ACTIVE service, both scopes, with the facts a hold decision needs.
    -> (units, user_scope_seen). A user manager that does not answer (root
    without SUDO_USER) is reported, never read as "no user units"."""
    units: List[Tuple[bool, Dict[str, str]]] = []
    user_seen = True
    for user in (False, True):
        listed = subprocess.run(
            _systemctl_query_argv(['list-units', '--type=service', '--state=active',
                                   '--no-legend', '--no-pager'], user=user),
            capture_output=True, text=True, timeout=10)
        names = [ln.split()[0] for ln in listed.stdout.splitlines() if ln.split()]
        names = [u for u in names if u.endswith(".service")]
        if listed.returncode != 0 or not names:
            if user:
                user_seen = False
            continue
        shown = subprocess.run(
            _systemctl_query_argv(['show', '-p', 'Id', '-p', 'MainPID', '-p',
                                   'ControlGroup', '-p', 'Type', '-p', 'FragmentPath',
                                   *names], user=user),
            capture_output=True, text=True, timeout=20)
        for rec in shown.stdout.split("\n\n"):
            kv = dict(ln.split("=", 1) for ln in rec.splitlines() if "=" in ln)
            if kv.get("Id"):
                units.append((user, kv))
    return units, user_seen


def _cgroup_pids(cgroup: str) -> List[int]:
    """Processes in the unit's OWN cgroup (v2). Not its descendants: a
    session or a tmux scope forked from it lives in another cgroup."""
    if not cgroup:
        return []
    try:
        with open(f"/sys/fs/cgroup{cgroup}/cgroup.procs") as fh:
            return [int(x) for x in fh.read().split() if x.isdigit()]
    except OSError:
        return []


def measure_rns_clients(probe: Path = SUBSTRATE_PROBE) -> Observation[MeasuredClients]:
    """Which ACTIVE units' OWN processes load RNS right now.

    The probe (honest_status's substrate probe, ``--no-descend``) is fed every
    pid in each candidate unit's own cgroup. A unit becomes a hold target only
    on ``entry`` evidence (its process's entry point needs RNS) and only if it
    is not on NEVER_HOLD_PREFIXES, not transient (/run) and not oneshot.
    rnsd is excluded. A missing or failing probe is Unobservable/Failed —
    never an empty client set.
    """
    if not probe.is_file():
        return Unobservable(f"substrate probe not present at {probe}")
    try:
        units, user_seen = _active_units()
    except (subprocess.SubprocessError, OSError) as e:
        return Unobservable(f"could not enumerate active units: {e}")
    if not units:
        return Unobservable("no active units enumerated (manager unreachable?)")
    out = MeasuredClients(user_scope_seen=user_seen)
    lines: List[str] = []
    scope_of: Dict[str, bool] = {}
    for is_user, u in units:
        unit = u["Id"]
        if unit == "rnsd.service":
            continue
        if (unit.startswith(NEVER_HOLD_PREFIXES)
                or u.get("FragmentPath", "").startswith("/run/")
                or u.get("Type") == "oneshot"):
            out.skipped.append(unit)
            continue
        pids = _cgroup_pids(u.get("ControlGroup", ""))
        if not pids and u.get("MainPID", "0") not in ("", "0"):
            pids = [int(u["MainPID"])]
        scope_of[unit] = is_user
        lines += [f"{_scope(is_user)} {unit} {p}" for p in pids]
    if not lines:
        return Unobservable("no candidate unit had a readable process")
    try:
        r = subprocess.run(["python3", str(probe), "--no-descend", "rns"],
                           input="\n".join(lines) + "\n",
                           capture_output=True, text=True,
                           timeout=MEASURE_TIMEOUT_S)
    except (subprocess.SubprocessError, OSError) as e:
        return Failed(f"substrate probe did not complete: {e}")
    if r.returncode != 0:
        return Failed(f"substrate probe rc={r.returncode}")
    for ln in r.stdout.splitlines():
        f = ln.split()
        if len(f) < 3:
            continue
        unit = f[1]
        if unit == "*":
            return Failed(f"substrate probe: {' '.join(f[2:])}")
        if unit not in scope_of:
            continue
        key = (unit[:-len(".service")] if unit.endswith(".service") else unit,
               scope_of[unit])
        if f[0] in ("SB", "SC"):
            bucket = out.clients if f[-1] == "entry" else out.repo_only
            if key not in bucket:
                bucket.append(key)
        elif f[0] == "SU":
            if (key[0], key[1], f[2]) not in out.unprobed:
                out.unprobed.append((key[0], key[1], f[2]))
        elif f[0] == "SX":
            out.unjudged += 1
    out.repo_only = [k for k in out.repo_only if k not in out.clients]
    return Seen(out)


#: The real measurement, reachable by its own tests: tests/conftest.py pins
#: ``measure_rns_clients`` OFF for the whole suite, because an unpinned call
#: probes the test host's LIVE processes (a verdict that depends on where the
#: suite runs pins nothing — feedback_tests_must_pin_ambient_state).
_measure_rns_clients_impl = measure_rns_clients


class RestartJudgement(NamedTuple):
    """judge_rnsd_restart's answer — ONE value, so the state and its text
    cannot drift apart (review 2026-10-28, R4: two hand-synced fields).
    ``state``: "ok" | "failed" | "unverified" | "not_judged" (no baseline)."""
    state: str
    why: str = ""
    trace: str = ""   # "MainPID a -> b -> c, NRestarts x -> y" when observed


#: ordered_restart_rnsd's ``started`` was False: start_service's client wait
#: (30 s) can expire inside rnsd's ExecStartPre while the start job carries on.
#: The release MEASURES the restart, so callers report this and follow the
#: release verdict (review of 2cf548ae, SHOULD 2).
START_NOT_CONFIRMED = ("rnsd start command did not confirm success (a client timeout "
                       "inside ExecStartPre, or a refused start; see the log) — the "
                       "verdict is the MEASURED restart. ")

NOT_JUDGED = RestartJudgement("not_judged", "rnsd restart NOT JUDGED: no pre-restart "
                              "baseline was passed (rnsd_before).")


@dataclass
class ReleaseResult:
    rnsd_owns: Optional[bool]          # None = ownership unobservable
    started: List[str] = field(default_factory=list)
    start_failed: List[str] = field(default_factory=list)
    left_stopped: List[str] = field(default_factory=list)
    owners: List[Tuple[int, str]] = field(default_factory=list)
    not_stopped: List[str] = field(default_factory=list)  # F2: hold.stop_failed
    #: did rnsd itself restart — MEASURED by judge_rnsd_restart inside
    #: release_rns_clients. Ownership alone cannot say: the OLD rnsd owns too.
    restart: RestartJudgement = NOT_JUDGED
    #: rnsd's RPC listener ``@rns/<name>/rpc`` up (None = unobservable, or
    #: never asked because ownership failed first). Residue (8).
    rpc_ready: Optional[bool] = None

    @property
    def rnsd_restart(self) -> str:
        return self.restart.state

    @property
    def rnsd_problem(self) -> str:
        """Why rnsd did not cleanly restart ("" = it did)."""
        return self.restart.why if self.restart.state != "ok" else ""

    @property
    def verdict(self) -> Optional[bool]:
        """True = repaired; False = a PROVEN failure; None = some part could
        not be observed. A proven failure outranks an unknown. ONE reading
        for ``ok`` and the CLI's exit code (review of f9b5e0eb, finding 4)."""
        # F-A (second read, 2026-09-29): a client that could NOT be stopped is
        # in the F1 shape (job lands later, never restarted) — never "Fixed".
        if (self.rnsd_owns is False or self.start_failed or self.not_stopped
                or self.rnsd_restart == "failed"
                or (self.rnsd_owns is True and self.rpc_ready is False)):
            return False
        # R6: an unjudged release cannot say rnsd restarted — UNKNOWN, never a pass
        if (self.rnsd_owns is None or self.rpc_ready is None
                or self.rnsd_restart in ("unverified", "not_judged")):
            return None
        return True

    @property
    def ok(self) -> bool:
        return self.verdict is True

    def summary(self) -> str:
        lead = f"{self.rnsd_problem} " if self.rnsd_problem else ""
        return lead + self._ownership_summary()

    def _ownership_summary(self) -> str:
        held = ", ".join(self.left_stopped) or "none"
        ns = (" NOT stopped (may be the squatter): " + ", ".join(self.not_stopped) + "."
              if self.not_stopped else "")
        if self.rnsd_restart == "failed":
            # F2 (operator 2026-10-27): never back on the process the repair was for
            return (f"Clients left STOPPED rather than put back on an rnsd that did "
                    f"not cleanly restart: {held}.{ns}")
        if self.rnsd_owns is True and self.rpc_ready is False:
            return (f"rnsd owns the shared instance but its RPC listener never came up "
                    f"(still bringing up interfaces?) — left STOPPED so they do not "
                    f"die on a refused RPC: {held}.")
        if self.rnsd_owns is True:
            line = "rnsd owns the shared instance."
            if self.rpc_ready is None:
                # R3: unverified → release + UNKNOWN
                line += (" Its RPC listener could not be observed (/proc/net/unix) — "
                         "clients started anyway; RPC readiness UNKNOWN.")
            if self.started:
                line += " Restarted clients: " + ", ".join(self.started) + "."
            if self.start_failed:
                line += " FAILED to restart: " + ", ".join(self.start_failed) + "."
            if self.not_stopped:
                line += (" Could NOT be stopped before the restart (check them): "
                         + ", ".join(self.not_stopped) + ".")
            return line
        why = ("ownership could not be observed (ss unavailable)"
               if self.rnsd_owns is None else
               "rnsd does NOT own the shared instance")
        own = "; ".join(f"PID {p}: {c[:60]}" for p, c in self.owners) or "no listener"
        return (f"{why} — left STOPPED so they cannot squat it: {held}. "
                f"Listener: {own}.{ns}")


def instance_name() -> str:
    from utils.paths import ReticulumPaths
    return ReticulumPaths.get_configured_instance_name() or "default"


def _read_cmdline(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace").strip()
    except OSError:
        return None


def _scan_owners(name: str) -> Optional[dict]:
    """``{pid: comm}`` holding ``@rns/<name>``; None if ``ss`` unobservable."""
    from utils.rns_init import _parse_ss_listener_line
    try:
        proc = subprocess.run(["ss", "-xnpl"], capture_output=True,
                              text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    owners = {}
    for line in proc.stdout.splitlines():
        parsed = _parse_ss_listener_line(line, name)
        if parsed is not None:
            owners[parsed[0]] = parsed[1]
    return owners


def listener_owners(name: Optional[str] = None) -> Optional[List[Tuple[int, str]]]:
    """``[(pid, cmdline)]`` holding ``@rns/<name>``; ``[]`` if none; None if
    ``ss`` was unobservable."""
    owners = _scan_owners(name or instance_name())
    if owners is None:
        return None
    return [(pid, _read_cmdline(pid) or comm)
            for pid, comm in sorted(owners.items())]


def rnsd_owns_listener(name: Optional[str] = None) -> Optional[bool]:
    """True only when the listener exists and EVERY owner is rnsd itself.
    None when unobservable; absent, squatted or mid-teardown → False."""
    from utils.rns_init import cmdline_is_rnsd_shaped
    owners = _scan_owners(name or instance_name())
    if owners is None:
        return None
    if not owners:
        return False
    for pid, comm in owners.items():
        if comm == "rnsd":
            continue
        cmdline = _read_cmdline(pid)
        if cmdline is None or not cmdline_is_rnsd_shaped(cmdline):
            return False  # squatter, or owner mid-teardown
    return True


_SO_ACCEPTCON = 0x10000  # /proc/net/unix Flags bit: a LISTENING socket


def rpc_listener_up(name: Optional[str] = None,
                    proc_net_unix: str = "/proc/net/unix") -> Optional[bool]:
    """Is rnsd's RPC listener ``@rns/<name>/rpc`` listening? None = unreadable.

    Residue (8), 2026-10-27: RNS binds ``@rns/<name>`` first, brings up every
    interface (an RNode serial open takes 2-5 s), and only THEN creates the
    RPC listener — ownership of the data socket is not readiness. Read from
    /proc/net/unix, not ss: ss truncates a spaced instance name at the first
    space, so both sockets of "volcano ai rns" display as ``@rns/volcano``."""
    target = f"@rns/{name or instance_name()}/rpc"
    try:
        with open(proc_net_unix, encoding="utf-8", errors="replace") as f:
            next(f, None)  # header
            for line in f:
                cols = line.rstrip("\n").split(None, 7)
                if len(cols) == 8 and cols[7] == target:
                    try:
                        if int(cols[3], 16) & _SO_ACCEPTCON:
                            return True
                    except ValueError:
                        continue
    except OSError as e:
        logger.debug("RPC listener check: %s unreadable: %s", proc_net_unix, e)
        return None
    return False


def terminate_pid(pid: int, wait_s: float = 3.0) -> bool:
    """SIGTERM one PID (never a pattern match) and wait for it to exit."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError as e:
        logger.warning("could not signal PID %s: %s", pid, e)
        return False
    ev = threading.Event()
    for _ in range(int(wait_s / 0.2)):
        if not os.path.exists(f"/proc/{pid}"):
            return True
        ev.wait(0.2)
    return not os.path.exists(f"/proc/{pid}")


def hold_rns_clients(units: Optional[Sequence[Tuple[str, bool]]] = None) -> ClientHold:
    """Stop every ACTIVE RNS client unit; remember which ones.

    ``units=None`` (every real caller): the MEASURED clients unioned with the
    RNS_CLIENT_UNITS floor. An explicit list is used as given (tests).
    """
    hold = ClientHold()
    if units is not None:
        units = list(units)
    else:
        units = list(RNS_CLIENT_UNITS)
        obs = measure_rns_clients()
        match obs:
            case Seen(value=m):
                extra = [u for u in m.clients if u not in units]
                units += extra
                hold.unprobed = [x for x in m.unprobed if (x[0], x[1]) not in units]
                hold.repo_only = [x for x in m.repo_only if x not in units]
                hold.unjudged = m.unjudged
                # Report every skipped transient/oneshot unit — `run-*` too:
                # systemd-run names one-offs run-uNN, exactly where a one-off
                # RNS client lives. Only the system never-hold units are quiet.
                quiet = tuple(x for x in NEVER_HOLD_PREFIXES if x != "run-")
                hold.not_considered = [u for u in m.skipped if not u.startswith(quiet)]
                hold.client_set = (
                    f"measured, service cgroups only ({len(m.clients)} units whose own entry point "
                    f"loads RNS" + (f", {len(extra)} beyond the curated floor"
                                    if extra else "") + ") + curated floor"
                    + "; a client run from a login/tmux session is not seen"
                    + ("" if m.user_scope_seen else
                       " — USER scope NOT visible to this run (no SUDO_USER?): "
                       "user-scope clients outside the floor would stay up"))
            case Unobservable(why=w) | Failed(why=w):
                hold.client_set = (f"curated floor ONLY — measurement unavailable "
                                   f"({w}); a client outside the floor would stay up")
                logger.warning("rnsd restart: %s", hold.client_set)
            case _ as unreachable:
                assert_never(unreachable)
    for unit, user in units:
        # BOTH scopes tri-state (review S2): a squatter-in-waiting is never
        # skipped on a bool; None = unobservable, recorded.
        active = _client_active(unit, user)
        if active is None:
            hold.unobservable.append((unit, user))
            continue
        if not active:
            continue
        deadline = stop_deadline_s(unit, user)
        ok, msg = stop_service(unit, user=user, timeout=deadline)
        if not ok and msg.startswith("Timeout"):
            # F1: our systemctl CLIENT gave up; the stop JOB is still queued
            # in systemd and lands on its own. Re-ask the unit.
            again = _client_active(unit, user)
            if again is False:
                ok, msg = True, f"{unit} stopped (client waited {deadline}s)"
            elif again is None:
                msg = f"{unit} state UNKNOWN after the client waited {deadline}s"
        if ok:
            hold.stopped.append((unit, user))
        else:
            hold.stop_failed.append((unit, user, msg))
    return hold


def release_rns_clients(
    hold: ClientHold,
    name: Optional[str] = None,
    wait_s: float = DEFAULT_OWNERSHIP_WAIT_S,
    poll_s: float = 0.5,
    _wait: Optional[Callable[[float], None]] = None,
    rnsd_before: Optional[Dict[str, str]] = None,
) -> ReleaseResult:
    """Wait for rnsd to OWN the listener, then start the held clients.
    If it never does, leave them stopped and say which.

    ``rnsd_before`` = ``show_unit('rnsd')`` taken BEFORE the caller stopped
    rnsd. With it, the restart is judged HERE (F2/F3, 2026-10-28), so every
    repair path gets the same answer: proven failed → clients stay stopped
    and named; unverified → released, verdict UNKNOWN. Without it the
    release reads UNKNOWN (``not_judged``) — every src caller passes one,
    pinned by ``test_every_release_site_is_judged``."""
    name = name or instance_name()
    wait = _wait or threading.Event().wait
    after_start: Dict[str, str] = {}
    if rnsd_before is not None:
        after_start = show_unit('rnsd')
        early = judge_rnsd_restart(rnsd_before, after_start, after_start)
        if early.state == "failed":
            # decidable now (MainPID 0, or unchanged): no wait can change it
            return _held(hold, name, ReleaseResult(
                rnsd_owns=rnsd_owns_listener(name), restart=early))

    def ready() -> Tuple[Optional[bool], Optional[bool]]:
        # ownership first (#69); the RPC listener is created LAST (residue 8)
        o = rnsd_owns_listener(name)
        return o, (rpc_listener_up(name) if o is True else None)

    owns, rpc = ready()
    for _ in range(max(0, int(wait_s / poll_s))):
        if owns is True and rpc in (True, None):
            break  # ready — or the RPC read is unobservable, which waiting cannot fix
        wait(poll_s)
        owns, rpc = ready()

    result = ReleaseResult(rnsd_owns=owns, rpc_ready=rpc)
    if rnsd_before is not None:
        result.restart = judge_rnsd_restart(rnsd_before, after_start, show_unit('rnsd'))
    # R3: an unreadable RPC read (None) releases + UNKNOWN; only a PROVEN
    # not-ready (False), a non-rnsd owner or a failed restart holds them
    if owns is not True or rpc is False or result.rnsd_restart == "failed":
        return _held(hold, name, result)
    result.not_stopped = _not_stopped(hold)
    for unit, user in hold.stopped:
        ok, msg = start_service(unit, user=user)
        label = f"{unit} ({_scope(user)})"
        (result.started if ok else result.start_failed).append(label)
        if not ok:
            logger.warning("client restart %s failed: %s", unit, msg)
    return result


def _not_stopped(hold: ClientHold) -> List[str]:
    return [f"{u} ({_scope(user)}): {m}" for u, user, m in hold.stop_failed]


def _held(hold: ClientHold, name: str, result: ReleaseResult) -> ReleaseResult:
    """Leave every held client stopped and say which, and who holds @rns."""
    result.not_stopped = _not_stopped(hold)
    result.left_stopped = hold.names()
    result.owners = listener_owners(name) or []
    logger.warning("rnsd restart: %s", result.summary())
    return result


#: systemd SubStates of a unit that has not yet started its main process
#: (ExecCondition / ExecStartPre / ExecStart of a not-yet-ready unit)
_PRE_START_SUBSTATES = ("condition", "start-pre", "start")


def _pre_start(st: Dict[str, str]) -> bool:
    return (st.get("MainPID") == "0" and st.get("ActiveState") == "activating"
            and st.get("SubState") in _PRE_START_SUBSTATES)


def judge_rnsd_restart(before: Dict[str, str], after_start: Dict[str, str],
                       after_wait: Dict[str, str]) -> RestartJudgement:
    """``state == "ok"`` when rnsd measurably restarted and stayed on one
    process through the ownership wait; else ``"failed" | "unverified"`` with
    ``why``. ``trace`` carries the MainPIDs either way. Pure.

    after_start vs after_wait (not the pre-restart NRestarts): whether an
    explicit start resets the counter does not matter for a window that
    begins after that start (residue (2), 2026-10-27)."""
    pid0, pid1, pid2 = (d.get("MainPID", "") for d in (before, after_start, after_wait))
    n1, n2 = after_start.get("NRestarts", ""), after_wait.get("NRestarts", "")
    trace = (f"rnsd MainPID {pid0 or '?'} -> {pid1 or '?'} -> {pid2 or '?'}, "
             f"NRestarts {n1 or '?'} -> {n2 or '?'}")
    J = lambda state, why="": RestartJudgement(state, why, trace)  # noqa: E731
    if "" in (pid0, pid1, pid2):
        return J("unverified", f"rnsd restart UNVERIFIED: MainPID unobservable "
                               f"({pid0 or '?'} -> {pid1 or '?'} -> {pid2 or '?'}).")
    # systemd counts a Restart= cycle in NRestarts; auto-restart* is BETWEEN
    # cycles (activating, MainPID 0) — a proven crash, never "still starting"
    if n1 != n2 or any(d.get("SubState", "").startswith("auto-restart")
                       for d in (after_start, after_wait)):
        return J("failed", f"rnsd crashed after the restart and systemd's Restart= "
                 f"cycled it (MainPID {pid1} -> {pid2}, NRestarts {n1 or '?'} -> {n2 or '?'}, "
                 f"now {after_wait.get('ActiveState', '?')}/{after_wait.get('SubState', '?')}).")
    # Review of 2cf548ae (#2): during ExecStartPre (wait_for_ipv6_ll.sh, up to
    # 30 s) systemd reports activating with MainPID 0 — not started YET, not dead.
    if pid2 == "0" and _pre_start(after_wait):
        return J("unverified", f"rnsd restart UNCONFIRMED: rnsd is still starting "
                 f"(activating/{after_wait.get('SubState', '?')}) after the wait.")
    pre_start = _pre_start(after_start)
    if pid2 == "0" or (pid1 == "0" and not pre_start):
        dead = after_wait if pid2 == "0" else after_start
        return J("failed", f"rnsd is not running after the restart "
                 f"({dead.get('ActiveState', '?')}/{dead.get('SubState', '?')}).")
    if pid0 != "0" and pid0 in (pid1, pid2):
        return J("failed", f"rnsd did not restart: MainPID unchanged ({pid0}).")
    if pid2 != pid1 and not pre_start:
        return J("failed", f"rnsd crashed during the ownership wait and systemd restarted it "
                 f"(MainPID {pid1} -> {pid2}, NRestarts {n1 or '?'} -> {n2 or '?'}).")
    return J("ok")


def ordered_restart_rnsd(
    wait_s: float = DEFAULT_OWNERSHIP_WAIT_S,
    while_stopped: Optional[Callable[[], None]] = None,
) -> Tuple[bool, ReleaseResult, ClientHold]:
    """clients down → rnsd down → [while_stopped] → rnsd up → owns? → clients up.

    Returns ``(rnsd_started, release_result, hold)``.
    """
    before = show_unit('rnsd')
    hold = hold_rns_clients()
    # The unit's own stop timeout, as for the clients (F1): a 30 s client wait
    # under a longer TimeoutStopSec races start_service against a queued stop.
    stopped, msg = stop_service('rnsd', timeout=stop_deadline_s('rnsd', False))
    if not stopped:
        logger.warning("rnsd stop: %s", msg)
    if while_stopped is not None:
        while_stopped()
    started, msg = start_service('rnsd')
    if not started:
        logger.warning("rnsd start failed: %s", msg)
    # residue (1)+(2): `systemctl start` on a still-active unit is a no-op
    # success, so a failed stop read as a repair — release measures the
    # process against this baseline, and holds the clients if it failed (F2).
    release = release_rns_clients(hold, wait_s=wait_s, rnsd_before=before)
    if release.rnsd_problem:
        logger.warning("rnsd restart: %s", release.rnsd_problem)
    return started, release, hold
