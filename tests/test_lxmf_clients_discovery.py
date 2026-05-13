"""Tests for handlers._lxmf_clients_discovery.

The discovery module is best-effort — every probe must silently return
[] on missing/invalid input. Discovery feeds the "Subscribe Local Client"
picker, so a single bad probe must not crash the menu.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "launcher_tui"))


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point `get_real_user_home()` at a tmp dir so probes look there."""
    monkeypatch.setattr(
        "utils.paths.get_real_user_home", lambda: tmp_path
    )
    # _lxmf_clients_discovery imports get_real_user_home at module-load
    # time; also patch the bound import so probes see the override.
    monkeypatch.setattr(
        "handlers._lxmf_clients_discovery.get_real_user_home", lambda: tmp_path
    )
    monkeypatch.setattr(
        "handlers._lxmf_clients_discovery.MeshChatXPaths.get_storage_dir",
        classmethod(lambda cls: tmp_path / ".local" / "share" / "meshchatx"),
    )
    return tmp_path


# ── NomadNet probe ───────────────────────────────────────────────────


class TestNomadnetProbe:
    def test_no_logfile_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_nomadnet
        assert _probe_nomadnet() == []

    def test_logfile_without_hash_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_nomadnet
        logdir = fake_home / ".nomadnetwork"
        logdir.mkdir()
        (logdir / "logfile").write_text("just some boring log lines\n")
        assert _probe_nomadnet() == []

    def test_extracts_last_hash_from_logfile(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_nomadnet
        logdir = fake_home / ".nomadnetwork"
        logdir.mkdir()
        (logdir / "logfile").write_text(
            "earlier line\n"
            "[2026-05-12 12:00:00] LXMF Router ready to receive on: <abc123def4567890>\n"
            "[2026-05-12 12:01:00] LXMF Router ready to receive on: <deadbeef00112233>\n"
            "later line\n"
        )
        out = _probe_nomadnet()
        assert len(out) == 1
        assert out[0].name == "NomadNet"
        assert out[0].hash == "deadbeef00112233"


# ── MeshChatX probe ──────────────────────────────────────────────────


class TestMeshchatxProbe:
    def test_no_storage_dir_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_meshchatx
        assert _probe_meshchatx() == []

    def test_storage_without_hash_file_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_meshchatx
        storage = fake_home / ".local" / "share" / "meshchatx"
        storage.mkdir(parents=True)
        assert _probe_meshchatx() == []

    def test_reads_hash_from_lxmf_hash_file(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_meshchatx
        storage = fake_home / ".local" / "share" / "meshchatx"
        storage.mkdir(parents=True)
        (storage / "lxmf_hash").write_text("AABBCCDD11223344\n")
        out = _probe_meshchatx()
        assert len(out) == 1
        assert out[0].name == "MeshChatX"
        assert out[0].hash == "aabbccdd11223344"


# ── User-config seam ────────────────────────────────────────────────


class TestUserConfigProbe:
    def test_no_file_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_user_config
        assert _probe_user_config() == []

    def test_invalid_json_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_user_config
        config_dir = fake_home / ".config" / "meshanchor"
        config_dir.mkdir(parents=True)
        (config_dir / "local_lxmf_clients.json").write_text("{not valid json")
        assert _probe_user_config() == []

    def test_non_list_returns_empty(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_user_config
        config_dir = fake_home / ".config" / "meshanchor"
        config_dir.mkdir(parents=True)
        (config_dir / "local_lxmf_clients.json").write_text('{"a": "b"}')
        assert _probe_user_config() == []

    def test_loads_valid_entries(self, fake_home):
        from handlers._lxmf_clients_discovery import _probe_user_config
        config_dir = fake_home / ".config" / "meshanchor"
        config_dir.mkdir(parents=True)
        (config_dir / "local_lxmf_clients.json").write_text(
            json.dumps(
                [
                    {"name": "MyClient", "hash": "aaaa000011112222"},
                    {"name": "BadEntry", "hash": "notavalidhash!!"},
                    {"name": "ShortHash", "hash": "abcd"},
                    {"name": "AnotherClient", "hash": "bbbb000011112222"},
                ]
            )
        )
        out = _probe_user_config()
        names = {c.name for c in out}
        # Only the two valid entries survive
        assert names == {"MyClient", "AnotherClient"}


# ── Top-level discover ──────────────────────────────────────────────


class TestDiscoverLocalLxmfClients:
    def test_dedups_on_hash(self, fake_home):
        from handlers._lxmf_clients_discovery import discover_local_lxmf_clients

        # NomadNet logs hash X
        logdir = fake_home / ".nomadnetwork"
        logdir.mkdir()
        (logdir / "logfile").write_text(
            "LXMF Router ready to receive on: <deadbeef00112233>\n"
        )
        # User config also registers the same hash under a different name
        cfg_dir = fake_home / ".config" / "meshanchor"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "local_lxmf_clients.json").write_text(
            json.dumps([{"name": "DuplicateClient", "hash": "deadbeef00112233"}])
        )

        out = discover_local_lxmf_clients()
        assert len(out) == 1
        # NomadNet (the first probe) wins the label
        assert out[0].name == "NomadNet"

    def test_probe_exception_does_not_kill_discovery(self, fake_home):
        """A buggy probe must not crash discovery — return [] and move on."""
        from handlers import _lxmf_clients_discovery as mod

        def _explode():
            raise RuntimeError("probe blew up")

        with patch.object(mod, "_PROBES", (_explode, mod._probe_user_config)):
            # Should not raise
            out = mod.discover_local_lxmf_clients()
            assert out == []

    def test_returns_empty_when_nothing_present(self, fake_home):
        from handlers._lxmf_clients_discovery import discover_local_lxmf_clients
        assert discover_local_lxmf_clients() == []
