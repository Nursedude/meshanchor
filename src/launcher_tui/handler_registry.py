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

        Returns:
            List of (tag, description). A row whose flag is off keeps its
            own label, prefixed with ``OFF_MARK`` so the reader still sees
            what the tool is.
        """
        items: List[Tuple[str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                items.append((tag, self.mark_label(desc, flag)))
        return items

    def mark_label(self, desc: str, flag: Optional[str]) -> str:
        """The label a row shows under the active profile.

        One implementation, because the launcher renders cross-section and
        legacy rows itself — they must all mark identically or the same
        action reads differently depending on which screen you found it on.
        """
        if flag is None or self._ctx.feature_enabled(flag):
            return desc
        return self.OFF_MARK + desc

    def get_gated_items(self, section: str) -> List[Tuple[str, str, str]]:
        """The rows the active profile marks off, and the flag that did it.

        Not "hidden" — nothing is hidden any more. Used for honest counts.

        Returns:
            List of (tag, description, flag). Empty when no profile is set.
        """
        gated: List[Tuple[str, str, str]] = []
        for handler in self._sections.get(section, []):
            for tag, desc, flag in handler.menu_items():
                if flag is not None and not self._ctx.feature_enabled(flag):
                    gated.append((tag, desc, flag))
        return gated

    def owner_flag(self, section: str, tag: str) -> Optional[str]:
        """The feature flag a tag carries in the section that OWNS it.

        Lets a cross-section row inherit its owner's flag instead of a
        second hardcoded copy of the flag name.
        """
        for handler in self._sections.get(section, []):
            for t, _desc, flag in handler.menu_items():
                if t == tag:
                    return flag
        logger.warning(
            "owner_flag(%r, %r): no handler in that section owns the tag "
            "— the cross-section row will not be marked", section, tag)
        return None

    def explain_gated(self, section: str, tag: str, flag: str) -> None:
        """Say why this row is off, and how to change it WITHOUT leaving.

        Derived from the flag and the profile rather than read out of a
        per-feature table of hint strings. A table is a second declaration
        of something the handler already states, free to drift from it and
        certain to be missing an entry the day a flag is added. The table
        this replaced had three entries for a tree with more flags than
        that, and every one of its bodies told the operator to quit the
        TUI and run a CLI command — the thing MF018 exists to forbid.
        """
        profile = self._ctx.profile_label() or "?"
        label = tag
        for handler in self._sections.get(section, []):
            for t, desc, _f in handler.menu_items():
                if t == tag:
                    label = desc.strip().split("  ")[0] or tag
        self._ctx.dialog.msgbox(
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

        Wraps the handler's ``execute()`` in ``safe_call()`` for
        consistent error handling.

        Args:
            section: Menu section key.
            tag: The action tag selected by the user.

        Returns:
            True if a handler was found and invoked, False otherwise.
        """
        handler = self._tag_index.get(section, {}).get(tag)
        if handler is None:
            return False

        # A row the profile marks off is SHOWN but not RUN. Intercepted
        # here, centrally, so every menu loop inherits it — a rule
        # implemented per-loop is a rule that is missing from one of them,
        # which is how this repo ended up running two designs at once.
        # Returns True because the tag IS owned; falling through would
        # reach the "not wired" path and report a wiring bug that does
        # not exist.
        flag = self.owner_flag(section, tag)
        if flag is not None and not self._ctx.feature_enabled(flag):
            logger.info("Refused %s/%s: '%s' is not in profile %r",
                        section, tag, flag, self._ctx.profile_label())
            self.explain_gated(section, tag, flag)
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
        """List of sections that have at least one handler."""
        return list(self._sections.keys())

    def __repr__(self) -> str:
        return (
            f"HandlerRegistry(handlers={len(self._handlers)}, "
            f"sections={list(self._sections.keys())})"
        )
