#!/usr/bin/env python3
"""
meshtastic_reemit_soak.py — periodic health report for the LXMF→MeshCore
re-emit bridge.

Sister to ``scripts/boundary_soak.py``. Reads journald for the
meshanchor-* units, parses the lines emitted by
``gateway.meshtastic_reemit_bridge``, aggregates by event class
(re-emits per source / per target channel, filter drops, errors,
restarts), and writes a markdown report plus JSON sidecar to::

    --out-dir / <YYYYMMDDTHHMMSSZ>.md
    --out-dir / <YYYYMMDDTHHMMSSZ>.json

If a prior JSON sidecar exists in the same directory, the markdown
also shows per-event deltas vs. the most recent prior run so a
regression is visible at a glance.

Scrape targets (logger name ``gateway.meshtastic_reemit_bridge``)::

    INFO  meshtastic_reemit: started — N source identity(ies), ...
    INFO  meshtastic_reemit: re-emitted on MeshCore ch{N} from {hash8}
          (orig ch{X} sender {sender})
    DEBUG meshtastic_reemit: dropping synth-ACK shaped content from {hash8}: ...
    DEBUG meshtastic_reemit: dropping nested-bridge content from {hash8}
          (prefix '...'): ...
    DEBUG meshtastic_reemit: no active MeshCore handler; ...
    WARNING meshtastic_reemit: invalid strip_pattern ... falling back to default
    WARNING meshtastic_reemit: bad output_format ... using fallback
    WARNING meshtastic_reemit: enabled but source_identities is empty — ...
    ERROR meshtastic_reemit: unexpected error: {exc}

Stats that the bridge tracks but does NOT log (``filtered_identity``,
``filtered_empty``) are deliberately absent here — the soak is a
journal scraper, and those counters are only visible via the bridge's
``get_status()`` snapshot. Pair this script with an API probe if you
want full coverage.

Designed to run from cron every 6h. Standalone — no ``src/`` imports —
so it drops onto any host that runs ``meshanchor-daemon.service``.

Companion to [[meshtastic-reemit-bridge-meshanchor-2026-05-18]].

Install (per host)::

    crontab -l 2>/dev/null | { cat; \\
        echo "0 */6 * * * /usr/bin/python3 /opt/meshanchor/scripts/meshtastic_reemit_soak.py"; \\
    } | crontab -

Requires the user to be in the ``adm`` (Debian/RPi OS) or
``systemd-journal`` group so ``journalctl`` reads system-unit logs
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

    Inlined from ``utils.paths.get_real_user_home`` so this script stays
    src-import-free for fleet drop-in.
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
    "meshanchor",
    "meshanchor-daemon",
    "meshanchor-map",
)
DEFAULT_WINDOW = "6 hours ago"
DEFAULT_OUT_DIR = (
    _real_user_home() / ".local" / "share" / "meshanchor" / "reemit_soak_reports"
)

# Liveness: 1.5x the typical 6h cron interval.
DEFAULT_MAX_GAP_SECS = 9 * 3600

# Line shapes. ``-o cat`` strips the timestamp/host/PID prefix so we
# only need to match the log message body. Hex source hashes are
# truncated to 8 chars by the bridge (``source_hex[:8]``) but be
# permissive in case operator changes that slice.
REEMIT_RX = re.compile(
    r"meshtastic_reemit: re-emitted on MeshCore ch(?P<channel>\d+) "
    r"from (?P<src>[0-9a-f]{4,16}) "
    r"\(orig ch(?P<orig_channel>\S+) sender (?P<sender>[^)]+)\)"
)
DROP_ACK_RX = re.compile(
    r"meshtastic_reemit: dropping synth-ACK shaped content from "
    r"(?P<src>[0-9a-f]{4,16})"
)
DROP_NESTED_RX = re.compile(
    r"meshtastic_reemit: dropping nested-bridge content from "
    r"(?P<src>[0-9a-f]{4,16})"
)
HANDLER_UNAVAIL_RX = re.compile(
    r"meshtastic_reemit: no active MeshCore handler"
)
STARTED_RX = re.compile(r"meshtastic_reemit: started\b")
ERROR_RX = re.compile(r"meshtastic_reemit: unexpected error\b")
WARN_RX = re.compile(
    r"meshtastic_reemit: (invalid strip_pattern|bad output_format|"
    r"enabled but source_identities)"
)

logger = logging.getLogger("meshtastic_reemit_soak")


def _empty_stats() -> Dict:
    """Fresh aggregation slot. Public so tests can build expected shapes."""
    return {
        "reemitted_total": 0,
        "by_source": defaultdict(int),
        "by_target_channel": defaultdict(int),
        "filtered_synth_ack": 0,
        "filtered_nested_bridge": 0,
        "handler_unavailable": 0,
        "errors": 0,
        "warnings": 0,
        "restarts": 0,
    }


def _finalize_stats(stats: Dict) -> Dict:
    """Convert defaultdicts to plain dicts for JSON serialization."""
    out = dict(stats)
    out["by_source"] = dict(stats["by_source"])
    out["by_target_channel"] = dict(stats["by_target_channel"])
    return out


def parse_journal(text: str) -> Dict:
    """Aggregate re-emit-bridge log lines into a stats dict.

    Returns a JSON-serializable dict matching the shape of ``_empty_stats``
    but with plain dicts instead of defaultdicts.
    """
    stats = _empty_stats()
    for line in text.splitlines():
        m = REEMIT_RX.search(line)
        if m:
            stats["reemitted_total"] += 1
            stats["by_source"][m.group("src")] += 1
            stats["by_target_channel"][m.group("channel")] += 1
            continue
        if DROP_ACK_RX.search(line):
            stats["filtered_synth_ack"] += 1
            continue
        if DROP_NESTED_RX.search(line):
            stats["filtered_nested_bridge"] += 1
            continue
        if HANDLER_UNAVAIL_RX.search(line):
            stats["handler_unavailable"] += 1
            continue
        if STARTED_RX.search(line):
            stats["restarts"] += 1
            continue
        if ERROR_RX.search(line):
            stats["errors"] += 1
            continue
        if WARN_RX.search(line):
            stats["warnings"] += 1
            continue
    return _finalize_stats(stats)


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
    if proc.returncode not in (0, 1):
        sys.stderr.write(
            f"journalctl failed (rc={proc.returncode}): {proc.stderr.strip()}\n"
        )
        sys.exit(2)
    return proc.stdout


def check_liveness(
    prior: Optional[Dict], now: datetime, max_gap_secs: int
) -> Optional[float]:
    """Return age (seconds) of the prior run if it exceeds the gap threshold."""
    if prior is None:
        return None
    ts_str = prior.get("timestamp")
    if not ts_str:
        return None
    try:
        prior_ts = datetime.fromisoformat(ts_str)
    except (TypeError, ValueError):
        return None
    if prior_ts.tzinfo is None:
        prior_ts = prior_ts.replace(tzinfo=timezone.utc)
    gap_secs = (now - prior_ts).total_seconds()
    if gap_secs > max_gap_secs:
        return gap_secs
    return None


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


_SCALAR_KEYS = (
    "reemitted_total",
    "filtered_synth_ack",
    "filtered_nested_bridge",
    "handler_unavailable",
    "errors",
    "warnings",
    "restarts",
)


def diff_against_prior(
    current: Dict, prior: Optional[Dict]
) -> Dict:
    """Compute per-counter deltas vs. the prior run.

    Returns ``{counter_name: delta}`` for the scalar counters, plus
    ``new_sources`` (sources seen this window but not the last) and
    ``new_channels`` (target channels seen this window but not the last).
    """
    prior_stats: Dict = (prior or {}).get("stats", {}) if prior else {}
    deltas: Dict = {}
    for key in _SCALAR_KEYS:
        deltas[key] = current.get(key, 0) - prior_stats.get(key, 0)
    cur_sources = set((current.get("by_source") or {}).keys())
    prev_sources = set((prior_stats.get("by_source") or {}).keys())
    deltas["new_sources"] = sorted(cur_sources - prev_sources)
    cur_channels = set((current.get("by_target_channel") or {}).keys())
    prev_channels = set((prior_stats.get("by_target_channel") or {}).keys())
    deltas["new_channels"] = sorted(cur_channels - prev_channels)
    return deltas


def write_reports(
    out_dir: Path,
    timestamp: datetime,
    window: str,
    units: List[str],
    current: Dict,
    deltas: Dict,
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
        "stats": current,
    }
    with json_path.open("w") as f:
        json.dump(json_data, f, indent=2, sort_keys=True)

    with md_path.open("w") as f:
        f.write(f"# Meshtastic re-emit soak — {timestamp.isoformat()}\n\n")
        f.write(f"**Window**: {window}  \n")
        f.write(f"**Units**: `{', '.join(units)}`\n\n")

        if current.get("reemitted_total", 0) == 0 and not any(
            current.get(k, 0) for k in _SCALAR_KEYS
        ):
            f.write(
                "No `meshtastic_reemit:` lines in the window. Bridge is "
                "either disabled, quiet, or the units aren't logging here.\n"
            )
            return md_path

        f.write("## Counters\n\n")
        f.write("| counter | this window | Δ vs prior |\n")
        f.write("|---|---:|---:|\n")
        for key in _SCALAR_KEYS:
            cur = current.get(key, 0)
            delta = deltas.get(key, 0)
            f.write(f"| `{key}` | {cur} | {delta:+d} |\n")

        if current.get("by_source"):
            f.write("\n## Re-emits by source identity\n\n")
            f.write("| source (hash8) | count |\n|---|---:|\n")
            for src, n in sorted(
                current["by_source"].items(), key=lambda x: -x[1]
            ):
                f.write(f"| `{src}` | {n} |\n")
            new_srcs = deltas.get("new_sources") or []
            if new_srcs:
                f.write(
                    "\n*New sources this window:* "
                    + ", ".join(f"`{s}`" for s in new_srcs)
                    + "\n"
                )

        if current.get("by_target_channel"):
            f.write("\n## Re-emits by target MeshCore channel\n\n")
            f.write("| target channel | count |\n|---|---:|\n")
            for ch, n in sorted(
                current["by_target_channel"].items(), key=lambda x: -x[1]
            ):
                f.write(f"| `ch{ch}` | {n} |\n")
            new_chs = deltas.get("new_channels") or []
            if new_chs:
                f.write(
                    "\n*New target channels this window:* "
                    + ", ".join(f"`ch{c}`" for c in new_chs)
                    + "\n"
                )
    return md_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate Meshtastic re-emit bridge log lines from journald "
            "into a per-run markdown + JSON report."
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
    parser.add_argument(
        "--max-gap-secs", type=int, default=DEFAULT_MAX_GAP_SECS,
        help=(
            "Liveness threshold in seconds. If the prior sidecar is older "
            "than this, log WARN and exit nonzero so cron mail catches it. "
            f"Default: {DEFAULT_MAX_GAP_SECS} (9h, = 1.5x default 6h cron)."
        ),
    )
    args = parser.parse_args(argv)

    units = list(args.unit) if args.unit else list(DEFAULT_UNITS)
    text = collect_journal(units, args.since, args.use_sudo)
    current = parse_journal(text)

    prior = find_prior_data(args.out_dir)
    deltas = diff_against_prior(current, prior)

    now = datetime.now(timezone.utc)

    stale_age = check_liveness(prior, now, args.max_gap_secs)
    if stale_age is not None:
        msg = (
            f"liveness: prior run was {stale_age / 3600:.1f}h ago "
            f"(>{args.max_gap_secs / 3600:.1f}h threshold) — "
            "cron may have stopped firing or daemon was wedged"
        )
        logger.warning(msg)
        sys.stderr.write(msg + "\n")

    md_path = write_reports(args.out_dir, now, args.since, units, current, deltas)

    print(
        "reemit_soak: reemitted={r} synth_ack_dropped={a} "
        "nested_dropped={n} handler_unavail={h} errors={e} "
        "warnings={w} restarts={s} -> {p}".format(
            r=current.get("reemitted_total", 0),
            a=current.get("filtered_synth_ack", 0),
            n=current.get("filtered_nested_bridge", 0),
            h=current.get("handler_unavailable", 0),
            e=current.get("errors", 0),
            w=current.get("warnings", 0),
            s=current.get("restarts", 0),
            p=md_path.name,
        )
    )

    return 1 if stale_age is not None else 0


if __name__ == "__main__":
    sys.exit(main())
