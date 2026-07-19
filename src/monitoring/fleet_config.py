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
import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


DEFAULT_PEER_PORT = 5001
"""Map service default port — the canonical NOC layout (VolcanoAI dev
uses :5002, but the operator overrides per-peer in the config)."""

DEFAULT_FEDERATION_FRESH_WINDOW_S = 7200
"""Show federation peers heard within this window. 2h is enough to
cover routine LXMF announce intervals without flooding the panel with
stale entries on a quiet network."""


# ── Positive self-identity check (2026-07-19 non_self_peers fix) ─────────
_IPV4_LITERAL_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

# Memoized per-host verdicts — resolution can be slow on dead DNS and
# non_self_peers sits in the dashboard poll path. TTL keeps a NIC/DNS
# change from sticking forever.
_SELF_IP_CACHE: Dict[str, Tuple[float, bool]] = {}
_SELF_IP_CACHE_TTL_S = 300.0


def _resolve_ips(host: str) -> List[str]:
    """Best-effort resolution of ``host`` to IPs; [] on failure (identity
    unknown). An IP literal resolves to itself instantly — no DNS."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        return sorted({i[4][0] for i in infos})
    except OSError:
        return []


def _ip_is_local(ip: str) -> bool:
    """Positive ownership test: binding to an address this box owns
    succeeds; binding to a remote address fails (EADDRNOTAVAIL). No
    packets are sent."""
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        s = socket.socket(fam, socket.SOCK_DGRAM)
        try:
            s.bind((ip, 0))
            return True
        finally:
            s.close()
    except OSError:
        return False


def _host_is_self(host: str) -> bool:
    """True only when ``host`` POSITIVELY resolves to an address this box
    owns. Resolution failure → False (identity unknown ≠ self): the caller
    then KEEPS the peer, so a genuinely remote box can never be silently
    dropped by a name-shape guess — the never-a-dropped-row contract."""
    now = time.monotonic()
    hit = _SELF_IP_CACHE.get(host)
    if hit is not None and (now - hit[0]) < _SELF_IP_CACHE_TTL_S:
        return hit[1]
    ips = _resolve_ips(host)
    verdict = any(_ip_is_local(ip) for ip in ips)
    _SELF_IP_CACHE[host] = (now, verdict)
    return verdict


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
        so we'd double-count if `host` resolves to localhost.

        Self-detection is by POSITIVE identity, not name shape (2026-07-19
        adversarial-review fix): the old bare-name stripper silently DROPPED
        a genuinely remote peer whose bare name matched this host (e.g. peer
        ``noc.remote-site.lan`` polled from a box named ``noc``) — violating
        the truth schema's never-a-dropped-row promise — while an IP-written
        self entry slipped through and double-counted. Ambiguous shapes
        (bare-name collision, IP-literal host) now get an identity check:
        does the host resolve to an address THIS box owns (bind test)? When
        identity cannot be established (resolution failure), the peer is
        KEPT — a true-self kept shows up as a visible duplicate row, while a
        true-remote dropped simply vanishes; fail-visible wins.
        """
        my_host = (hostname or socket.gethostname()).lower()
        out = []
        for peer in self.peers:
            ph = (peer.host or "").lower()
            if ph in (my_host, "localhost", "127.0.0.1", "::1", ""):
                continue
            bare_collision = ph.split(".", 1)[0] == my_host.split(".", 1)[0]
            ip_literal = bool(_IPV4_LITERAL_RE.match(ph)) or ":" in ph
            if (bare_collision or ip_literal) and _host_is_self(ph):
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
