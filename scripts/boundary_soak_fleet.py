#!/usr/bin/env python3
"""
boundary_soak_fleet.py — fleet-wide rollup of per-host boundary soak reports.

Companion to ``boundary_soak.py``. Where the per-host script answers
"what is THIS NOC's boundary health?", this script answers "across the
fleet, which boundaries are slow on which hosts?".

How it works:
1. Reads the latest local soak JSON sidecar from ``--local-dir``
   (default ``~/.local/share/meshanchor/soak_reports/``).
2. For each peer host passed as ``--host <ssh-target>``, rsyncs the
   peer's ``*.json`` sidecars into ``<fleet-dir>/<host>/`` and reads
   the most recent one. A peer that times out or rejects ssh is logged
   and skipped — one bad host does not kill the run.
3. Aggregates per-label across hosts: total_slow, total_raised, max_s,
   and the list of hosts that saw the label.
4. Writes a single fleet markdown + JSON sidecar to ``<fleet-dir>/``:

       fleet-<YYYYMMDDTHHMMSSZ>.md
       fleet-<YYYYMMDDTHHMMSSZ>.json

Designed to run from cron once per day on the NOC pi (the host with
ssh keys to the rest of the fleet)::

    0 7 * * * /usr/bin/python3 /opt/meshanchor/scripts/boundary_soak_fleet.py \
        --host pi-r1 --host pi-portable-1 --host pi-portable-2

Single-NOC deployments can omit ``--host`` entirely — the script will
just rebuild a rollup of the local reports, which is still useful for
trend tracking even with one host.

Requires:
- ``rsync`` installed locally and on each peer.
- Passwordless ssh from the running user to each peer (standard ssh
  key setup; this script does not pass auth).
- The peer's reports directory at ``~/.local/share/meshanchor/soak_reports/``
  on the peer (i.e. it has been running boundary_soak.py from cron).

No ``src/`` imports — drop-in standalone.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pwd
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _real_user_home() -> Path:
    """Real user's home, even when invoked via sudo (MF001).

    Same precedence as utils.paths.get_real_user_home, inlined here so
    this script stays src-import-free for fleet drop-in.
    """
    for env in ("SUDO_USER", "LOGNAME"):
        name = os.environ.get(env, "")
        if name and name != "root" and "/" not in name and ".." not in name:
            try:
                return Path(pwd.getpwnam(name).pw_dir)
            except KeyError:
                continue
    return Path.home()


DEFAULT_LOCAL_DIR = _real_user_home() / ".local" / "share" / "meshanchor" / "soak_reports"
DEFAULT_FLEET_DIR = _real_user_home() / ".local" / "share" / "meshanchor" / "fleet_reports"
DEFAULT_REMOTE_DIR = ".local/share/meshanchor/soak_reports/"

# Default stale threshold for the daily fleet aggregator: 30h. Per-host
# soak runs every 6h, so anything older than 30h has missed at least 4
# cycles and the host is almost certainly silent. Catches the dominant
# silent-failure mode: daemon dead -> no boundary timing emitted -> per-
# host report never updates -> fleet report still says "OK" even though
# the host is broken. Without this check, an empty soak report from a
# wedged daemon is indistinguishable from a healthy one.
DEFAULT_STALE_THRESHOLD_HOURS = 30.0

logger = logging.getLogger("boundary_soak_fleet")


def rsync_host(
    host: str,
    fleet_dir: Path,
    remote_dir: str = DEFAULT_REMOTE_DIR,
    timeout: int = 60,
) -> bool:
    """Rsync the peer's *.json sidecars into ``fleet_dir/<host>/``.

    Returns True on success. Logs and returns False on any rsync /
    ssh / timeout failure so a single bad host does not kill the run.
    """
    target = fleet_dir / host
    target.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-rt",
        "--timeout", str(timeout),
        "--include", "*.json",
        "--exclude", "*",
        f"{host}:{remote_dir}",
        str(target) + "/",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 30, check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("rsync timed out for %s", host)
        return False
    except FileNotFoundError:
        logger.error("rsync not installed locally; cannot sync any peer")
        return False
    if proc.returncode != 0:
        logger.warning(
            "rsync from %s failed (rc=%d): %s",
            host, proc.returncode, proc.stderr.strip(),
        )
        return False
    return True


def latest_report_for_host(host_dir: Path) -> Optional[Dict]:
    """Find and parse the most recent JSON sidecar in ``host_dir``."""
    if not host_dir.exists():
        return None
    candidates = sorted(host_dir.glob("*.json"))
    if not candidates:
        return None
    try:
        return json.loads(candidates[-1].read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Bad sidecar %s: %s", candidates[-1], e)
        return None


def report_age_hours(data: Optional[Dict], now: datetime) -> Optional[float]:
    """Compute the age in hours of a per-host report, or None if unknown.

    Returns None when the report is missing entirely (rsync failed) or
    its timestamp can't be parsed. The caller treats None as "no data" —
    a separate state from "stale" — so a flapping rsync doesn't get
    silently bucketed with a wedged daemon.
    """
    if not data:
        return None
    ts_str = data.get("timestamp")
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def aggregate(per_host: Dict[str, Optional[Dict]]) -> Dict[str, Dict]:
    """Roll per-host data up into per-label fleet totals.

    Returns ``{label: {'total_slow': int, 'total_raised': int,
    'max_s': float, 'hosts': List[str]}}``. Hosts whose value is None
    (rsync failed, no reports yet) are silently skipped — they show up
    in the per-host status block but cannot contribute to a rollup.
    """
    by_label: Dict[str, Dict] = {}
    for host, data in per_host.items():
        if not data:
            continue
        labels = data.get("labels", {})
        for label, rec in labels.items():
            agg = by_label.setdefault(label, {
                "total_slow": 0,
                "total_raised": 0,
                "max_s": 0.0,
                "hosts": [],
            })
            agg["total_slow"] += rec.get("slow", 0)
            agg["total_raised"] += rec.get("raised", 0)
            agg["max_s"] = max(agg["max_s"], rec.get("max_s", 0.0))
            if host not in agg["hosts"]:
                agg["hosts"].append(host)
    return by_label


def write_fleet_report(
    out_dir: Path,
    timestamp: datetime,
    per_host: Dict[str, Optional[Dict]],
    rollup: Dict[str, Dict],
    stale_threshold_hours: float = DEFAULT_STALE_THRESHOLD_HOURS,
) -> Path:
    """Write the fleet markdown + JSON sidecar. Returns the markdown path.

    Per-host table now carries an ``age (h)`` column and a ``stale?`` flag
    so a host whose daemon went silent shows up immediately. ``stale?`` is
    true when the host's most recent report is older than
    ``stale_threshold_hours`` (default 30h, = 5x the typical 6h cron).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    md_path = out_dir / f"fleet-{stamp}.md"
    json_path = out_dir / f"fleet-{stamp}.json"

    # Strip None per-host entries from JSON (keep them as null for context)
    json_data = {
        "timestamp": timestamp.isoformat(),
        "per_host": per_host,
        "rollup": rollup,
        "stale_threshold_hours": stale_threshold_hours,
    }
    with json_path.open("w") as f:
        json.dump(json_data, f, indent=2, sort_keys=True, default=str)

    hosts = sorted(per_host.keys())
    with md_path.open("w") as f:
        f.write(f"# Fleet soak — {timestamp.isoformat()}\n\n")
        if not hosts:
            f.write("No hosts configured. Pass `--host <target>` or run "
                    "`boundary_soak.py` locally.\n")
            return md_path

        f.write(f"**Hosts**: {', '.join(f'`{h}`' for h in hosts)}\n\n")

        # Per-host status — reachability + report freshness + staleness
        f.write("## Per-host\n\n")
        f.write("| host | reports? | age (h) | stale? | window | unique labels |\n")
        f.write("|---|:---:|---:|:---:|---|---:|\n")
        for h in hosts:
            d = per_host.get(h)
            if not d:
                f.write(f"| `{h}` | – | ? | ? | – | 0 |\n")
                continue
            age = report_age_hours(d, timestamp)
            if age is None:
                age_str = "?"
                stale_mark = "?"
            else:
                age_str = f"{age:.1f}"
                stale_mark = "**STALE**" if age > stale_threshold_hours else ""
            n_labels = len(d.get("labels", {}))
            window = d.get("window", "?")
            f.write(
                f"| `{h}` | OK | {age_str} | {stale_mark} | "
                f"{window} | {n_labels} |\n"
            )

        if not rollup:
            f.write(
                "\nAll wrapped boundaries stayed under threshold across the "
                "fleet — no rollup rows.\n"
            )
            return md_path

        f.write("\n## Fleet rollup (sorted by max latency)\n\n")
        f.write("| label | hosts | total slow | total raised | max (s) |\n")
        f.write("|---|---|---:|---:|---:|\n")
        ordered = sorted(rollup.items(), key=lambda x: -x[1]["max_s"])
        for label, agg in ordered:
            host_list = ", ".join(f"`{h}`" for h in agg["hosts"])
            f.write(
                f"| `{label}` | {host_list} | "
                f"{agg['total_slow']} | {agg['total_raised']} | "
                f"{agg['max_s']:.3f} |\n"
            )
    return md_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate per-host boundary soak reports into one fleet rollup."
        ),
    )
    parser.add_argument(
        "--host", action="append", default=None,
        help=(
            "Peer host to rsync from (repeatable). Local reports are always "
            "included. Omit for single-NOC deployments."
        ),
    )
    parser.add_argument(
        "--local-dir", type=Path, default=DEFAULT_LOCAL_DIR,
        help=f"Local soak_reports dir. Default: {DEFAULT_LOCAL_DIR}",
    )
    parser.add_argument(
        "--fleet-dir", type=Path, default=DEFAULT_FLEET_DIR,
        help=(
            "Where peer JSON sidecars are mirrored AND fleet reports are "
            f"written. Default: {DEFAULT_FLEET_DIR}"
        ),
    )
    parser.add_argument(
        "--remote-dir", default=DEFAULT_REMOTE_DIR,
        help=(
            "Path on each peer to rsync from (relative to peer's home, "
            f"trailing slash matters for rsync). Default: '{DEFAULT_REMOTE_DIR}'"
        ),
    )
    parser.add_argument(
        "--rsync-timeout", type=int, default=60,
        help="Per-host rsync timeout in seconds. Default: 60.",
    )
    parser.add_argument(
        "--stale-threshold-hours", type=float,
        default=DEFAULT_STALE_THRESHOLD_HOURS,
        help=(
            "Flag a host as STALE if its latest soak report is older than "
            "this. The aggregator exits non-zero when ANY host is stale or "
            f"unreachable, so cron mail catches silent failure. Default: "
            f"{DEFAULT_STALE_THRESHOLD_HOURS}h."
        ),
    )
    args = parser.parse_args(argv)

    args.fleet_dir.mkdir(parents=True, exist_ok=True)

    per_host: Dict[str, Optional[Dict]] = {}
    per_host["local"] = latest_report_for_host(args.local_dir)

    for host in (args.host or []):
        ok = rsync_host(
            host, args.fleet_dir, args.remote_dir, args.rsync_timeout,
        )
        per_host[host] = (
            latest_report_for_host(args.fleet_dir / host) if ok else None
        )

    rollup = aggregate(per_host)
    now = datetime.now(timezone.utc)
    md_path = write_fleet_report(
        args.fleet_dir, now, per_host, rollup, args.stale_threshold_hours,
    )

    # Tally health for exit code + punch summary
    n_hosts = len(per_host)
    stale_hosts: List[str] = []
    unreachable_hosts: List[str] = []
    n_hosts_ok_fresh = 0
    for h, d in per_host.items():
        if not d:
            unreachable_hosts.append(h)
            continue
        age = report_age_hours(d, now)
        if age is None or age > args.stale_threshold_hours:
            stale_hosts.append(h)
        else:
            n_hosts_ok_fresh += 1

    n_labels = len(rollup)
    health_bits: List[str] = [
        f"{n_hosts_ok_fresh}/{n_hosts} hosts fresh",
        f"{n_labels} labels in rollup",
    ]
    if stale_hosts:
        health_bits.append(f"STALE: {','.join(stale_hosts)}")
    if unreachable_hosts:
        health_bits.append(f"unreachable: {','.join(unreachable_hosts)}")
    print(f"fleet-soak: {'; '.join(health_bits)} -> {md_path.name}")

    # Non-zero exit on any host that is stale or unreachable so cron mail
    # surfaces silent-failure conditions on the NOC operator's terms.
    return 0 if not (stale_hosts or unreachable_hosts) else 1


if __name__ == "__main__":
    sys.exit(main())
