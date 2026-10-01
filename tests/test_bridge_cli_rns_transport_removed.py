"""bridge_mode=rns_transport must refuse to start, never fall back.

The RNS-over-Meshtastic transport (gateway/rns_transport.py) was removed
2026-10-01: it never handed a packet to RNS. A config that still names it
must stop with the reason before any preflight or bridge is built.
"""

from unittest.mock import patch

import pytest

from gateway import bridge_cli
from gateway.config import GatewayConfig

# preflight is stubbed to FAIL in every test here: if the refusal regresses,
# main() must stop at preflight instead of starting a LIVE bridge (2026-10-01:
# the red drill on the old code did exactly that against this box's rnsd and
# meshtasticd until it was killed).


def _config(mode):
    cfg = GatewayConfig()
    cfg.bridge_mode = mode
    return cfg


def test_rns_transport_mode_exits_with_the_reason(capsys):
    with patch.object(GatewayConfig, "load", return_value=_config("rns_transport")), \
         patch.object(bridge_cli, "preflight_checks", return_value=False) as preflight:
        with pytest.raises(SystemExit) as exc:
            bridge_cli.main()
    assert exc.value.code == 1
    preflight.assert_not_called()
    assert bridge_cli.RNS_TRANSPORT_REMOVED in capsys.readouterr().out


def test_removed_transport_is_not_importable():
    with pytest.raises(ImportError):
        from gateway import rns_transport  # noqa: F401


def test_other_modes_reach_preflight_control():
    """Control: the refusal is specific to rns_transport — another mode goes
    on to preflight (stubbed to fail so main() stops there)."""
    with patch.object(GatewayConfig, "load", return_value=_config("message_bridge")), \
         patch.object(bridge_cli, "preflight_checks", return_value=False) as preflight:
        with pytest.raises(SystemExit):
            bridge_cli.main()
    preflight.assert_called_once()


def test_mixed_case_mode_is_refused_too(capsys):
    with patch.object(GatewayConfig, "load", return_value=_config("RNS_Transport")), \
         patch.object(bridge_cli, "preflight_checks", return_value=False) as preflight:
        with pytest.raises(SystemExit) as exc:
            bridge_cli.main()
    assert exc.value.code == 1
    preflight.assert_not_called()


def test_no_offered_template_selects_the_removed_transport():
    """Review pair 2026-10-01: the rns_over_mesh template survived R1."""
    names = list(GatewayConfig.get_available_templates())
    assert names
    for name in names:
        cfg = GatewayConfig.from_template(name)
        assert (cfg.bridge_mode or "").lower() != "rns_transport", name
    assert GatewayConfig.from_template("rns_over_mesh") is None


@pytest.mark.parametrize("mode", ["rns_transport", "RNS_Transport"])
def test_validator_names_the_removal(mode):
    from gateway.config_validators import RNS_TRANSPORT_REMOVED, validate_bridge_mode

    err = validate_bridge_mode(mode, "bridge_mode")
    assert err is not None and err.severity == "error"
    assert err.message == RNS_TRANSPORT_REMOVED
    assert bridge_cli.RNS_TRANSPORT_REMOVED is RNS_TRANSPORT_REMOVED
