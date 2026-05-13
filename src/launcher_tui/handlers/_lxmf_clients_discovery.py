"""Discover LXMF identity hashes for local clients on the operator's pi.

Used by the LXMF Broadcast Bridge TUI handler to populate a "Subscribe
Local Client" picker — the operator selects which local LXMF client
(NomadNet, MeshChatX, etc.) should receive MeshCore broadcast fan-outs.

Discovery is best-effort. A probe returning `[]` is normal; the operator
can always fall back to the JSON config seam (see `_probe_user_config`)
to register a hash that this module can't auto-detect.

Adding a new probe = adding a function below and appending it to the
`_PROBES` list. No registration framework needed at this scale.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, NamedTuple

from utils.paths import MeshChatXPaths, get_real_user_home

logger = logging.getLogger(__name__)


class LocalLXMFClient(NamedTuple):
    """A locally-discovered LXMF client identity."""

    name: str           # human label, e.g. "NomadNet"
    hash: str           # 16-char hex LXMF destination hash (lowercased)
    source: str         # where the hash was found, e.g. "~/.nomadnetwork/logfile"


# Regex shared with `_gateway_preflight_template.py` — NomadNet logs its
# LXMF destination hash on every router start. Last match wins (a fresh
# restart re-announces with the same hash, but the line is appended).
_NOMADNET_HASH_RE = re.compile(
    r"LXMF Router ready to receive on: <([0-9a-f]+)>"
)


def _probe_nomadnet() -> List[LocalLXMFClient]:
    """Discover NomadNet identity hash from its logfile.

    NomadNet stores its config + logfile under `~/.nomadnetwork/`. The
    LXMF destination hash is logged on every router boot; last match
    wins so a fresh restart picks up the current identity even if old
    log lines reference a previous (corrupted) one.
    """
    logfile = get_real_user_home() / ".nomadnetwork" / "logfile"
    if not logfile.exists():
        return []
    try:
        content = logfile.read_text(errors="ignore")
    except OSError as e:
        logger.debug("NomadNet logfile unreadable: %s", e)
        return []
    matches = _NOMADNET_HASH_RE.findall(content)
    if not matches:
        return []
    return [
        LocalLXMFClient(
            name="NomadNet",
            hash=matches[-1].lower(),
            source=str(logfile),
        )
    ]


def _probe_meshchatx() -> List[LocalLXMFClient]:
    """Discover MeshChatX identity hash — best-effort, returns [] today.

    MeshChatX (third-party LXMF web client) stores its identity under
    `~/.local/share/meshchatx/` but doesn't expose the destination hash
    in a documented file or logline we can scrape reliably. Operators
    who want their MeshChatX hash in the bridge can drop it into the
    JSON seam (`_probe_user_config`).

    Left as a function rather than removed so future MeshChatX versions
    that expose `~/.local/share/meshchatx/lxmf_hash.txt` (or similar)
    can be picked up by editing here without touching the call site.
    """
    storage = MeshChatXPaths.get_storage_dir()
    if not storage.is_dir():
        return []

    # Conventional names MeshChatX might write the hash to in the
    # future — try them all, ignore failures.
    for fname in ("lxmf_hash", "lxmf_hash.txt", "destination_hash"):
        path = storage / fname
        if not path.is_file():
            continue
        try:
            raw = path.read_text(errors="ignore").strip()
        except OSError:
            continue
        if re.fullmatch(r"[0-9a-fA-F]{16,32}", raw):
            return [
                LocalLXMFClient(
                    name="MeshChatX",
                    hash=raw.lower(),
                    source=str(path),
                )
            ]
    return []


def _probe_user_config() -> List[LocalLXMFClient]:
    """Extension seam — operator-managed JSON list of local clients.

    Path: `~/.config/meshanchor/local_lxmf_clients.json`
    Shape: `[{"name": "...", "hash": "16hex"}, ...]`

    Lets the operator register hashes for clients we can't auto-detect
    (MeshChatX today, future third-party LXMF apps) without writing code.
    """
    config = (
        get_real_user_home() / ".config" / "meshanchor" / "local_lxmf_clients.json"
    )
    if not config.is_file():
        return []
    try:
        data = json.loads(config.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("local_lxmf_clients.json unreadable/invalid: %s", e)
        return []
    if not isinstance(data, list):
        logger.warning(
            "local_lxmf_clients.json must be a JSON list; got %s",
            type(data).__name__,
        )
        return []

    out: List[LocalLXMFClient] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        raw_hash = entry.get("hash")
        if not isinstance(name, str) or not isinstance(raw_hash, str):
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{16,32}", raw_hash.strip()):
            continue
        out.append(
            LocalLXMFClient(
                name=name,
                hash=raw_hash.strip().lower(),
                source=str(config),
            )
        )
    return out


_PROBES = (_probe_nomadnet, _probe_meshchatx, _probe_user_config)


def discover_local_lxmf_clients() -> List[LocalLXMFClient]:
    """Run every probe, dedup on hash, return ordered list.

    Order matters: the first probe to surface a given hash wins the
    `name`/`source` columns. NomadNet runs first so the operator's
    canonical client gets the canonical label.
    """
    seen: set = set()
    out: List[LocalLXMFClient] = []
    for probe in _PROBES:
        try:
            found = probe()
        except Exception as e:
            logger.warning("LXMF client probe %s failed: %s", probe.__name__, e)
            continue
        for client in found:
            if client.hash in seen:
                continue
            seen.add(client.hash)
            out.append(client)
    return out
