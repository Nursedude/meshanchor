#!/usr/bin/env python3
"""
rns_wedge_repro.py — minimal upstream-quality repro for rnsd shared-instance wedge.

Attaches to a running rnsd as a shared-instance client. No MeshAnchor or
MeshForge code in the loop. Runs configurable workloads and prints per-RPC
timings so a wedge produces a forensic instead of a black box.

Background:
    MeshAnchor wedges rnsd when running as a shared-instance client. Audit
    on 2026-05-06 ruled out announce-driven path_request floods (no such
    code path exists). Leading suspect now: contention on the shared-
    instance unix socket when the client process runs >1 LXMRouter.

What this script does:
    1. Writes a client RNS config to a tmp dir (share_instance = Yes,
       no interfaces, instance_name + rpc_key matching rnsd).
    2. Connects RNS as a shared-instance client.
    3. Optionally registers 1 or 2 LXMRouters with delivery identities
       (--routers 1 mimics the gateway alone; --routers 2 mimics gateway
       + broadcast bridge).
    4. Each router announces on a configurable cadence.
    5. A poll loop reads RNS.Transport.path_table on a configurable cadence
       (mimics PathTableMonitor) and times the call.
    6. Optionally sends periodic LXMF DMs to a target hash (mimics the
       subscribe-DM reply path that fires has_path/request_path/recall).
    7. Watchdog: if any RPC takes longer than --wedge-threshold seconds,
       log WEDGE and continue; if 3 consecutive WEDGEs occur, exit non-zero.

Run as the same user as rnsd, or as root if rnsd is system-managed and
the rpc_key file is root-readable.

Example:
    # Two-router workload (closest to MeshAnchor with broadcast bridge ON)
    sudo python3 scripts/rns_wedge_repro.py --routers 2 --duration 600

    # One-router workload (closest to MeshAnchor with bridge OFF)
    sudo python3 scripts/rns_wedge_repro.py --routers 1 --duration 600

    # No-router baseline (just shared-instance attach + path_table polls)
    sudo python3 scripts/rns_wedge_repro.py --routers 0 --duration 600
"""
from __future__ import annotations

import argparse
import logging
import signal as _signal_mod
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import RNS
import LXMF


_LOG_FMT = "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FMT, datefmt="%H:%M:%S")
log = logging.getLogger("rns_wedge_repro")


@contextmanager
def _suppress_signal_in_thread():
    """LXMRouter() registers SIGINT/SIGTERM in its constructor and crashes
    on non-main threads. We boot routers from background threads here so
    multiple identities behave like the real two-router shape."""
    if threading.current_thread() is threading.main_thread():
        yield
        return
    original = _signal_mod.signal

    def _safe_signal(signalnum, handler):
        return _signal_mod.SIG_DFL

    _signal_mod.signal = _safe_signal
    try:
        yield
    finally:
        _signal_mod.signal = original


def _read_rnsd_config_field(rnsd_config: Path, key: str) -> str | None:
    try:
        text = rnsd_config.read_text()
    except (OSError, PermissionError) as e:
        log.warning("Cannot read %s (%s); pass --%s explicitly",
                    rnsd_config, e, key.replace("_", "-"))
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip() or None
    return None


def _write_client_config(
    config_dir: Path, instance_name: str, rpc_key: str | None
) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    cfg = [
        "# rns_wedge_repro client config — DO NOT EDIT",
        "# Attaches to running rnsd as shared-instance client; no interfaces.",
        "",
        "[reticulum]",
        "share_instance = Yes",
        "shared_instance_port = 37428",
        "instance_control_port = 37429",
        f"instance_name = {instance_name}",
    ]
    if rpc_key:
        cfg.append(f"rpc_key = {rpc_key}")
    (config_dir / "config").write_text("\n".join(cfg) + "\n")


class TimedRPC:
    """Context manager: log call duration; flag wedge when > threshold."""
    def __init__(self, label: str, threshold_s: float, watchdog: "Watchdog"):
        self.label = label
        self.threshold_s = threshold_s
        self.watchdog = watchdog
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.monotonic() - self._t0
        if exc is not None:
            log.error("%s FAILED in %.3fs: %s: %s",
                      self.label, elapsed, exc_type.__name__, exc)
            self.watchdog.note_wedge(f"{self.label} raised {exc_type.__name__}")
            return False
        if elapsed > self.threshold_s:
            log.warning("WEDGE %s took %.3fs (>%.1fs threshold)",
                        self.label, elapsed, self.threshold_s)
            self.watchdog.note_wedge(f"{self.label} {elapsed:.1f}s")
        else:
            log.info("%s ok %.3fs", self.label, elapsed)
        return False


