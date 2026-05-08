"""Operator-placed positions for MeshCore nodes.

MeshCore advertisements do not carry GPS, so any MeshCore node the
operator wants to see on the map needs a position pinned by hand.
This module persists those pins to JSON.

The store is keyed by MeshCore pubkey hex (lower-case). Same pubkey
the gateway handler surfaces in `tracker.get_meshcore_nodes()` —
joining placements with discovered contacts is just a dict lookup.

Storage:
  ~/.config/meshanchor/meshcore_positions.json
  {
    "<pubkey_hex>": {
      "lat": 21.3069,
      "lon": -157.8583,
      "alt": 12.0,            # optional meters MSL
      "name": "Lat",          # optional operator override
      "notes": "QTH HAM",     # optional free-form
      "set_at": "2026-05-07T18:30:00+00:00"
    }
  }

Writes are atomic (write to .tmp + os.replace) and guarded by a
process-local lock. Reads always go to disk so multiple processes
stay coherent without a daemon.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

_DEFAULT_FILENAME = "meshcore_positions.json"


def _normalize_pubkey(pubkey: str) -> str:
    """Lower-case hex, strip whitespace and any 0x prefix."""
    if pubkey is None:
        return ""
    p = pubkey.strip().lower()
    if p.startswith("0x"):
        p = p[2:]
    return p


def _validate_coords(lat: float, lon: float) -> None:
    if not (-90.0 <= float(lat) <= 90.0):
        raise ValueError(f"latitude out of range: {lat}")
    if not (-180.0 <= float(lon) <= 180.0):
        raise ValueError(f"longitude out of range: {lon}")


class MeshCorePositionStore:
    """JSON-backed pubkey -> placement record store.

    Construct with no args to use the default path under
    ``get_real_user_home()/.config/meshanchor``. Tests pass an
    explicit ``path=`` to keep them isolated.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            path = get_real_user_home() / ".config" / "meshanchor" / _DEFAULT_FILENAME
        self.path: Path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, Dict]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(
                    "meshcore_positions.json is not a dict (got %s); ignoring",
                    type(data).__name__,
                )
                return {}
            return data
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("meshcore_positions.json read error: %s", e)
            return {}

    def _atomic_write(self, data: Dict[str, Dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory so os.replace is atomic
        # on the same filesystem, then rename. dir=parent ensures cross-fs
        # rename can't break atomicity.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".meshcore_positions.", suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            # Best-effort cleanup if anything fails before the rename
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get(self, pubkey: str) -> Optional[Dict]:
        """Return the placement record for ``pubkey`` or None if unset."""
        key = _normalize_pubkey(pubkey)
        if not key:
            return None
        with self._lock:
            return self._read().get(key)

    def set(
        self,
        pubkey: str,
        lat: float,
        lon: float,
        alt: Optional[float] = None,
        name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict:
        """Place ``pubkey`` at (lat, lon). Returns the stored record."""
        key = _normalize_pubkey(pubkey)
        if not key:
            raise ValueError("pubkey is required")
        _validate_coords(lat, lon)
        record = {
            "lat": float(lat),
            "lon": float(lon),
            "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if alt is not None:
            record["alt"] = float(alt)
        if name:
            record["name"] = str(name).strip()
        if notes:
            record["notes"] = str(notes).strip()
        with self._lock:
            data = self._read()
            data[key] = record
            self._atomic_write(data)
        return record

    def delete(self, pubkey: str) -> bool:
        """Remove ``pubkey`` from the store. Returns True if anything was removed."""
        key = _normalize_pubkey(pubkey)
        if not key:
            return False
        with self._lock:
            data = self._read()
            if key not in data:
                return False
            data.pop(key)
            self._atomic_write(data)
            return True

    def list(self) -> Dict[str, Dict]:
        """Return a copy of all placements (pubkey -> record)."""
        with self._lock:
            return dict(self._read())

    def __len__(self) -> int:
        with self._lock:
            return len(self._read())


_default_store: Optional[MeshCorePositionStore] = None
_default_store_lock = threading.Lock()


def get_position_store() -> MeshCorePositionStore:
    """Return a process-wide MeshCorePositionStore using the default path."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = MeshCorePositionStore()
        return _default_store


def reset_default_store() -> None:
    """Reset the cached default store. Test-only."""
    global _default_store
    with _default_store_lock:
        _default_store = None


__all__ = [
    "MeshCorePositionStore",
    "get_position_store",
    "reset_default_store",
]
