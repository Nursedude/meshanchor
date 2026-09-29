"""The dialled-Host half of the map's trust gate (port of MF F2, 2026-09-28).

MF keeps every trust rule in its ``utils/_map_trust_gate.py``; MeshAnchor's
IP/Origin rules still live in ``map_http_handler.py`` (MA never took MF's
WebSocket gate, 151eaa00), so only the Host rule and the local-name vocabulary
it needs live here. ``_local_only_name``, ``_host_header_trusted`` and
``_same_local_host`` are
byte-identical to MF's — keep them that way.
"""
import ipaddress
import re
from typing import Optional

#: Name suffixes only the browser's LOCAL resolver can answer (mDNS, RFC 8375
#: home.arpa, ICANN-reserved .internal, AREDN's local.mesh). A public name is
#: attacker-controllable (DNS rebinding), so it never earns same-host trust.
_LOCAL_NAME_SUFFIXES = (".local", ".home.arpa", ".internal", ".local.mesh")
_LABEL_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')


def _local_only_name(name: str) -> bool:
    """True for a hostname no public DNS can hand an attacker: one label
    (``moc``, resolved from /etc/hosts or the LAN's own DNS) or a reserved
    local suffix. IP literals are handled by the /24 rule, never here."""
    name = (name or "").lower().rstrip(".")
    labels = name.split(".")
    if not name or not all(_LABEL_RE.match(lbl) for lbl in labels):
        return False
    if len(labels) == 1:
        return not name.isdigit()
    return name.endswith(_LOCAL_NAME_SUFFIXES)


def _host_header_trusted(host: Optional[str]) -> bool:
    """The name the client dialled is one DNS rebinding cannot hand an attacker.

    Rebinding: a LAN browser opens ``http://evil.example:5000``, the attacker
    re-points ``evil.example`` at this box, and the page's SAME-ORIGIN fetch
    arrives from the victim's trusted LAN address — the IP gate admits it. The
    one thing the attacker cannot change is the Host the browser sends: their
    own public name. So a trusted read also needs Host to be an IP literal, or
    a local-only name (``_local_only_name``: single label, ``.local``,
    ``.home.arpa``, ``.internal``, ``.local.mesh``) — the same rule the
    WebSocket's same-host Origin check uses (Fable review 2026-09-28, F2).

    No Host header at all = not a browser (HTTP/1.0 tooling) → trusted; an
    EMPTY or malformed one is refused.
    """
    if host is None:
        return True
    host = host.strip()
    m = re.match(r'^\[([0-9A-Fa-f:.]+)\](?::\d+)?$', host)       # [v6]:port
    if m:
        name = m.group(1)
    else:
        name = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return _local_only_name(name)


def _same_local_host(origin: str, request_host: Optional[str],
                     page_port: Optional[int]) -> bool:
    """The page and the socket were reached by the SAME local-only name: the
    map opened as ``http://moc:5000`` connects ``ws://moc:5001`` (the status
    endpoint builds that URL from the page's Host), so Origin names the host
    the browser itself dialled. Found 2026-09-25: the IP-prefix list refused
    every map opened by hostname (moc: IP origin 101, hostname origin 403)."""
    if not request_host or page_port is None:
        return False
    m = re.match(r'^http://([^/:]+):(\d+)$', origin or "")
    if not m or int(m.group(2)) != int(page_port):
        return False
    name = m.group(1).lower()
    dialled = request_host.rsplit(":", 1)[0].lower() if ":" in request_host else request_host.lower()
    return name == dialled and _local_only_name(name)


class HostRuleMixin:
    """The Host rule at dispatch, for EVERY route (port of MF Option A,
    2026-09-28, Fable re-review #2): 15 ungated GETs still answered a
    DNS-rebinding page while only ``_reject_if_untrusted`` checked Host.
    ``/healthz`` + ``/metrics`` are exempt (no mesh data; scrapers). Kept here,
    not in the handler, for the MF025 size cap. Needs ``self._serve_json``."""

    #: Paths answered whatever Host the client dialled: liveness + scrape
    #: endpoints that carry no mesh data and are polled by tooling that may
    #: address a box by any name (Prometheus, uptime monitors).
    _HOST_EXEMPT_PATHS = frozenset({'/healthz', '/metrics'})

    def _serve_host_refusal(self, host) -> None:
        self._serve_json(
            {"error": "forbidden",
             "detail": (f"This box does not answer requests addressed to "
                        f"{host!r}: open it by IP, by its bare name, or by a "
                        f"local name (.local / .home.arpa / .internal / "
                        f".local.mesh). A public name here is what a DNS-"
                        f"rebinding page would send. Behind a reverse proxy, "
                        f"forward the upstream's own host (docs/REST_API.md).")},
            status=403)

    def _refuse_untrusted_host(self, path_only: Optional[str] = None) -> bool:
        """Send 403 + return True when the request was addressed to a name
        DNS rebinding could hand an attacker."""
        if path_only is None:
            from urllib.parse import urlparse
            path_only = urlparse(self.path).path.rstrip('/')
        if (path_only or '/') in self._HOST_EXEMPT_PATHS:
            return False
        headers = getattr(self, 'headers', None)
        host = headers.get('Host') if headers is not None else None
        if _host_header_trusted(host):
            return False
        self._serve_host_refusal(host)
        return True

    def do_HEAD(self):
        """HEAD on the static tree (stdlib) — behind the same Host rule."""
        if self._refuse_untrusted_host():
            return
        super().do_HEAD()
