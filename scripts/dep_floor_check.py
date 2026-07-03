#!/usr/bin/env python3
"""dep_floor_check.py — one-shot dep version-floor check for THIS interpreter.

The remote leg of the mini uplink (2026-07-03): the MeshForge manager box's
``ma_health_uplink.sh`` cron ssh-invokes this under the SAME python that runs
the MeshAnchor services (the venv — the consumer-of-record), because the
resident ``ActiveHealthProbe`` does not run under ``core.orchestrator``
deployments, so a cron-driven one-shot is the honest check host on such boxes
(same pattern as the fleet tracer / ntfy loopback: an active probe run on
cadence, not a resident thread).

Invoke with the interpreter under test:

    /opt/meshanchor/venv/bin/python /opt/meshanchor/scripts/dep_floor_check.py

Prints one line: ``OK <reason>`` / ``FAIL <reason>``.
Exit 0 = healthy or honestly indeterminate (same semantics as
``check_dep_version_floor`` — an unreadable floor or a not-importable package
must not read as drift). Exit 1 = a concrete below-floor fact.
Exit 2 = this script itself could not run the check (import failure) — the
caller must treat that as a failure of observation, not health.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))


def main() -> int:
    try:
        from utils.active_health_probe import ActiveHealthProbe
    except Exception as e:
        print(f"FAIL check_unavailable: {type(e).__name__}: {e}")
        return 2
    result = ActiveHealthProbe(interval=3600).check_dep_version_floor()
    print(f"{'OK' if result.healthy else 'FAIL'} {result.reason}")
    return 0 if result.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