class Watchdog:
    def __init__(self, max_consecutive_wedges: int = 3):
        self._lock = threading.Lock()
        self._consecutive = 0
        self._max = max_consecutive_wedges
        self.tripped = False
        self.reason = ""

    def note_wedge(self, reason: str) -> None:
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self._max and not self.tripped:
                self.tripped = True
                self.reason = (
                    f"{self._consecutive} consecutive RPC wedges; last={reason}"
                )

    def note_ok(self) -> None:
        with self._lock:
            self._consecutive = 0


def boot_router(label: str, storage_dir: Path):
    """Boot one LXMRouter with a fresh delivery identity. Returns
    (label, router, identity, lxmf_source_destination, dest_hash)."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    with _suppress_signal_in_thread():
        router = LXMF.LXMRouter(storagepath=str(storage_dir))
    identity = RNS.Identity()
    src = router.register_delivery_identity(identity, display_name=label)
    if src is None:
        raise RuntimeError(f"router {label}: register_delivery_identity returned None")
    dest_hash = getattr(src, "hash", None)
    if dest_hash is None:
        raise RuntimeError(f"router {label}: source has no .hash")
    log.info("router %s identity registered: %s", label, dest_hash.hex())
    return label, router, identity, src, dest_hash


def announce_loop(stop: threading.Event, router: LXMF.LXMRouter,
                  dest_hash: bytes, label: str, interval: float,
                  watchdog: Watchdog, threshold: float) -> None:
    while not stop.is_set():
        if stop.wait(interval):
            return
        with TimedRPC(f"announce[{label}]", threshold, watchdog):
            router.announce(dest_hash)


def path_table_loop(stop: threading.Event, interval: float,
                    watchdog: Watchdog, threshold: float) -> None:
    while not stop.is_set():
        if stop.wait(interval):
            return
        with TimedRPC("path_table.read", threshold, watchdog):
            tbl = RNS.Transport.path_table
            n = len(tbl) if tbl else 0
        log.info("path_table size=%d", n)
        watchdog.note_ok()


def dm_loop(stop: threading.Event, router: LXMF.LXMRouter,
            lxmf_source, target_hex: str,
            interval: float, watchdog: Watchdog, threshold: float) -> None:
    try:
        target_hash = bytes.fromhex(target_hex)
    except ValueError:
        log.error("--send-dm value %r is not hex; disabling dm loop", target_hex)
        return

    while not stop.is_set():
        if stop.wait(interval):
            return
        try:
            with TimedRPC("dm.has_path", threshold, watchdog):
                has = RNS.Transport.has_path(target_hash)
            if not has:
                with TimedRPC("dm.request_path", threshold, watchdog):
                    RNS.Transport.request_path(target_hash)
                # Brief wait for path; do NOT block long
                for _ in range(30):
                    if stop.wait(0.1):
                        return
                    if RNS.Transport.has_path(target_hash):
                        break
            if not RNS.Transport.has_path(target_hash):
                log.info("dm target %s: no path yet, skipping send",
                         target_hash.hex()[:8])
                continue
            with TimedRPC("dm.identity_recall", threshold, watchdog):
                dest_identity = RNS.Identity.recall(target_hash)
            if dest_identity is None:
                log.info("dm target %s: identity not yet recalled, skipping",
                         target_hash.hex()[:8])
                continue
            destination = RNS.Destination(
                dest_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                "lxmf", "delivery",
            )
            lxm = LXMF.LXMessage(
                destination,
                lxmf_source,
                f"wedge-repro ping {time.time():.0f}",
                "wedge-repro",
            )
            with TimedRPC("dm.handle_outbound", threshold, watchdog):
                router.handle_outbound(lxm)
        except Exception as e:
            log.error("dm loop error: %s: %s", type(e).__name__, e)
            watchdog.note_wedge(f"dm loop {type(e).__name__}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rnsd-config", default="/etc/reticulum/config",
                    help="Path to running rnsd's config (read instance_name + rpc_key)")
    ap.add_argument("--instance-name", default=None,
                    help="Override instance_name (else read from --rnsd-config)")
    ap.add_argument("--rpc-key", default=None,
                    help="Override rpc_key (else read from --rnsd-config)")
    ap.add_argument("--config-dir", default=None,
                    help="Where to write the client RNS config (default: tmp)")
    ap.add_argument("--routers", type=int, default=1, choices=[0, 1, 2],
                    help="0=no LXMRouter, 1=one, 2=two (default: 1)")
    ap.add_argument("--announce-interval", type=float, default=60.0,
                    help="Seconds between own announces (per router)")
    ap.add_argument("--poll-interval", type=float, default=10.0,
                    help="Seconds between path_table polls")
    ap.add_argument("--send-dm", default=None,
                    help="If set, send periodic LXMF DMs to this destination hash (hex)")
    ap.add_argument("--dm-interval", type=float, default=30.0,
                    help="Seconds between outbound DMs (when --send-dm is set)")
    ap.add_argument("--duration", type=float, default=600.0,
                    help="Total run time in seconds (default 10m)")
    ap.add_argument("--wedge-threshold", type=float, default=5.0,
                    help="RPC duration over this counts as a wedge (default 5s)")
    args = ap.parse_args()

    rnsd_cfg = Path(args.rnsd_config)
    instance_name = args.instance_name or _read_rnsd_config_field(rnsd_cfg, "instance_name") or "default"
    rpc_key = args.rpc_key or _read_rnsd_config_field(rnsd_cfg, "rpc_key")
    log.info("Using instance_name=%r rpc_key=%s",
             instance_name, "yes" if rpc_key else "no")

    if args.config_dir:
        client_dir = Path(args.config_dir)
    else:
        client_dir = Path(tempfile.mkdtemp(prefix="rns_wedge_repro_"))
    log.info("Client config dir: %s", client_dir)
    _write_client_config(client_dir, instance_name, rpc_key)

    log.info("Connecting to rnsd as shared-instance client…")
    t0 = time.monotonic()
    RNS.Reticulum(configdir=str(client_dir))
    log.info("RNS attached in %.2fs (transport_enabled=%s)",
             time.monotonic() - t0, RNS.Transport.identity is not None)

    watchdog = Watchdog(max_consecutive_wedges=3)
    stop = threading.Event()
    threads: list[threading.Thread] = []

    routers = []  # list of (label, router, identity, lxmf_source, dest_hash)
    for i in range(args.routers):
        label = f"router{i + 1}"
        try:
            routers.append(
                boot_router(label, client_dir / f"lxmf_{label}_storage")
            )
        except Exception as e:
            log.error("router %s boot failed: %s", label, e)
            return 2

    for label, r, _ident, _src, dh in routers:
        t = threading.Thread(
            target=announce_loop, name=f"announce-{label}",
            args=(stop, r, dh, label, args.announce_interval,
                  watchdog, args.wedge_threshold),
            daemon=True,
        )
        t.start()
        threads.append(t)

    poll_t = threading.Thread(
        target=path_table_loop, name="path-poll",
        args=(stop, args.poll_interval, watchdog, args.wedge_threshold),
        daemon=True,
    )
    poll_t.start()
    threads.append(poll_t)

    if args.send_dm and routers:
        # Use the first router's source destination for outbound DMs.
        _label, r, _ident, src, _dh = routers[0]
        dm_t = threading.Thread(
            target=dm_loop, name="dm",
            args=(stop, r, src, args.send_dm,
                  args.dm_interval, watchdog, args.wedge_threshold),
            daemon=True,
        )
        dm_t.start()
        threads.append(dm_t)

    deadline = time.monotonic() + args.duration
    exit_code = 0
    try:
        while time.monotonic() < deadline:
            if watchdog.tripped:
                log.error("WATCHDOG TRIPPED: %s", watchdog.reason)
                exit_code = 3
                break
            time.sleep(1.0)
        else:
            log.info("Duration elapsed; clean exit")
    except KeyboardInterrupt:
        log.info("Interrupt; exiting")

    stop.set()
    for t in threads:
        t.join(timeout=3.0)

    log.info("Done. Final wedge state: tripped=%s reason=%r",
             watchdog.tripped, watchdog.reason)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
