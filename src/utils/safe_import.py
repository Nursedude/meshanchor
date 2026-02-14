"""
Safe Import Utility — Phase 1.1 Technical Debt Reduction

Consolidates the ~489 try/except ImportError blocks scattered across
the codebase into a single reusable helper.

Usage:
    from utils.safe_import import safe_import

    # Single attribute from a module
    emit_message, HAS_BUS = safe_import('utils.event_bus', 'emit_message')

    # Multiple attributes from one module
    check_service, ServiceState, HAS_CHECK = safe_import(
        'utils.service_check', 'check_service', 'ServiceState'
    )

    # Relative import (within a package)
    Handler, HAS_HANDLER = safe_import(
        '.mqtt_bridge_handler', 'MQTTBridgeHandler', package='gateway'
    )

    # Whole-module import (no attribute names)
    requests_mod, HAS_REQUESTS = safe_import('requests')

Return value:
    Always a tuple of (*values, available: bool).
    On success: (attr1, attr2, ..., True)
    On failure: (None, None, ..., False)
"""

import importlib
import logging
from typing import Any, Tuple

logger = logging.getLogger(__name__)


def safe_import(module: str, *names: str, package: str = None) -> Tuple[Any, ...]:
    """Import a module and return requested attributes with a success flag.

    Args:
        module: Dotted module path (e.g. 'utils.event_bus') or relative
                path (e.g. '.mqtt_bridge_handler') when ``package`` is set.
        *names: Attribute names to extract from the module. If empty, the
                module object itself is returned.
        package: Required for relative imports. Pass ``__package__`` from the
                 calling module.

    Returns:
        Tuple of (*attrs, available_bool). When the import fails every attr
        is ``None`` and the flag is ``False``.

    Examples:
        >>> emit, ok = safe_import('utils.event_bus', 'emit_message')
        >>> if ok:
        ...     emit('rx', 'hello')

        >>> svc, state, ok = safe_import(
        ...     'utils.service_check', 'check_service', 'ServiceState')
    """
    try:
        mod = importlib.import_module(module, package=package)
    except ImportError:
        logger.debug("safe_import: module %r not available", module)
        if not names:
            return (None, False)
        return tuple([None] * len(names)) + (False,)

    if not names:
        return (mod, True)

    attrs = []
    for name in names:
        attrs.append(getattr(mod, name, None))

    return tuple(attrs) + (True,)


__all__ = ['safe_import']
