"""The per-app half of the NOC read grant seam — MeshAnchor's values.

``utils/box_truth{,_reader,_grants,_center,_collect}.py`` are byte-identical
with MeshForge (the lead repo; ``scripts/parity_check.py`` BYTE_IDENTICAL).
This module is the ONE file of the arc that differs per repo: both repos
define the same NAMES (pinned by ``tests/test_box_truth_app_seam.py``), each
with its own VALUES. A shared module never names an app; it asks this module.

Two kinds of value live here, and the difference matters:

* **this box's own app** — where its watchdog / mini / deployment state is,
  which units are its own, the export a grant written HERE forces, the
  center's key and targets file when this box is a center;
* **the target's app** — a center pushing a grant runs the TARGET's grant
  CLI, and a target may run either app (or both). ``GRANT_CLI_SEARCH`` is
  tried in order on the target: this app's install first, then the
  sibling's. Never a third directory.
"""
from __future__ import annotations

from pathlib import Path

APP = "meshanchor"
INSTALL_DIR = "/opt/meshanchor"
#: ``~/.config/<d>``, ``~/.local/share/<d>``, ``~/.local/state/<d>``.
CONFIG_DIRNAME = "meshanchor"
#: The box-HEALTH watchdog's state file. MeshAnchor runs no box-health
#: watchdog of its own (``meshanchor-fleet-watchdog`` is a collector silence
#: detector and writes no such file); the fleet's box watchdog organ is
#: MeshForge's, and on a box running both apps it writes HERE (measured on
#: the MA box 2026-10-14). On a MeshAnchor-only box the file is absent by
#: design — the export reports the section absent, never invents one.
WATCHDOG_PATH = Path("/var/lib/meshforge/watchdog.json")
#: This app's own system units. ``meshanchor.service`` (the orchestrator) is
#: BARE — a ``meshanchor-*`` glob would miss it.
UNIT_GLOB = "meshanchor*.service"
#: mini-dudeai's state file, relative to the operator's home (MA moved off
#: the home-dir convention 2026-07-24; same path mini_dudeai._util.PEER_APPS
#: names in MeshForge).
MINI_STATE_RELPATH = ".local/share/meshanchor/mini/state.json"
#: A center's restricted read key: ``~/.ssh/<this>``. Per app, so a box
#: running both centers holds two keys and each target two grant lines.
NOC_KEY_BASENAME = "meshanchor_noc_ed25519"
#: Where a center looks for the TARGET's grant CLI, in order.
GRANT_CLI_SEARCH = ("/opt/meshanchor", "/opt/meshforge")
#: The box judges its OWN role (session 3 step 2, 2026-10-14): the export's
#: ``role`` section resolves this app's declared role through this app's
#: provisioner + catalog and reports whether these two units are expected.
#: The unit whose absence removes this app's whole HTTP truth surface
#: (``/api/status`` + ``/fleet/slo``).
HTTP_SURFACE_UNIT = "meshanchor-map"
#: The box-health watchdog unit a role declares enabled / disabled / absent.
#: ONE box organ for both apps (see WATCHDOG_PATH) — MeshForge's — so this
#: seam names MeshForge's unit, and MeshAnchor's catalog declares it per
#: role (``enabled`` where the box runs it, ``absent`` on a MeshAnchor-only
#: box, so its absence reads inert there instead of dark).
WATCHDOG_UNIT = "meshforge-watchdog"
#: None: MeshAnchor's radio is MeshCore, opened in-process by the app, so
#: there is no unit a role could declare. Its radio expectation is then
#: undecidable (None) and the radio cell behaves exactly as before —
#: never False, which would quiet a real "not connected".
RADIO_UNIT = None
#: This app's role catalog, and the provisioner that resolves a role in it.
ROLE_CATALOG = Path(INSTALL_DIR) / "docs" / "fleet_roles.yaml"
PROVISIONER = Path(INSTALL_DIR) / "scripts" / "provision_role.py"


#: How an operator pins a target's host key in THIS app's identity source —
#: quoted by the shared reader/center when a target has none. MeshAnchor has
#: no fleet_naming registry: the pin lives in the operator's fleet.json.
HOSTKEY_FIX = ("add it to fleet.json: peers[].ssh.expect_hostkey "
               "(ssh-keyscan -t ed25519 <host> | ssh-keygen -lf -)")


def expect_hostkey_of(alias: str):
    """(expected host-key fingerprint, why-not) for a target ``alias`` from
    THIS app's identity source — MeshAnchor: the ``fleet.json`` peer whose
    ``ssh.alias`` (else ``name``) is ``alias``, field ``ssh.expect_hostkey``.
    The OPERATOR writes it; tooling only reads it (spec Q11 identity half;
    registration stays in the shared targets file — a CLI rewriting the
    hand-edited fleet.json is the concurrent-editor hazard, N6). A fleet.json
    that does not parse is NAMED, never "no key"."""
    from monitoring.fleet_config import load_fleet_config
    cfg = load_fleet_config()
    if cfg.parse_error:
        return None, (f"fleet.json unreadable ({cfg.source_path}): "
                      f"{cfg.parse_error} — fix it before reading targets")
    if alias in cfg.duplicate_aliases:
        return None, (f"fleet.json names {alias} more than once (a name or an "
                      "ssh.alias) — make each unique before reading it")
    for peer in cfg.peers:
        if (peer.ssh_alias or peer.name) == alias:
            return peer.ssh_expect_hostkey, None
    return None, None


def app_version() -> str:
    try:
        from __version__ import __version__
        return str(__version__)
    except Exception:  # an export must answer even if the version is unreadable
        return "unknown"
