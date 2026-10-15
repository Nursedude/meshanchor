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


def app_version() -> str:
    try:
        from __version__ import __version__
        return str(__version__)
    except Exception:  # an export must answer even if the version is unreadable
        return "unknown"
