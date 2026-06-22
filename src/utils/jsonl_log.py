"""Append-only JSONL writer (rotate + torn-tail repair + swallow-and-report).

Ported from MeshForge's ``mini_dudeai.history.append_jsonl`` so the mesh-oracle
audit log has the same posture in MeshAnchor (which has no ``mini_dudeai``). One
line per record; bounded by ``max_bytes`` via line-oriented rotation; never
raises — a disk/perms problem returns an error STRING the caller can surface as
a witness (honest_failure_modes #9), it must never crash an observation loop.

Kept OUT of the ``oracle`` package on purpose: the oracle module is read-only
(test-pinned), so the actual write lives here + in the gateway log closure.
"""
from __future__ import annotations

import json
import os

DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB (oracle continuity log)


def _rotate_if_needed(path: str, max_bytes: int) -> str | None:
    """Line-oriented retention: when over ``max_bytes``, keep the newest lines
    that fit half the cap (so we don't re-rotate on the next write). Atomic
    (tmp + os.replace), line-oriented (stays valid JSONL). Swallows OSError into
    a returned string; a failed rotation leaves the file for the append to try.
    """
    if max_bytes <= 0:
        return None
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"
    if size <= max_bytes:
        return None
    target = max_bytes // 2
    try:
        with open(path, "rb") as f:
            data = f.read()
        lines = data.splitlines(keepends=True)
        # Terminate a torn final line so it stays an isolated malformed line
        # readers skip, instead of fusing with the next appended record.
        if lines and not lines[-1].endswith(b"\n"):
            lines[-1] += b"\n"
        kept: list[bytes] = []
        total = 0
        for line in reversed(lines):
            if kept and total + len(line) > target:
                break
            kept.append(line)
            total += len(line)
        kept.reverse()
        tmp = path + ".rot.tmp"
        with open(tmp, "wb") as f:
            f.writelines(kept)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def _repair_torn_tail(path: str) -> None:
    """If the last byte isn't a newline (crash mid-append), add one — so the
    NEXT append can't fuse two records into one unparseable line. Best-effort."""
    try:
        with open(path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
    except OSError:
        pass


def append_jsonl(path: str, entries: list[dict], max_bytes: int = DEFAULT_MAX_BYTES) -> str | None:
    """Append ``entries`` as JSON lines. Returns None on success, error str on
    failure. Rotates first (newest survives), repairs a torn tail, then appends.
    Never raises.
    """
    if not entries:
        return None
    _rotate_if_needed(path, max_bytes)
    if os.path.exists(path):
        _repair_torn_tail(path)
    try:
        with open(path, "a") as f:
            for e in entries:
                f.write(json.dumps(e, default=str) + "\n")
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"
