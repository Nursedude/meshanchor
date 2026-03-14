"""Pure-logic RNS readiness gate for NomadNet pre-launch checks.

No side effects, no dialogs, no subprocesses. Takes system state as
input, returns a decision as output. Fully unit-testable.

Replaces the 270-line decision tree in _nomadnet_rns_checks.py with
a simple, deterministic function.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RNSReadiness:
    """Result of RNS readiness check for NomadNet launch.

    Attributes:
        can_launch: Whether NomadNet can proceed with launch.
        reason: Human-readable explanation of the decision.
        suggestion: Actionable next step (points to diagnostics).
        warning: Optional warning even when can_launch is True.
        rnsd_running: Whether rnsd process was detected.
        shared_instance: Whether RNS shared instance is available.
        user_match: Whether rnsd user matches launch user (None if N/A).
    """
    can_launch: bool
    reason: str
    suggestion: str
    warning: Optional[str] = None
    rnsd_running: bool = False
    shared_instance: bool = False
    user_match: Optional[bool] = None


def check_rns_readiness(
    rnsd_running: bool,
    shared_instance_available: bool,
    rnsd_user: Optional[str] = None,
    launch_user: Optional[str] = None,
) -> RNSReadiness:
    """Pure function: system state in, launch decision out.

    Decision matrix:
        rnsd running | shared instance | users match | Result
        -------------|-----------------|-------------|-------
        yes          | yes             | yes         | can_launch=True
        yes          | yes             | no          | can_launch=True + warning
        yes          | no              | —           | can_launch=False
        no           | no              | —           | can_launch=False
        no           | yes             | —           | can_launch=True (standalone)

    Args:
        rnsd_running: Whether rnsd process is detected.
        shared_instance_available: Whether RNS shared instance socket is up.
        rnsd_user: OS user running rnsd (None if not running).
        launch_user: OS user who will run NomadNet (SUDO_USER or current).

    Returns:
        RNSReadiness with the launch decision.
    """
    # Determine user match
    user_match = None
    if rnsd_running and rnsd_user and launch_user:
        user_match = (rnsd_user == launch_user)

    # Case: rnsd not running
    if not rnsd_running:
        if shared_instance_available:
            # Standalone RNS instance already running (rare but valid)
            return RNSReadiness(
                can_launch=True,
                reason="RNS shared instance available (standalone mode).",
                suggestion="",
                rnsd_running=False,
                shared_instance=True,
                user_match=None,
            )
        return RNSReadiness(
            can_launch=False,
            reason=(
                "rnsd is not running and no RNS shared instance is available.\n\n"
                "NomadNet needs rnsd for Meshtastic bridging and\n"
                "shared RNS interfaces."
            ),
            suggestion="Use RNS Diagnostics to start and configure rnsd.",
            rnsd_running=False,
            shared_instance=False,
            user_match=None,
        )

    # Case: rnsd running but shared instance not available
    if not shared_instance_available:
        return RNSReadiness(
            can_launch=False,
            reason=(
                "rnsd is running but the shared instance is not available.\n\n"
                "rnsd may still be initializing, or an interface may be\n"
                "blocking startup."
            ),
            suggestion="Use RNS Diagnostics to check rnsd status and interfaces.",
            rnsd_running=True,
            shared_instance=False,
            user_match=user_match,
        )

    # Case: rnsd running + shared instance available
    warning = None
    if user_match is False:
        # User mismatch — advisory, not blocking
        warning = (
            f"rnsd runs as '{rnsd_user}' but NomadNet will run as "
            f"'{launch_user}'.\n"
            "Different users may cause RPC authentication issues.\n"
            "If NomadNet crashes, use RNS Diagnostics to fix the user mismatch."
        )

    return RNSReadiness(
        can_launch=True,
        reason="RNS is ready." if not warning else "RNS is ready (with warning).",
        suggestion="",
        warning=warning,
        rnsd_running=True,
        shared_instance=True,
        user_match=user_match,
    )
