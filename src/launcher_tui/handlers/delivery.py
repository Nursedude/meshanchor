"""Delivery Handler — did messages arrive on this box? (MF port, 2026-09-23)

Renders ``utils.delivery_view`` for THIS box: the daemon's delivery record
(windowed confirmation rate — the same count the stall check judges — plus
lifetime totals and drops) and its queue and dead letters, each with source
and age, read LIVE from the local map (never the DB). A source that cannot
be read says UNKNOWN and prints no number.

⚠️ READ-ONLY. It never opens the delivery DB, never touches a service.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class DeliveryHandler(BaseHandler):
    """Did messages arrive on this box — with source and age per number."""

    handler_id = "delivery"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("delivery", "Delivery            did messages arrive? (source+age)", None),
        ]

    def execute(self, action):
        if action == "delivery":
            self._show()

    def _show(self):
        try:
            from utils.delivery_view import gather, render
            text = render(gather())
        except Exception as e:  # the screen must say it could not see
            logger.warning("delivery view failed: %s", e, exc_info=True)
            text = ("Delivery — UNKNOWN\n\nThe delivery sources on this box "
                    f"could not be read:\n  {type(e).__name__}: {e}\n\n"
                    "UNKNOWN is not a pass. This screen never changes anything.")
        self.ctx.dialog.msgbox("Delivery", text)
