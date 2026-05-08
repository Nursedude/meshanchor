"""TUI handler: pin MeshCore nodes on the map.

MeshCore advertisements don't carry GPS, so any MeshCore node the
operator wants to see on the map needs a manual lat/lon pin. This
handler is the operator-facing UI for ``utils.meshcore_positions``.

Menu:
  - Place node       — pick a discovered MeshCore contact (or enter
                       a pubkey by hand) and set its lat/lon
  - List placements  — show every pinned node
  - Edit placement   — re-pin or rename
  - Delete placement — remove a pin
  - Back

The placement store is at ``~/.config/meshanchor/meshcore_positions.json``
and is read every time MapDataCollector runs ``_collect_meshcore``,
so updates show on the map within one collection cycle (~5s).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

_get_position_store, _HAS_POSITIONS = safe_import(
    'utils.meshcore_positions', 'get_position_store',
)
_get_node_tracker, _HAS_TRACKER = safe_import(
    'gateway.node_tracker', 'get_node_tracker',
)


def _short_pubkey(pubkey: str) -> str:
    return (pubkey or "")[:12]


class MeshCorePositionsHandler(BaseHandler):
    """TUI handler for operator-placed MeshCore positions."""

    handler_id = "meshcore_positions"
    menu_section = "meshcore"
    CHAT_API_BASE = "http://127.0.0.1:8081"

    def menu_items(self):
        return [
            (
                "meshcore_positions",
                "MeshCore Map Pins   Pin nodes on the map (lat/lon)",
                "meshcore",
            ),
        ]

    def execute(self, action):
        if action == "meshcore_positions":
            self._main_menu()

    def _main_menu(self) -> None:
        if not _HAS_POSITIONS:
            self.ctx.dialog.msgbox(
                "MeshCore Map Pins",
                "Position store module is not available.\n\n"
                "This is a packaging issue; reinstall MeshAnchor.",
            )
            return

        while True:
            store = _get_position_store()
            placed = store.list()
            subtitle = f"Pinned nodes: {len(placed)}"

            choice = self.ctx.dialog.menu(
                "MeshCore Map Pins",
                subtitle,
                [
                    ("self", "Pin this radio      Self-pin from /radio (name + coords)"),
                    ("place", "Place node          Pin a MeshCore node on the map"),
                    ("list", "List placements     Show all pinned nodes"),
                    ("edit", "Edit placement      Move a pin / rename"),
                    ("delete", "Delete placement    Remove a pin"),
                    ("back", "Back"),
                ],
            )
            if choice in (None, "back"):
                return

            dispatch = {
                "self": ("Pin This Radio", self._pin_self),
                "place": ("Place MeshCore Node", self._place),
                "list": ("List Placements", self._list),
                "edit": ("Edit Placement", self._edit),
                "delete": ("Delete Placement", self._delete),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    # ---- Pin self -------------------------------------------------

    def _fetch_local_radio(self) -> Optional[Dict]:
        """GET /radio. Returns the ``radio`` payload dict or None on error."""
        import json
        import urllib.error
        import urllib.request

        url = f"{self.CHAT_API_BASE}/radio"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            self.ctx.dialog.msgbox(
                "Daemon unreachable",
                f"Could not reach the local daemon at {self.CHAT_API_BASE}/radio.\n\n"
                f"Reason: {e}\n\n"
                "Start the daemon (System → Daemon Control) and try again.",
            )
            return None
        if not isinstance(body, dict) or "radio" not in body:
            self.ctx.dialog.msgbox(
                "Unexpected response",
                "The daemon returned a response without a 'radio' field.",
            )
            return None
        return body.get("radio") or {}

    def _pin_self(self) -> None:
        """Pin the local USB MeshCore radio using its current /radio state.

        Pulls pubkey + name + advertised lat/lon from /radio and writes a
        placement so the local node shows on the map without manual data
        entry. Falls through to the explicit lat/lon prompts when the
        pubkey is known but coordinates aren't yet set on the chip.
        """
        radio = self._fetch_local_radio()
        if radio is None:
            return

        pubkey = (radio.get("public_key") or "").strip()
        if not pubkey:
            self.ctx.dialog.msgbox(
                "No public key",
                "The daemon hasn't read a public_key from the radio yet.\n\n"
                "Open Radio Config → View to refresh state, or wait for "
                "the next SELF_INFO refresh.",
            )
            return

        name = (radio.get("node_name") or "").strip()
        lat = radio.get("radio_lat")
        lon = radio.get("radio_lon")
        coords_set = (
            isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180
            and not (float(lat) == 0.0 and float(lon) == 0.0)
        )

        if coords_set:
            confirm = (
                f"Pin this radio on the map?\n\n"
                f"  Name:   {name or '(unnamed)'}\n"
                f"  Pubkey: {_short_pubkey(pubkey)}…\n"
                f"  Coords: ({float(lat):.5f}, {float(lon):.5f})\n\n"
                "These come from /radio (the values sent in adverts)."
            )
            if not self.ctx.dialog.yesno("Pin This Radio", confirm):
                return
            try:
                _get_position_store().set(
                    pubkey, float(lat), float(lon),
                    alt=None,
                    name=name or None,
                    notes="Self-pinned from /radio",
                )
            except ValueError as e:
                self.ctx.dialog.msgbox("Could not save", str(e))
                return
            self.ctx.dialog.msgbox(
                "Pinned",
                f"{name or 'this radio'} placed at "
                f"({float(lat):.5f}, {float(lon):.5f}).\n\n"
                "Will appear on the map within ~5s.",
            )
            return

        # Pubkey known but coords absent: fall through to manual entry,
        # pre-filling the friendly name from /radio.
        self.ctx.dialog.msgbox(
            "Coords not set on radio",
            "/radio doesn't report advertised lat/lon yet.\n\n"
            "Set them via Radio Config → Identity & Position → Set Coordinates "
            "(broadcast in adverts), or enter them manually now.",
        )
        self._prompt_and_save(pubkey, name)

    # ---- Place ----------------------------------------------------

    def _place(self) -> None:
        contacts = self._discovered_contacts()
        choices: List[Tuple[str, str]] = []
        for pubkey, name in contacts:
            choices.append((
                pubkey,
                f"{name[:24]:<24} {_short_pubkey(pubkey)}",
            ))
        choices.append(("__manual__", "Enter pubkey manually..."))
        choices.append(("__cancel__", "Cancel"))

        chosen = self.ctx.dialog.menu(
            "Place MeshCore Node",
            "Pick a discovered contact or enter a pubkey by hand:",
            choices,
        )
        if chosen in (None, "__cancel__"):
            return

        if chosen == "__manual__":
            pubkey = self.ctx.dialog.inputbox(
                "Enter Pubkey",
                "MeshCore pubkey (hex, 32 bytes / 64 chars):",
            )
            if not pubkey:
                return
            pubkey = pubkey.strip()
            existing_name = ""
        else:
            pubkey = chosen
            existing_name = next(
                (n for pk, n in contacts if pk == pubkey), ""
            )

        self._prompt_and_save(pubkey, existing_name)

    def _prompt_and_save(self, pubkey: str, existing_name: str = "") -> None:
        store = _get_position_store()
        existing = store.get(pubkey)
        defaults = existing or {
            "lat": None, "lon": None, "alt": None,
            "name": existing_name, "notes": "",
        }

        lat_str = self.ctx.dialog.inputbox(
            "Latitude",
            "Latitude (decimal degrees, -90 to 90):",
            init=str(defaults.get("lat") or ""),
        )
        if lat_str is None:
            return
        lon_str = self.ctx.dialog.inputbox(
            "Longitude",
            "Longitude (decimal degrees, -180 to 180):",
            init=str(defaults.get("lon") or ""),
        )
        if lon_str is None:
            return

        try:
            lat = float(lat_str.strip())
            lon = float(lon_str.strip())
        except ValueError as e:
            self.ctx.dialog.msgbox("Invalid coordinates", f"Could not parse: {e}")
            return

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            self.ctx.dialog.msgbox(
                "Out of range",
                f"Latitude must be -90..90 and longitude -180..180.\n"
                f"Got lat={lat}, lon={lon}.",
            )
            return

        alt_str = self.ctx.dialog.inputbox(
            "Altitude (optional)",
            "Altitude in meters MSL (or blank to skip):",
            init=str(defaults.get("alt") or ""),
        )
        if alt_str is None:
            return
        alt: Optional[float] = None
        if alt_str.strip():
            try:
                alt = float(alt_str.strip())
            except ValueError:
                self.ctx.dialog.msgbox(
                    "Invalid altitude",
                    f"Could not parse altitude '{alt_str}'. Skipping altitude.",
                )

        name = self.ctx.dialog.inputbox(
            "Display name (optional)",
            "Friendly name for the map (or blank to use node's adv_name):",
            init=str(defaults.get("name") or ""),
        )
        if name is None:
            return

        notes = self.ctx.dialog.inputbox(
            "Notes (optional)",
            "Free-form note (e.g., 'QTH', 'mountaintop relay'):",
            init=str(defaults.get("notes") or ""),
        )
        if notes is None:
            return

        try:
            store.set(
                pubkey, lat, lon,
                alt=alt,
                name=name.strip() or None,
                notes=notes.strip() or None,
            )
        except ValueError as e:
            self.ctx.dialog.msgbox("Could not save", str(e))
            return

        self.ctx.dialog.msgbox(
            "Pinned",
            f"{name.strip() or 'MC-' + _short_pubkey(pubkey)} placed at "
            f"({lat:.5f}, {lon:.5f}).\n\n"
            "Will appear on the map within ~5s on next collection cycle.",
        )

    # ---- List -----------------------------------------------------

    def _list(self) -> None:
        store = _get_position_store()
        placed = store.list()
        clear_screen()
        print("=== MeshCore Map Pins ===\n")
        if not placed:
            print("  (none)")
            print("\n  Use 'Place node' from the menu to add one.")
        else:
            for pk, rec in sorted(placed.items(), key=lambda kv: kv[1].get("name") or kv[0]):
                name = rec.get("name") or f"MC-{_short_pubkey(pk)}"
                lat = rec.get("lat")
                lon = rec.get("lon")
                alt = rec.get("alt")
                notes = rec.get("notes") or ""
                set_at = rec.get("set_at") or "?"
                line = f"  {name:<24} ({lat:.5f}, {lon:.5f})"
                if alt is not None:
                    line += f"  alt={alt}m"
                line += f"  pk={_short_pubkey(pk)}…  set={set_at[:10]}"
                print(line)
                if notes:
                    print(f"      note: {notes}")
        print()
        self.ctx.wait_for_enter()

    # ---- Edit -----------------------------------------------------

    def _edit(self) -> None:
        store = _get_position_store()
        placed = store.list()
        if not placed:
            self.ctx.dialog.msgbox(
                "Edit Placement",
                "No placements to edit.\n\nUse 'Place node' to add one.",
            )
            return
        choices = [
            (pk, f"{(rec.get('name') or 'MC-' + _short_pubkey(pk))[:24]:<24} "
                 f"{_short_pubkey(pk)}…")
            for pk, rec in sorted(placed.items())
        ]
        choices.append(("__cancel__", "Cancel"))
        chosen = self.ctx.dialog.menu(
            "Edit Placement",
            "Pick a pin to edit:",
            choices,
        )
        if chosen in (None, "__cancel__"):
            return
        existing_name = placed[chosen].get("name") or ""
        self._prompt_and_save(chosen, existing_name)

    # ---- Delete ---------------------------------------------------

    def _delete(self) -> None:
        store = _get_position_store()
        placed = store.list()
        if not placed:
            self.ctx.dialog.msgbox("Delete Placement", "No placements to delete.")
            return
        choices = [
            (pk, f"{(rec.get('name') or 'MC-' + _short_pubkey(pk))[:24]:<24} "
                 f"{_short_pubkey(pk)}…")
            for pk, rec in sorted(placed.items())
        ]
        choices.append(("__cancel__", "Cancel"))
        chosen = self.ctx.dialog.menu(
            "Delete Placement",
            "Pick a pin to remove:",
            choices,
        )
        if chosen in (None, "__cancel__"):
            return
        rec = placed[chosen]
        name = rec.get("name") or f"MC-{_short_pubkey(chosen)}"
        if not self.ctx.dialog.yesno(
            "Confirm Delete",
            f"Remove pin for {name} at ({rec.get('lat')}, {rec.get('lon')})?",
            default_no=True,
        ):
            return
        if store.delete(chosen):
            self.ctx.dialog.msgbox("Deleted", f"Removed {name}.")
        else:
            self.ctx.dialog.msgbox("Delete failed", "Pin not found (already removed?).")

    # ---- Helpers --------------------------------------------------

    def _discovered_contacts(self) -> List[Tuple[str, str]]:
        """Return [(pubkey, name)] from the unified node tracker.

        Empty list when the tracker isn't available — the manual-pubkey
        path stays usable so this handler never strands the operator.
        """
        if not _HAS_TRACKER:
            return []
        try:
            tracker = _get_node_tracker()
            mc_nodes = tracker.get_meshcore_nodes()
        except Exception as e:
            logger.debug("MeshCore contacts lookup failed: %s", e)
            return []

        out: List[Tuple[str, str]] = []
        for node in mc_nodes or []:
            pk = (getattr(node, "meshcore_pubkey", "") or "").lower()
            if not pk:
                continue
            name = getattr(node, "name", None) or pk[:12]
            out.append((pk, name))
        out.sort(key=lambda x: x[1].lower())
        return out
