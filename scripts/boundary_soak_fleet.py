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
) -> Path:
    """Write the fleet markdown + JSON sidecar. Returns the markdown path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    md_path = out_dir / f"fleet-{stamp}.md"
    json_path = out_dir / f"fleet-{stamp}.json"

    # Strip None per-host entries from JSON (keep them as null for context)
    json_data = {
        "timestamp": timestamp.isoformat(),
        "per_host": per_host,
        "rollup": rollup,
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

        # Per-host status — shows reachability + report freshness
        f.write("## Per-host\n\n")
        f.write("| host | reports? | window | unique labels |\n")
        f.write("|---|:---:|---|---:|\n")
        for h in hosts:
            d = per_host.get(h)
            if not d:
                f.write(f"| `{h}` | – | – | 0 |\n")
                continue
            n_labels = len(d.get("labels", {}))
            window = d.get("window", "?")
            f.write(f"| `{h}` | OK | {window} | {n_labels} |\n")

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
    md_path = write_fleet_report(args.fleet_dir, now, per_host, rollup)

    n_hosts_ok = sum(1 for d in per_host.values() if d)
    n_hosts = len(per_host)
    n_labels = len(rollup)
    print(
        f"fleet-soak: {n_hosts_ok}/{n_hosts} hosts ok, "
        f"{n_labels} labels in rollup -> {md_path.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
