"""Root Context Warning must not promise a workaround it never performs.

Twin of MeshForge a088e33a (tests/test_tui_startup_honesty.py
TestRootContextWarningClaimsOnlyWhatItDoes). The old text said
"MeshAnchor will try to work around this" — no code after the msgbox
does anything: an instrument claiming an action it does not take.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import FakeDialog


def test_mismatch_warning_promises_no_workaround():
    import main as tui_main
    dialog = FakeDialog()
    fake_self = SimpleNamespace(dialog=dialog)
    done = SimpleNamespace(returncode=0, stdout="wh6gxz\n")
    env = {k: v for k, v in os.environ.items() if k != 'SUDO_USER'}
    with patch.object(tui_main.os, 'getuid', return_value=0), \
            patch.dict(tui_main.os.environ, env, clear=True), \
            patch.object(tui_main.subprocess, 'run', return_value=done):
        tui_main.MeshAnchorLauncher._check_root_without_sudo_user(fake_self)
    assert dialog.last_msgbox_title == "Root Context Warning"
    text = dialog.last_msgbox_text.lower()
    assert "work around" not in text and "workaround" not in text, (
        "the warning claims a workaround no code performs"
    )
    assert "sudo -e meshanchor" in text
    assert "rpc" in text
