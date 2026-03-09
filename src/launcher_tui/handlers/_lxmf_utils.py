"""
LXMF Exclusivity Utility — Check for conflicting LXMF clients.

NomadNet is MeshForge's supported LXMF client.  This helper detects
if another LXMF client (e.g. Sideband, another NomadNet instance) is
using port 37428 and warns the user before starting NomadNet.
"""

import subprocess


def ensure_lxmf_exclusive(dialog, starting_app: str, **_kwargs) -> bool:
    """Ensure no other LXMF client is using port 37428.

    Args:
        dialog: DialogBackend instance for user prompts.
        starting_app: The app being started (e.g. ``"nomadnet"``).

    Returns:
        True if OK to proceed, False if the user cancelled.
    """
    if starting_app != "nomadnet":
        return True

    # Check if port 37428 is already in use by another LXMF client
    port_in_use = False
    try:
        result = subprocess.run(
            ['ss', '-tlnp', 'sport', '=', '37428'],
            capture_output=True, text=True, timeout=5,
        )
        port_in_use = 'LISTEN' in result.stdout
    except (subprocess.SubprocessError, OSError):
        pass

    if port_in_use:
        if not dialog.yesno(
            "LXMF Port Conflict",
            "Another LXMF client is already listening on port 37428.\n\n"
            "Only one LXMF app should run at a time.\n"
            "Please stop the other client first, then retry.\n\n"
            "Continue anyway?",
        ):
            return False

    return True
