"""Gateway Pre-Flight only invites a test send when the gateway RUNS
(2026-09-25, port of MeshForge c98dd56b)."""
import os
import sys
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, "..", "src"), os.path.join(HERE, "..", "src", "launcher_tui")):
    if p not in sys.path:
        sys.path.insert(0, p)


# --- the gateway hash only invites a test send when the gateway RUNS (2026-09-25)

def _identity_result(monkeypatch, tmp_path, running):
    from handlers import gateway_preflight as gp
    (tmp_path / "gateway_identity").write_bytes(b"x")
    monkeypatch.setattr(gp, "_gateway_identity_path", lambda: tmp_path / "gateway_identity")
    fake_rns = MagicMock()
    fake_rns.Destination.hash.return_value = bytes.fromhex("9ef9c158f88e9e29350cfc1e55b9d581")
    monkeypatch.setattr(gp, "safe_import", lambda name: (fake_rns, True))
    monkeypatch.setattr(gp.GatewayPreflightHandler, "_gateway_running", staticmethod(lambda: running))
    return gp.GatewayPreflightHandler()._check_gateway_identity()


def test_hash_invites_a_test_send_only_when_the_gateway_runs(monkeypatch, tmp_path):
    from handlers import gateway_preflight as gp
    status, msg, fix = _identity_result(monkeypatch, tmp_path, True)
    assert status == gp._OK and "gateway running — send from NomadNet" in msg


def test_hash_on_a_box_without_a_running_gateway_is_a_warning(monkeypatch, tmp_path):
    from handlers import gateway_preflight as gp
    status, msg, fix = _identity_result(monkeypatch, tmp_path, False)
    assert status == gp._WARN and "nothing receives on this address" in msg
    assert "send from NomadNet" not in msg


def test_unknown_gateway_state_is_not_ok(monkeypatch, tmp_path):
    from handlers import gateway_preflight as gp
    status, msg, _ = _identity_result(monkeypatch, tmp_path, None)
    assert status == gp._WARN and "UNKNOWN" in msg
