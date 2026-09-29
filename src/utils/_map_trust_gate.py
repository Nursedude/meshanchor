"""The dialled-Host half of the map's trust gate (port of MF F2, 2026-09-28).

MF keeps every trust rule in its ``utils/_map_trust_gate.py``; MeshAnchor's
IP/Origin rules still live in ``map_http_handler.py`` (MA never took MF's
WebSocket gate, 151eaa00), so only the Host rule and the local-name vocabulary
it needs live here. ``_local_only_name`` and ``_host_header_trusted`` are
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
