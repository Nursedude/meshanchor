"""MeshAnchor supervisor processes.

The supervisor pattern gives long-running radio sessions their own
lifecycle, separate from the gateway bridge daemon. A bridge restart
should not flap the radio; an operator inspecting the radio (via TUI
or CLI) should not race the bridge for the serial port.

This package is Session 2 of the MeshCore high-integration charter.
See ``.claude/plans/meshcore_high_integration_charter.md``.
"""
