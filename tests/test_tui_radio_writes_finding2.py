"""TUI audit finding 2 (2026-09-27) — MeshAnchor twin of MeshForge 949736ee.

MeshAnchor's shape differs: no meshtasticd_radio handler (so no HAT-swap
confirm and no LoRa builder here), and radio_menu's owner writer is the ONLY
one — so instead of delegating, it now carries MeshForge's hardened writer
verbatim. These are MeshForge's 2026-09-20 owner-writer tests, driven
through MeshAnchor's real entry point, `RadioMenuHandler._radio_set_name`.
"""

import glob
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

import yaml

from handler_test_utils import make_handler_context  # noqa: E402
from commands import meshtastic as _mesh_cmd_module  # noqa: E402
from commands.base import CommandResult  # noqa: E402

REPO = os.path.join(os.path.dirname(__file__), '..')


class TestNoKernelChipSelectInOurPresets:
    PRESETS = sorted(glob.glob(os.path.join(REPO, "templates", "meshforge-presets", "*.yaml")))

    def test_scanner_sees_presets(self):
        assert len(self.PRESETS) >= 4

    def test_no_preset_claims_gpio8(self):
        for p in self.PRESETS:
            cs = (yaml.safe_load(open(p)).get("Lora") or {}).get("CS")
            assert cs != 8, f"{os.path.basename(p)} claims CS: 8 (SPI0 CE0)"


_INFO_RAW = """Connected to radio

Owner: Kona Base (KONA)
My info: { "myNodeNum": 1644220965, "rebootCount": 3 }

Nodes in mesh: {
  "!6201ce25": {
    "user": {"id": "!6201ce25", "longName": "Kona Base", "shortName": "KONA"}
  },
  "!699aeb50": {
    "user": {"id": "!699aeb50", "longName": "GreenPanda", "shortName": "GPND"}
  }
}
"""


def _make_handler():
    from handlers.radio_menu import RadioMenuHandler
    h = RadioMenuHandler()
    ctx = make_handler_context()
    ctx.src_dir = Path(__file__).parent.parent / "src"
    h.set_context(ctx)
    return h


class TestOwnerWriterThroughRadioMenu:
    def _run(self, raw, inputs, info_ok=True, persisted=True):
        h = _make_handler()
        h.ctx.dialog._inputbox_returns = list(inputs)   # [] = accept the defaults
        calls = {}
        info = CommandResult(success=info_ok, message="", raw_output=raw)
        with patch.object(_mesh_cmd_module, 'get_node_info', return_value=info), \
             patch.object(_mesh_cmd_module, 'set_owner',
                          side_effect=lambda n: (calls.__setitem__('long', n), CommandResult.ok("OK"))[1]), \
             patch.object(_mesh_cmd_module, 'set_owner_short',
                          side_effect=lambda n: (calls.__setitem__('short', n), CommandResult.ok("OK"))[1]), \
             patch('utils.device_config_store.save_device_settings', return_value=persisted), \
             patch('subprocess.run') as run:
            h._radio_set_name()
        run.assert_not_called()          # no raw `meshtastic --set-owner` anymore
        inits = [kw['init'] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']
        return h, inits, calls

    def test_prefill_is_the_owner_line_not_the_last_node(self):
        h, inits, calls = self._run(_INFO_RAW, inputs=[])
        assert inits == ["Kona Base", "KONA"], inits
        assert calls == {}, calls        # accepting the defaults writes nothing

    def test_only_the_changed_field_is_written(self):
        h, inits, calls = self._run(_INFO_RAW, inputs=["Kona Relay", "KONA"])
        assert calls == {'long': "Kona Relay"}, calls

    def test_a_double_quote_in_the_name_is_refused(self):
        h, inits, calls = self._run(_INFO_RAW, inputs=['GreenPanda",', 'GPND'])
        assert calls == {}
        assert h.ctx.dialog.last_msgbox_title == "Error"

    def test_failed_read_says_unknown_never_none(self):
        h, inits, calls = self._run("", inputs=[], info_ok=False)
        prompts = [args[1] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']
        assert all("UNKNOWN" in p for p in prompts), prompts
        assert calls == {}

    def test_persistence_failure_is_partial_success(self):
        h, inits, calls = self._run(_INFO_RAW, inputs=["Kona Relay", "KONA"], persisted=False)
        assert h.ctx.dialog.last_msgbox_title == "Partial Success"

    def test_no_raw_set_owner_argv_left(self):
        from handlers import radio_menu
        src = inspect.getsource(radio_menu)
        assert "'--set-owner'" not in src and '"--set-owner"' not in src
