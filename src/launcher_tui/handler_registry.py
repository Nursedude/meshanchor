"""
Handler Registry — Central dispatch for TUI command handlers.

Manages handler registration, menu-item aggregation, feature-flag
filtering, and action dispatch. Replaces the inline ``dispatch = {}``
dictionaries scattered across MeshAnchorLauncher submenu methods.

Phase 0 of the migration: infrastructure only, no existing code changed.

See also:
    handler_protocol.py — TUIContext, CommandHandler, BaseHandler
    handlers/            — Converted handler implementations
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from handler_protocol import CommandHandler, LifecycleHandler, TUIContext

logger = logging.getLogger(__name__)


class HandlerRegistry:
    """Central registry for TUI command handlers.

    Handlers register themselves (or are registered by the launcher).
    Submenu orchestrators call ``get_menu_items(section)`` to build menus
    and ``dispatch(section, tag)`` to execute actions.

    During the migration, submenus try registry dispatch first and fall
    back to legacy mixin methods when no handler is found.
    """

    def __init__(self, ctx: TUIContext):
        self._ctx = ctx
        self._handlers: Dict[str, CommandHandler] = {}
        self._sections: Dict[str, List[CommandHandler]] = defaultdict(list)
        # Tag-to-handler index for O(1) dispatch
        self._tag_index: Dict[str, Dict[str, CommandHandler]] = defaultdict(dict)
        # Cross-section rows: (section, tag) -> (owner_section, owner_tag).
        # See ``alias()`` — the ONE declaration of a row shown on a screen
        # other than its handler's own.
        self._aliases: Dict[str, Dict[str, Tuple[str, str]]] = defaultdict(dict)

    def register(self, handler: CommandHandler) -> None:
        """Register a handler, injecting the shared context.

        Args:
            handler: A CommandHandler instance. Must have a unique handler_id.

        Raises:
            ValueError: If handler_id is already registered.
        """
        hid = handler.handler_id
        if hid in self._handlers:
            raise ValueError(
                f"Handler {hid!r} already registered "
                f"(existing: {type(self._handlers[hid]).__name__}, "
                f"new: {type(handler).__name__})"
            )

        # Validate tags BEFORE mutating registry state, so a duplicate
        # leaves the registry unchanged (no half-registered handler).
        for tag, _desc, _flag in handler.menu_items():
            existing = self._tag_index[handler.menu_section].get(tag)
            if existing is not None:
                raise ValueError(
                    f"Duplicate tag {tag!r} in section "
                    f"{handler.menu_section!r}: already owned by "
                    f"{existing.handler_id!r}, refusing {hid!r} — a "
                    f"duplicate would silently shadow one handler's action"
                )

        handler.set_context(self._ctx)
        self._handlers[hid] = handler
        self._sections[handler.menu_section].append(handler)

        for tag, _desc, _flag in handler.menu_items():
            self._tag_index[handler.menu_section][tag] = handler

        logger.debug(
            "Registered handler %s (section=%s, items=%d)",
            hid, handler.menu_section, len(handler.menu_items()),
        )

    def get_handler(self, handler_id: str) -> Optional[CommandHandler]:
        """Look up a handler by its unique ID.

        Returns:
            The handler, or None if not found.
        """
        return self._handlers.get(handler_id)

    #: A PREFIX rather than a suffix on purpose: whiptail truncates a label
    #: to the box width, so a marker at the end is exactly what disappears
    #: on the 24x80 terminal where it matters most.
    OFF_MARK = "[off] "

    def alias(self, section: str, tag: str,
              owner_section: str, owner_tag: str) -> None:
        """Declare a CROSS-SECTION row: ``tag`` on ``section``'s screen IS
        the action ``owner_section``/``owner_tag``, shown one menu away.

        ONE declaration per row, and nothing else about it is stated
        anywhere: its label, its ``[off]`` mark and the title of its
        refusal all derive from the owner through ``owner_row``, so the
        same action cannot read differently on two screens. Until
        2026-09-17 each menu loop carried a hand-copied 2-tuple label with
        NO mark and a hand-written dispatch fallback — so on the MeshCore
        box, under the ``meshcore`` profile, the primary menu listed
        NomadNet / Channels as available and the keypress answered
        "not in this profile" (MeshForge review 2026-09-16 R1/R4/F4,
        ported).

        Fails loud at declaration: an alias to an owner nobody registered,
        or to a tag the section's own handlers already own, is a wiring
        bug the operator must never meet as a blank or duplicate row.
        """
        if owner_tag not in self._tag_index.get(owner_section, {}):
            raise ValueError(
                f"alias {section}/{tag}: no handler owns "
                f"{owner_section}/{owner_tag}")
        if tag in self._tag_index.get(section, {}):
            raise ValueError(
                f"alias {section}/{tag}: a handler in {section!r} already "
                f"owns that tag — the alias is dead, delete it")
        if tag in self._aliases.get(section, {}):
            raise ValueError(
                f"alias {section}/{tag}: already declared as "
                f"{self._aliases[section][tag]} — a second declaration is a "
                f"contradiction in the SSOT table, not an update")
        self._aliases[section][tag] = (owner_section, owner_tag)

    def _profile_active(self) -> bool:
        """True when a saved profile is gating the menu at all."""
        return bool(getattr(self._ctx, "feature_flags", None))

    def aliases(self, section: str) -> Dict[str, Tuple[str, str]]:
        """The cross-section rows declared on a section's screen."""
        return dict(self._aliases.get(section, {}))

    def get_menu_items(self, section: str) -> List[Tuple[str, str]]:
        """Every menu row for a section — gated ones MARKED, never removed.

        The rule, stated once: **a profile changes what a row SAYS, never
        whether it is there.** Until this change three separate places
        removed rows a profile disabled — here, and two top-level lists in
        ``main.py`` — while a comment above ``_FEATURE_HINTS`` claimed the
        vanish behaviour had already been replaced. It had not; both
        designs ran at once.

        Hiding is wrong for the people the TUI is actually for. Someone new
        to the domain cannot go looking for a capability they have never
        been shown, so a shorter menu just looks like a smaller product.
        Marking teaches them what the tool does AND why this box does not
        do it, which is the whole job of a deployment profile.

        Cross-section rows (``alias``) come last, rendered from their
        owner's label and flag, so the menus that carry one keep no copy.

        Returns:
            List of (tag, description). A row whose flag is off keeps its
            own label, prefixed with ``OFF_MARK`` so the reader still sees
            what the tool is.
        """
        items: List[Tuple[str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                items.append((tag, self.mark_label(desc, flag)))
        for tag, (osec, otag) in self._aliases.get(section, {}).items():
            row = self.owner_row(osec, otag)
            if row is None:
                # Validated at declaration, so the owner LOST the row
                # since. owner_row logged which half drifted; render the
                # tag rather than let the row vanish — and under a profile
                # render it MARKED, because dispatch() will refuse it: a
                # flag that cannot be read is not "no flag" (review
                # 2026-09-17 #2, honest-failure-modes #1).
                items.append((tag, self.OFF_MARK + tag
                              if self._profile_active() else tag))
                continue
            items.append((tag, self.mark_label(row[0], row[1])))
        return items

    def mark_label(self, desc: str, flag: Optional[str]) -> str:
        """The label a row shows under the active profile.

        One implementation, because the launcher renders legacy rows
        itself — they must all mark identically or the same action reads
        differently depending on which screen you found it on.
        """
        if flag is None or self._ctx.feature_enabled(flag):
            return desc
        return self.OFF_MARK + desc

    def get_gated_items(self, section: str) -> List[Tuple[str, str, str]]:
        """The ROWS the active profile marks off on this screen, and the
        flag that did it — including a cross-section row whose owner is
        off, so the count agrees with the marks the operator can see.

        Not "hidden" — nothing is hidden any more. Used for honest counts.

        Returns:
            List of (tag, description, flag). Empty when no profile is set.
        """
        gated: List[Tuple[str, str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                if flag is not None and not self._ctx.feature_enabled(flag):
                    gated.append((tag, desc, flag))
        for tag, (osec, otag) in self._aliases.get(section, {}).items():
            row = self.owner_row(osec, otag)
            if row is None:
                if self._profile_active():
                    gated.append((tag, tag, "?"))   # unreadable = refused
                continue
            if row[1] is None:
                continue
            if not self._ctx.feature_enabled(row[1]):
                gated.append((tag, row[0], row[1]))
        return gated

    def owner_row(self, section: str,
                  tag: str) -> Optional[Tuple[str, Optional[str]]]:
        """The (description, flag) of the handler that OWNS section/tag,
        following a cross-section alias to its owner first.

        ONE lookup for every reader — ``get_menu_items``,
        ``get_gated_items``, ``dispatch`` and ``explain_gated`` — where
        each used to walk the section on its own. It can fail two ways
        and logs them as two different sentences because they are two
        different defects: no handler registered for the tag is a wiring
        gap; a registered handler whose LIVE ``menu_items()`` no longer
        lists the tag is a handler that drifted from its own registration.
        """
        section, tag = self._aliases.get(section, {}).get(tag, (section, tag))
        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            logger.warning(
                "owner_row(%r, %r): no handler in that section owns the tag",
                section, tag)
            return None
        for t, desc, flag in handler.menu_items():
            if t == tag:
                return desc, flag
        logger.warning(
            "owner_row(%r, %r): %s is registered for the tag but its live "
            "menu_items() no longer lists it — the handler drifted from "
            "its registration",
            section, tag, getattr(handler, "handler_id", "?"))
        return None

    def owner_flag(self, section: str, tag: str) -> Optional[str]:
        """The feature flag a tag carries in the section that OWNS it."""
        row = self.owner_row(section, tag)
        return None if row is None else row[1]

    def explain_gated(self, section: str, tag: str, flag: str,
                      label: Optional[str] = None) -> None:
        """Say why this row is off, and how to change it WITHOUT leaving.

        Derived from the flag and the profile rather than read out of a
        per-feature table of hint strings. A table is a second declaration
        of something the handler already states, free to drift from it and
        certain to be missing an entry the day a flag is added. The table
        this replaced had three entries for a tree with more flags than
        that, and every one of its bodies told the operator to quit the
        TUI and run a CLI command — the thing MF018 exists to forbid.

        The dialog goes through ``safe_call`` like every other thing the
        registry shows, so a refusal that fails to render leaves a LOG
        witness (``log_error`` runs before any second dialog). What that
        does NOT buy is exception-freedom: ``safe_call``'s own error
        dialog uses the same backend, so if the dialog itself is what is
        broken the exception still propagates — as it does from every
        ``safe_call`` in the TUI.
        """
        profile = self._ctx.profile_label() or "?"
        if label is None:
            row = self.owner_row(section, tag)
            label = row[0] if row is not None else tag
        label = label.strip().split("  ")[0] or tag
        self._ctx.safe_call(
            f"explain gated {section}/{tag}", self._ctx.dialog.msgbox,
            f"{label} — not in this profile",
            f"This box is set to the '{profile}' deployment profile, which "
            f"does not include '{flag}'.\n\n"
            f"Nothing is broken and nothing has been removed — the profile "
            f"describes what this box is FOR, so tools outside it are shown "
            f"but not run.\n\n"
            f"To use it, change the profile in:\n"
            f"  Configuration > MeshAnchor Settings > Deployment Profile\n\n"
            f"Choosing a wider profile (for example 'full') enables "
            f"everything.")

    def dispatch(self, section: str, tag: str) -> bool:
        """Find and execute the handler for a given section + tag.

        A cross-section alias delegates to its owner, so the menu loops
        need no per-tag fallback of their own — and the refusal a gated
        owner shows is titled with the label this screen rendered,
        because both come from the same ``owner_row``.

        Wraps the handler's ``execute()`` in ``safe_call()`` for
        consistent error handling.

        Args:
            section: Menu section key.
            tag: The action tag selected by the user.

        Returns:
            True if a handler was found — invoked, or refused with an
            explanation because the active profile does not include it.
            False only when no handler owns the tag.
        """
        target = self._aliases.get(section, {}).get(tag)
        if target is not None:
            return self.dispatch(*target)

        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            return False

        # A row the profile marks off is SHOWN but not RUN. Intercepted
        # here, centrally, so every menu loop inherits it — a rule
        # implemented per-loop is a rule that is missing from one of them,
        # which is how this repo ended up running two designs at once.
        # Returns True because the tag IS owned; falling through would
        # reach the "not wired" path and report a wiring bug that does
        # not exist. owner_row logs the witness when the handler's live
        # rows no longer carry the tag — the action still runs, unflagged,
        # and the log says why.
        row = self.owner_row(section, tag)
        if row is None and self._profile_active():
            # The flag cannot be read (owner_row logged why). Under a
            # profile that is a REFUSAL, not a pass: None must not wear
            # the "unflagged, run it" value (review 2026-09-17 #2).
            profile = self._ctx.profile_label() or "?"
            self._ctx.safe_call(
                f"refuse unreadable {section}/{tag}", self._ctx.dialog.msgbox,
                f"{tag} — cannot verify profile",
                f"MeshAnchor cannot tell whether the '{profile}' profile "
                f"includes '{tag}': its handler no longer lists this row, "
                f"so the row's feature flag is unreadable.\n\n"
                f"Not run. This is a MeshAnchor wiring drift — please "
                f"report it (About > Version has the issue link).")
            return True
        flag = row[1] if row is not None else None
        if flag is not None and not self._ctx.feature_enabled(flag):
            logger.info("Refused %s/%s: '%s' is not in profile %r",
                        section, tag, flag, self._ctx.profile_label())
            self.explain_gated(section, tag, flag, label=row[0])
            return True

        self._ctx.safe_call(handler.handler_id, handler.execute, tag)
        return True

    def startup_all(self) -> None:
        """Call ``on_startup()`` on all handlers that implement LifecycleHandler."""
        for handler in self._handlers.values():
            if isinstance(handler, LifecycleHandler):
                try:
                    handler.on_startup()
                except Exception as e:
                    logger.warning(
                        "Startup hook failed for %s: %s",
                        handler.handler_id, e,
                    )

    def shutdown_all(self) -> None:
        """Call ``on_shutdown()`` on all handlers that implement LifecycleHandler."""
        for handler in self._handlers.values():
            if isinstance(handler, LifecycleHandler):
                try:
                    handler.on_shutdown()
                except Exception as e:
                    logger.warning(
                        "Shutdown hook failed for %s: %s",
                        handler.handler_id, e,
                    )

    @property
    def handler_count(self) -> int:
        """Number of registered handlers."""
        return len(self._handlers)

    @property
    def section_names(self) -> List[str]:
        """List of sections that have at least one handler or alias."""
        names = list(self._sections.keys())
        for sec in self._aliases:
            if sec not in names:
                names.append(sec)
        return names

    def __repr__(self) -> str:
        return (
            f"HandlerRegistry(handlers={len(self._handlers)}, "
            f"sections={list(self._sections.keys())})"
        )
