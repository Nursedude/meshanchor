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


# ---------------------------------------------------------------------------
# Primary channel writer — MeshForge 43b2b08b, missed by this port (found by
# the 2026-09-29 non-author double-tap: one Enter renamed channel 0 to
# "MeshAnchor", and a renamed primary changes the channel hash, so every peer
# on the mesh stops hearing the box).
# ---------------------------------------------------------------------------

_CH_NAMED = 'Index 0: PRIMARY psk=default { "psk": "AQ==", "name": "Fleet0" }'
_CH_UNNAMED = 'Index 0: PRIMARY psk=default { "psk": "AQ==" }'


class TestPrimaryChannelOneEnter:
    def _run(self, raw, inputs, yesno=(), info_ok=True):
        from handlers.channel_config import ChannelConfigHandler
        h = ChannelConfigHandler()
        ctx = make_handler_context()
        ctx.src_dir = Path(__file__).parent.parent / "src"
        h.set_context(ctx)
        h.ctx.dialog._inputbox_returns = list(inputs)   # [] = press Enter on the pre-fill
        h.ctx.dialog._yesno_returns = list(yesno)       # [] = the dialog's default (No)
        writes = []
        info = CommandResult(success=info_ok, message="", raw_output=raw)
        with patch.object(_mesh_cmd_module, 'get_node_info', return_value=info), \
             patch.object(_mesh_cmd_module, 'set_channel_name',
                          side_effect=lambda i, n: (writes.append((i, n)), CommandResult.ok("OK"))[1]):
            h._set_primary_channel()
        inits = [kw['init'] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']
        return h, inits, writes

    def test_one_enter_writes_nothing_and_prefills_the_current_name(self):
        h, inits, writes = self._run(_CH_NAMED, inputs=[])
        assert inits == ["Fleet0"], inits
        assert writes == [], writes

    def test_never_prefills_the_brand_name(self):
        for raw, ok in ((_CH_NAMED, True), (_CH_UNNAMED, True), ("", False)):
            h, inits, writes = self._run(raw, inputs=[], info_ok=ok)
            assert "MeshAnchor" not in inits, inits
            assert writes == [], writes

    def test_a_changed_name_needs_an_explicit_yes(self):
        h, inits, writes = self._run(_CH_NAMED, inputs=["Fleet1"])        # confirm defaults No
        assert writes == []
        confirms = [kw for name, args, kw in h.ctx.dialog.calls if name == 'yesno']
        assert confirms and confirms[0].get('default_no') is True, confirms

    def test_deliberate_confirmed_change_is_written(self):
        h, inits, writes = self._run(_CH_NAMED, inputs=["Fleet1"], yesno=[True])
        assert writes == [(0, "Fleet1")], writes

    def test_failed_read_says_unknown(self):
        h, inits, writes = self._run("", inputs=[], info_ok=False)
        prompts = [args[1] for name, args, kw in h.ctx.dialog.calls if name == 'inputbox']
        assert prompts and "UNKNOWN" in prompts[0], prompts
        assert writes == []

    def test_parse_three_states(self):
        from handlers.channel_config import ChannelConfigHandler
        assert ChannelConfigHandler._parse_primary_name(_CH_NAMED) == "Fleet0"
        assert ChannelConfigHandler._parse_primary_name(_CH_UNNAMED) == ""
        assert ChannelConfigHandler._parse_primary_name("Owner: x (y)") is None


# Reader-pair findings on the port (2026-09-29): the CLI prints channel JSON
# through protobuf json_format (ensure_ascii), so a Hawaiian name arrives as
# \uXXXX escapes and a '"' as \" — a regex capture handed back ESCAPE TEXT.
def _ch0_line(name):
    # json.dumps(ensure_ascii=True) is the escaping json_format really emits;
    # generated at run time so no editor or tool can pre-decode the escapes.
    import json
    return ('  Index 0: PRIMARY psk=default { "psk": "AQ==", "name": '
            + json.dumps(name, ensure_ascii=True) + ', "channelNum": 0 }')


_CH_HAWAIIAN = _ch0_line("hōkū")
_CH_QUOTE = _ch0_line('a"b')
assert "\\u014d" in _CH_HAWAIIAN, "fixture lost its escapes — it would test nothing"
_CH_TRAILING = '  Index 0: PRIMARY psk=default { "psk": "AQ==", "name": "Fleet0 ", "channelNum": 0 }'


class TestPrimaryChannelReaderPair:
    def test_parse_unescapes_json(self):
        from handlers.channel_config import ChannelConfigHandler
        assert ChannelConfigHandler._parse_primary_name(_CH_HAWAIIAN) == "hōkū"
        assert ChannelConfigHandler._parse_primary_name(_CH_QUOTE) == 'a"b'

    def test_prefill_is_the_real_name_not_escape_text(self):
        h, inits, writes = TestPrimaryChannelOneEnter()._run(_CH_HAWAIIAN, inputs=[])
        assert inits == ["hōkū"], inits
        assert writes == []

    def test_trailing_space_on_the_radio_is_no_change(self):
        h, inits, writes = TestPrimaryChannelOneEnter()._run(_CH_TRAILING, inputs=[], yesno=[True])
        assert writes == []
        assert not [c for c in h.ctx.dialog.calls if c[0] == 'yesno'], "confirm shown for no change"

    def test_name_over_11_utf8_bytes_is_refused_not_truncated(self):
        # nanopb ChannelSettings.name max_size:12 = 11 bytes + NUL.
        # 12 bytes / 12 cp; and 9 code points that are 14 UTF-8 bytes
        for too_long in ("TwelveChars!", "hōkūlani🌺"):
            assert len(too_long.encode()) > 11
            h, inits, writes = TestPrimaryChannelOneEnter()._run(_CH_NAMED, inputs=[too_long], yesno=[True])
            assert writes == [], (too_long, writes)
        h, inits, writes = TestPrimaryChannelOneEnter()._run(_CH_NAMED, inputs=["hōkū"], yesno=[True])
        assert writes == [(0, "hōkū")], writes

    def test_the_slow_read_is_announced(self):
        h, inits, writes = TestPrimaryChannelOneEnter()._run(_CH_NAMED, inputs=[])
        names = [c[0] for c in h.ctx.dialog.calls]
        assert "infobox" in names and names.index("infobox") < names.index("inputbox"), names
