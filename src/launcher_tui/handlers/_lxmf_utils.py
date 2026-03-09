"""
LXMF Exclusivity Utility — Check for conflicting LXMF clients.

NomadNet is MeshForge's supported LXMF client.  This helper detects
if another LXMF client (e.g. a manually-installed MeshChat) is using
port 37428 and offers to stop it before starting NomadNet.
"""

import subprocess
import time


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

    # Check if another LXMF client (e.g. meshchat.py) is running
    other_running = False
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'meshchat.py'],
            capture_output=True, text=True, timeout=5,
        )
        other_running = (
            result.returncode == 0 and
            bool(result.stdout.strip())
        )
    except (subprocess.SubprocessError, OSError):
        pass

    if other_running:
        if not dialog.yesno(
            "LXMF Client Running",
            "Another LXMF client (meshchat.py) is running.\n\n"
            "Only one LXMF app should run at a time\n"
            "to avoid port 37428 conflicts.\n\n"
            "Stop it and start NomadNet?",
        ):
            return False
        try:
            subprocess.run(
                ['pkill', '-f', 'meshchat.py'],
                capture_output=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(2)

    return True
