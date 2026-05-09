"""Peer configuration for the multi-host fleet monitor.

Loads `~/.config/meshanchor/fleet.json` (override via env var
`MESHANCHOR_FLEET_CONFIG`). The file lists the peer hosts the rollup
endpoint should poll plus federation-scrape settings. Schema is
intentionally narrow — adding a peer is a 3-field operation:

    {
      "peers": [
        {"name": "meshanchor-server", "host": "meshanchor-server.local",
         "port": 5001, "kind": "noc"},
        {"name": "VolcanoAI", "host": "volcanoai.local",
         "port": 5002, "kind": "dev"}
      ],
      "federation": {
        "scrape_rns_announces": true,
        "fresh_window_s": 7200
      }
    }

A missing or unparseable file returns an empty config — the rollup
endpoint then degrades to "self-only" rather than 5xx-ing the
dashboard. Path resolution honors `get_real_user_home()` (MF001) so
running under sudo doesn't read /root/.config.
"""
from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


DEFAULT_PEER_PORT = 5001
"""Map service default port — the canonical NOC layout (VolcanoAI dev
uses :5002, but the operator overrides per-peer in the config)."""

DEFAULT_FEDERATION_FRESH_WINDOW_S = 7200
"""Show federation peers heard within this window. 2h is enough to
cover routine LXMF announce intervals without flooding the panel with
stale entries on a quiet network."""


@dataclass
class PeerConfig:
    """One entry in `fleet.json`'s `peers` list."""

    name: str
    """Display name. Operator-facing; used by the dashboard + TUI."""

    host: str
    """Hostname or IP. Used both for the HTTP fetch and for self-
    detection (matched against `socket.gethostname()`)."""

    port: int = DEFAULT_PEER_PORT
    """Map service port. Default 5001 (canonical NOC). Dev surfaces
    that run on a non-standard port (e.g. VolcanoAI :5002) override."""

    kind: str = "noc"
    """Operator-defined classifier — typical values "noc", "dev",
    "field". Surfaces in the dashboard so the operator can sort their
    own boxes vs. peer NOCs at a glance."""

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class FederationConfig:
    """Settings for the RNS-side federation peer view.

    The view itself is computed from the map service's directory
    cache (filtered to `network == "rns"`), not from a separate RNS
    scrape — so these flags are about *display*, not data acquisition.
    """

    scrape_rns_announces: bool = True
    """When False, the federation panel is disabled. Useful for dev
    boxes that don't run RNS (the directory will be empty anyway)."""

    fresh_window_s: int = DEFAULT_FEDERATION_FRESH_WINDOW_S


@dataclass
class FleetConfig:
    """Top-level config object. Always returns — defaults fill in if
    the file is missing or partially malformed."""

    peers: List[PeerConfig] = field(default_factory=list)
    federation: FederationConfig = field(default_factory=FederationConfig)
    source_path: Optional[str] = None
    """Where the config was loaded from. Surfaces in /fleet/rollup so
    the dashboard can display it (helps debug "why isn't peer X
    showing up?")."""

    parse_error: Optional[str] = None
    """Set when the config file existed but couldn't be parsed.
    Surfaces in /fleet/rollup `errors[]` so a bad edit is immediately
    visible in the dashboard."""

    def non_self_peers(self, *, hostname: Optional[str] = None) -> List[PeerConfig]:
        """Peers excluding any that match this host — the rollup
        endpoint runs `collect_local_snapshot()` for self separately,
        so we'd double-count if `host` resolves to localhost."""
        my_host = (hostname or socket.gethostname()).lower()
        out = []
        for peer in self.peers:
            ph = (peer.host or "").lower()
            if ph in (my_host, "localhost", "127.0.0.1", "::1", ""):
                continue
            # Strip a `.local`/domain suffix and compare bare names too;
            # mDNS hosts often differ by suffix only (`volcanoai.local`
            # vs `VolcanoAI`).
            if ph.split(".", 1)[0] == my_host.split(".", 1)[0]:
                continue
            out.append(peer)
        return out


# ──────────────────────────────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────────────────────────────


def get_fleet_config_path() -> Path:
    """Resolve the on-disk config path. Honors MESHANCHOR_FLEET_CONFIG
    override; falls back to get_real_user_home() (MF001 — never use the
    stdlib's home resolver, since under sudo it returns /root)."""
    override = os.environ.get("MESHANCHOR_FLEET_CONFIG")
    if override:
        return Path(override)
    from utils.paths import get_real_user_home
    return get_real_user_home() / ".config" / "meshanchor" / "fleet.json"


def _parse_peer(raw: Dict[str, Any]) -> Optional[PeerConfig]:
    """Best-effort peer parse. Drops malformed entries silently — the
    overall config still loads."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    host = raw.get("host")
    if not isinstance(name, str) or not isinstance(host, str):
        return None
    port = raw.get("port", DEFAULT_PEER_PORT)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PEER_PORT
    kind = raw.get("kind", "noc")
    if not isinstance(kind, str):
        kind = "noc"
    return PeerConfig(name=name.strip(), host=host.strip(), port=port, kind=kind)


def _parse_federation(raw: Any) -> FederationConfig:
    if not isinstance(raw, dict):
        return FederationConfig()
    fresh = raw.get("fresh_window_s", DEFAULT_FEDERATION_FRESH_WINDOW_S)
    try:
        fresh = int(fresh)
    except (TypeError, ValueError):
        fresh = DEFAULT_FEDERATION_FRESH_WINDOW_S
    return FederationConfig(
        scrape_rns_announces=bool(raw.get("scrape_rns_announces", True)),
        fresh_window_s=fresh,
    )


def load_fleet_config(path: Optional[Path] = None) -> FleetConfig:
    """Load the fleet config, with safe defaults when the file is
    missing or malformed. Never raises; the dashboard always renders."""
    p = path or get_fleet_config_path()
    cfg = FleetConfig(source_path=str(p))

    if not p.exists():
        return cfg

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        cfg.parse_error = f"{type(e).__name__}: {e}"
        logger.warning("fleet.json parse failed: %s", e)
        return cfg

    if not isinstance(raw, dict):
        cfg.parse_error = "top-level JSON must be an object"
        return cfg

    peers_raw = raw.get("peers", []) or []
    if isinstance(peers_raw, list):
        for entry in peers_raw:
            peer = _parse_peer(entry)
            if peer is not None:
                cfg.peers.append(peer)

    cfg.federation = _parse_federation(raw.get("federation"))
    return cfg


__all__ = [
    "FleetConfig",
    "FederationConfig",
    "PeerConfig",
    "DEFAULT_PEER_PORT",
    "DEFAULT_FEDERATION_FRESH_WINDOW_S",
    "get_fleet_config_path",
    "load_fleet_config",
]
