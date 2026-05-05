#!/usr/bin/env python3
"""
boundary_soak.py — periodic health report for the boundary timing forensics.

Reads journald for the meshanchor-* units (or any units passed in), parses
the WARNING lines emitted by utils.boundary_timing
(``rpc[<label>] slow|raised``), aggregates per label, and writes a markdown
report and JSON sidecar to::

    --out-dir / <YYYYMMDDTHHMMSSZ>.md
    --out-dir / <YYYYMMDDTHHMMSSZ>.json

If a prior JSON sidecar exists in the same directory, the markdown also
shows the per-label delta in slow_count / raised_count vs. the most recent
prior run, so a regression is visible at a glance.

Designed to run from cron every 6h (or 24h for quieter readings). It is
deliberately standalone — no ``src/`` imports — so it can drop onto any
fleet host without dragging dependencies along.

Charter: ``.claude/plans/boundary_observability_charter.md`` — soak fills
the observability gap until Phase E (status surface) lands.

Install (per host)::

    crontab -l 2>/dev/null | { cat; \\
        echo "0 */6 * * * /usr/bin/python3 /opt/meshanchor/scripts/boundary_soak.py"; \\
    } | crontab -

Requires the user to be in the ``adm`` (Debian/RPi OS) or
``systemd-journal`` group so ``journalctl`` can read system-unit logs
without sudo. Use ``--use-sudo`` if your host requires it (NOPASSWD only).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pwd
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _real_user_home() -> Path:
    """Real user's home, even when invoked via sudo (MF001).

    Inlined from utils.paths.get_real_user_home so this script stays
    src-import-free for fleet drop-in. Same precedence: SUDO_USER →
    LOGNAME → current process. Path traversal-guarded.
    """
    for env in ("SUDO_USER", "LOGNAME"):
        name = os.environ.get(env, "")
        if name and name != "root" and "/" not in name and ".." not in name:
            try:
                return Path(pwd.getpwnam(name).pw_dir)
            except KeyError:
                continue
    return Path.home()


DEFAULT_UNITS: tuple = (
    "meshanchor-gateway",
    "meshanchor-map",
    "meshanchor-collector",
)
DEFAULT_WINDOW = "6 hours ago"
DEFAULT_OUT_DIR = _real_user_home() / ".local" / "share" / "meshanchor" / "soak_reports"

# Two log line shapes the boundary_timing helper emits:
#   rpc[<label>[<target>]] slow: 4.231s (>=2.0s threshold)
#   rpc[<label>[<target>]] raised after 0.034s
# The label may itself contain a [<target>] suffix, so the outer brackets
# are matched non-greedily on a no-bracket inner pattern.
RPC_RX = re.compile(r"rpc\[([^\]]+(?:\[[^\]]+\])?)\]\s+(slow|raised)\b[^\d]*([\d.]+)s")

logger = logging.getLogger("boundary_soak")


def parse_journal(text: str) -> Dict[str, Dict]:
    """Aggregate boundary WARN lines by label.

    Returns a dict keyed by label (e.g. ``meshtasticd.toradio_put[a1b2c3d4]``)
    with values ``{'slow': int, 'raised': int, 'max_s': float, 'sum_s': float}``.
    """
    out: Dict[str, Dict] = defaultdict(
        lambda: {"slow": 0, "raised": 0, "max_s": 0.0, "sum_s": 0.0}
    )
    for line in text.splitlines():
        m = RPC_RX.search(line)
        if not m:
            continue
        label, kind, secs_s = m.group(1), m.group(2), m.group(3)
        try:
            secs = float(secs_s)
        except ValueError:
            continue
        rec = out[label]
        rec[kind] += 1
        rec["max_s"] = max(rec["max_s"], secs)
        rec["sum_s"] += secs
    return dict(out)


def collect_journal(units: List[str], since: str, use_sudo: bool) -> str:
    """Run journalctl and return its stdout."""
    cmd: List[str] = []
    if use_sudo:
        cmd.append("sudo")
    cmd += ["journalctl", "-q", "--since", since, "-o", "cat"]
    for u in units:
        cmd += ["-u", u]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, check=False
    )
    # rc=0 normal, rc=1 "no entries" — both fine. Anything else is a real error.
    if proc.returncode not in (0, 1):
        sys.stderr.write(
            f"journalctl failed (rc={proc.returncode}): {proc.stderr.strip()}\n"
        )
        sys.exit(2)
    return proc.stdout


def find_prior_data(out_dir: Path) -> Optional[Dict]:
    """Find the most recent prior JSON sidecar and return its parsed data."""
    if not out_dir.exists():
        return None
    candidates = sorted(out_dir.glob("*.json"))
    if not candidates:
        return None
    try:
        with candidates[-1].open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not read prior report %s: %s", candidates[-1], e)
        return None


def diff_against_prior(
    current: Dict[str, Dict], prior: Optional[Dict]
) -> Dict[str, Dict]:
    """Compute regression deltas per label.

    Returns ``{label: {'d_slow': int, 'd_raised': int, 'is_new': bool}}``.
    A label that didn't exist in the prior run is flagged ``is_new=True``;
    deltas there equal the current counts.
    """
    out: Dict[str, Dict] = {}
    prior_labels: Dict = (prior or {}).get("labels", {}) if prior else {}
    for label, rec in current.items():
        prev = prior_labels.get(label, {})
        out[label] = {
            "d_slow": rec["slow"] - prev.get("slow", 0),
            "d_raised": rec["raised"] - prev.get("raised", 0),
            "is_new": label not in prior_labels,
        }
    return out


def write_reports(
    out_dir: Path,
    timestamp: datetime,
    window: str,
    units: List[str],
    current: Dict[str, Dict],
    deltas: Dict[str, Dict],
) -> Path:
    """Write the markdown + JSON sidecar. Returns the markdown path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    md_path = out_dir / f"{stamp}.md"
    json_path = out_dir / f"{stamp}.json"

    json_data = {
        "timestamp": timestamp.isoformat(),
        "window": window,
        "units": list(units),
        "labels": current,
    }
    with json_path.open("w") as f:
        json.dump(json_data, f, indent=2, sort_keys=True)

    with md_path.open("w") as f:
        f.write(f"# Boundary soak — {timestamp.isoformat()}\n\n")
        f.write(f"**Window**: {window}  \n")
        f.write(f"**Units**: `{', '.join(units)}`\n\n")
        if not current:
            f.write(
                "No `rpc[*]` WARN lines in the window — every wrapped boundary "
                "stayed under threshold.\n"
            )
            return md_path

        f.write(
            "| label | slow | raised | max (s) | mean (s) | "
            "Δ slow | Δ raised | new |\n"
        )
        f.write("|---|---:|---:|---:|---:|---:|---:|:---:|\n")
        ordered = sorted(current.items(), key=lambda x: -x[1]["max_s"])
        for label, rec in ordered:
            count = rec["slow"] + rec["raised"]
            mean = rec["sum_s"] / count if count else 0.0
            d = deltas.get(label, {})
            d_slow = d.get("d_slow", 0)
            d_raised = d.get("d_raised", 0)
            new_mark = "*" if d.get("is_new") else ""
            f.write(
                f"| `{label}` | {rec['slow']} | {rec['raised']} | "
                f"{rec['max_s']:.3f} | {mean:.3f} | "
                f"{d_slow:+d} | {d_raised:+d} | {new_mark} |\n"
            )
    return md_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate boundary timing WARN lines from journald into a "
            "per-run markdown + JSON report."
        ),
    )
    parser.add_argument(
        "--unit", action="append", default=None,
        help=(
            "Systemd unit to scan (repeatable). "
            f"Default: {' '.join(DEFAULT_UNITS)}"
        ),
    )
    parser.add_argument(
        "--since", default=DEFAULT_WINDOW,
        help=f"Window passed to journalctl --since. Default: '{DEFAULT_WINDOW}'.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR,
        help=f"Where to write reports. Default: {DEFAULT_OUT_DIR}.",
    )
    parser.add_argument(
        "--use-sudo", action="store_true",
        help="Wrap journalctl with sudo (requires NOPASSWD).",
    )
    args = parser.parse_args(argv)

    units = list(args.unit) if args.unit else list(DEFAULT_UNITS)
    text = collect_journal(units, args.since, args.use_sudo)
    current = parse_journal(text)

    prior = find_prior_data(args.out_dir)
    deltas = diff_against_prior(current, prior)

    now = datetime.now(timezone.utc)
    md_path = write_reports(args.out_dir, now, args.since, units, current, deltas)

    if not current:
        print(f"soak ok: no WARN lines (report {md_path.name})")
        return 0

    total_slow = sum(r["slow"] for r in current.values())
    total_raised = sum(r["raised"] for r in current.values())
    worst_label, worst_rec = max(current.items(), key=lambda x: x[1]["max_s"])
    new_labels = sum(1 for d in deltas.values() if d["is_new"])
    print(
        f"soak: slow={total_slow} raised={total_raised} new_labels={new_labels} "
        f"worst={worst_label} max={worst_rec['max_s']:.2f}s -> {md_path.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
