"""Allowlisted MeshCore contact capture (meshcore_contact_capture_mixin).

Pins the portable-shape contract from 2026-08-29: a peer listed in
``meshcore.repliable_contacts`` whose advert reaches the radio as a pending
contact is stored via ``add_contact`` (becoming DM-able for the reply leg);
everything else — unlisted peers, already-stored contacts, an empty
allowlist, a radio that errors — leaves the contact DB alone, and every add
attempt leaves a stat witness.
"""

import asyncio
import threading
from types import SimpleNamespace

from gateway.meshcore_handler import MeshCoreHandler


def _contact(name="p1", pk="ab" * 32):
    return {
        "adv_name": name, "public_key": pk, "type": 1, "flags": 0,
        "out_path_len": -1, "out_path": "", "last_advert": 0,
        "adv_lat": 0.0, "adv_lon": 0.0,
    }


class _FakeCommands:
    def __init__(self, result_type="ok"):
        self.added = []
        self._result_type = result_type

    async def add_contact(self, contact):
        self.added.append(contact)
        return SimpleNamespace(type=self._result_type)


class _FakeMeshCore:
    def __init__(self, pending=None, stored=None, result_type="ok"):
        self.commands = _FakeCommands(result_type)
        self.pending_contacts = dict(pending or {})
        self.contacts = dict(stored or {})
        self.subscribed = []

    def subscribe(self, event_type, cb):
        self.subscribed.append((event_type, cb))
        return object()


def _bare_handler(allowlist, mc=None):
    h = MeshCoreHandler.__new__(MeshCoreHandler)
    h.stats = {}
    h._stats_lock = threading.Lock()
    h._subscriptions = []
    h._meshcore = mc if mc is not None else _FakeMeshCore()
    h.config = SimpleNamespace(
        meshcore=SimpleNamespace(repliable_contacts=allowlist))
    return h


class _EventType:
    NEW_CONTACT = "new_contact"


class TestInit:
    def test_empty_allowlist_subscribes_nothing(self):
        mc = _FakeMeshCore()
        h = _bare_handler([], mc)
        h._init_contact_capture(_EventType)
        assert mc.subscribed == []
        assert h._subscriptions == []

    def test_allowlist_subscribes_new_contact(self):
        mc = _FakeMeshCore()
        h = _bare_handler(["p1"], mc)
        h._init_contact_capture(_EventType)
        assert [e for e, _ in mc.subscribed] == ["new_contact"]
        assert len(h._subscriptions) == 1

    def test_subscribe_failure_is_witnessed_not_raised(self):
        mc = _FakeMeshCore()
        mc.subscribe = None  # not callable -> TypeError inside
        h = _bare_handler(["p1"], mc)
        h._init_contact_capture(_EventType)  # must not raise
        assert h._subscriptions == []


class TestCapture:
    def test_listed_pending_contact_is_added(self):
        mc = _FakeMeshCore()
        h = _bare_handler(["p1"], mc)
        asyncio.run(h._on_new_contact(
            SimpleNamespace(payload=_contact("p1"))))
        assert len(mc.commands.added) == 1
        assert h.stats["meshcore_contact_captured"] == 1

    def test_pubkey_prefix_entry_matches(self):
        mc = _FakeMeshCore()
        h = _bare_handler(["abab"], mc)
        asyncio.run(h._on_new_contact(
            SimpleNamespace(payload=_contact("whoever", "ab" * 32))))
        assert len(mc.commands.added) == 1

    def test_unlisted_contact_is_ignored(self):
        mc = _FakeMeshCore()
        h = _bare_handler(["p1"], mc)
        asyncio.run(h._on_new_contact(
            SimpleNamespace(payload=_contact("stranger", "cd" * 32))))
        assert mc.commands.added == []
        assert "meshcore_contact_captured" not in h.stats

    def test_already_stored_contact_is_skipped(self):
        pk = "ab" * 32
        mc = _FakeMeshCore(stored={pk: _contact("p1", pk)})
        h = _bare_handler(["p1"], mc)
        asyncio.run(h._on_new_contact(
            SimpleNamespace(payload=_contact("p1", pk))))
        assert mc.commands.added == []

    def test_radio_error_leaves_failed_stat(self):
        mc = _FakeMeshCore(result_type="error")
        h = _bare_handler(["p1"], mc)
        asyncio.run(h._on_new_contact(
            SimpleNamespace(payload=_contact("p1"))))
        assert h.stats["meshcore_contact_capture_failed"] == 1
        assert "meshcore_contact_captured" not in h.stats

    def test_non_dict_payload_is_ignored(self):
        mc = _FakeMeshCore()
        h = _bare_handler(["p1"], mc)
        asyncio.run(h._on_new_contact(SimpleNamespace(payload=None)))
        assert mc.commands.added == []


class TestPendingSweep:
    def test_sweep_captures_listed_backlog_only(self):
        listed = _contact("p1", "ab" * 32)
        unlisted = _contact("stranger", "cd" * 32)
        mc = _FakeMeshCore(pending={
            listed["public_key"]: listed,
            unlisted["public_key"]: unlisted,
        })
        h = _bare_handler(["p1"], mc)
        asyncio.run(h._sweep_pending_contacts())
        assert [c["adv_name"] for c in mc.commands.added] == ["p1"]

    def test_init_schedules_sweep_when_loop_runs(self):
        listed = _contact("p1")
        mc = _FakeMeshCore(pending={listed["public_key"]: listed})
        h = _bare_handler(["p1"], mc)

        async def run():
            h._init_contact_capture(_EventType)
            await asyncio.sleep(0)  # let the sweep task run

        asyncio.run(run())
        assert len(mc.commands.added) == 1


class TestConfigDefault:
    def test_config_default_is_empty_list(self):
        from gateway.config import GatewayConfig
        cfg = GatewayConfig()
        assert cfg.meshcore.repliable_contacts == []
