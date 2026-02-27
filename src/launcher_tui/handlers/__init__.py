"""
TUI Command Handlers — Registry-based dispatch replacements for mixins.

Each module in this package contains one handler class that implements
the CommandHandler protocol from handler_protocol.py.

Usage:
    from handlers import get_all_handlers
    for handler_cls in get_all_handlers():
        registry.register(handler_cls())
"""

from typing import List, Type


def get_all_handlers() -> List[Type]:
    """Return all handler classes for registration.

    New handlers are added here as mixins are converted.
    Import is deferred to avoid circular dependencies.
    """
    handlers: List[Type] = []

    # Phase 1 pilot handlers
    from handlers.latency import LatencyHandler
    from handlers.classifier import ClassifierHandler
    from handlers.amateur_radio import AmateurRadioHandler
    from handlers.analytics import AnalyticsHandler
    from handlers.rf_tools import RFToolsHandler
    handlers.extend([
        LatencyHandler,
        ClassifierHandler,
        AmateurRadioHandler,
        AnalyticsHandler,
        RFToolsHandler,
    ])

    return handlers
