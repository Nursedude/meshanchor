"""NOC-grant step 4 (port of MeshForge F1, 2026-10-14 → MA 2026-10-15): an
unreadable LXMF broadcast identity is REFUSED, never recreated — an identity
file IS the box's mesh address. MA's loader was the pre-F1 shape and worse:
`RNS.Identity.from_file` returns None for a corrupt file (it never raises),
so a zero-byte corpse came back as identity None. Wanted: refuse to start,
keep the corpse byte-for-byte, leave a witness (status + counter + ERROR)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fake_rns_identity import KEY_BYTES, make_identity_class  # noqa: E402
from gateway.config import LXMFBroadcastConfig  # noqa: E402
from gateway.lxmf_broadcast_bridge import LXMFBroadcastBridge  # noqa: E402

OP = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _allow_rns_tx():
    from utils import tx_guard
    with tx_guard.allow_rns_egress():
        yield


def _rns():
    rns = MagicMock(name="RNS")
    rns.Identity = make_identity_class()
    return rns


def _lxmf():
    lxmf = MagicMock(name="LXMF")
    router = MagicMock(name="LXMRouter")
    src = MagicMock()
    src.hash = bytes.fromhex(OP)
    router.register_delivery_identity.return_value = src
    lxmf.LXMRouter.return_value = router
    return lxmf, router


def _bridge(tmp_path, path, lxmf):
    cfg = LXMFBroadcastConfig(enabled=True, channels=[0], announce_interval_sec=0)
    return LXMFBroadcastBridge(cfg, rns_module=_rns(), lxmf_module=lxmf,
                               identity_path=path, db_path=tmp_path / "subs.db",
                               storage_path=tmp_path / "storage")


@pytest.mark.parametrize("corpse", [b"", b"\x00" * 64 + b"x", b"short"])
def test_an_unreadable_identity_refuses_and_keeps_the_corpse(tmp_path, corpse):
    lxmf, router = _lxmf()
    path = tmp_path / "identity"
    path.write_bytes(corpse)
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    b = _bridge(tmp_path, path, lxmf)
    assert b.start() is False
    router.register_delivery_identity.assert_not_called()
    st = path.stat()
    assert (path.read_bytes(), st.st_ino, st.st_mtime_ns) == before
    status = b.get_status()
    assert status["identity_error"] and str(path) in status["identity_error"]
    assert status["stats"]["identity_refused"] == 1


def test_an_absent_identity_is_created(tmp_path):
    lxmf, router = _lxmf()
    path = tmp_path / "identity"
    b = _bridge(tmp_path, path, lxmf)
    try:
        assert b.start() is True
        assert len(path.read_bytes()) == KEY_BYTES
        assert b.get_status()["identity_error"] is None
    finally:
        b.stop()
