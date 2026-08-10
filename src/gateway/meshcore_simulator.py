"""In-process MeshCore companion-radio simulator.

Split out of ``meshcore_handler`` 2026-08-09 when the egress-guard port
pushed that file past the MF025 1,500-line cap. The simulator is not RF:
``_send_message``'s tx_guard chokepoint exempts it by isinstance, which is
also why it must remain ONE class both modules agree on — import it from
here (``meshcore_handler`` re-exports it for existing consumers).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class MeshCoreSimulator:
    """
    Simulates MeshCore companion radio for testing without hardware.

    Generates fake events at configurable intervals so the bridge loop
    and routing can be tested end-to-end without a physical radio.
    """

    def __init__(self):
        self._running = False
        self._subscribers: Dict[str, List[Callable]] = {}
        self._contacts = self._generate_fake_contacts()

    def _generate_fake_contacts(self) -> List[Dict[str, Any]]:
        """Generate fake MeshCore contacts for simulation."""
        return [
            {
                'adv_name': 'SimNode-Alpha',
                'public_key': b'\x01\x02\x03\x04\x05\x06',
                'last_seen': datetime.now(),
            },
            {
                'adv_name': 'SimNode-Bravo',
                'public_key': b'\x0a\x0b\x0c\x0d\x0e\x0f',
                'last_seen': datetime.now(),
            },
            {
                'adv_name': 'SimRepeater-01',
                'public_key': b'\xaa\xbb\xcc\xdd\xee\xff',
                'last_seen': datetime.now(),
                'role': 'repeater',
            },
        ]

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Subscribe to simulated events."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    async def start(self):
        """Start generating simulated events."""
        self._running = True
        logger.info("MeshCore simulator started")

    async def stop(self):
        """Stop the simulator."""
        self._running = False

    async def get_contacts(self) -> List[Dict]:
        """Return simulated contacts."""
        return self._contacts

    async def send_msg(self, contact: Any, text: str) -> bool:
        """Simulate sending a message."""
        logger.info(f"[SIM] MeshCore TX: {text[:50]}")
        return True

    async def send_channel_txt_msg(self, text: str) -> bool:
        """Simulate sending a channel broadcast."""
        logger.info(f"[SIM] MeshCore channel TX: {text[:50]}")
        return True
