"""Tests for utils.meshcore_positions — operator-placed MeshCore pins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.meshcore_positions import (
    MeshCorePositionStore,
    _normalize_pubkey,
)


@pytest.fixture
def store(tmp_path: Path) -> MeshCorePositionStore:
    return MeshCorePositionStore(path=tmp_path / "positions.json")


class TestNormalizePubkey:
    def test_lowercases(self):
        assert _normalize_pubkey("ABCDEF") == "abcdef"

    def test_strips_whitespace(self):
        assert _normalize_pubkey("  abc  ") == "abc"

    def test_strips_0x_prefix(self):
        assert _normalize_pubkey("0xABC") == "abc"

    def test_none_returns_empty(self):
        assert _normalize_pubkey(None) == ""


class TestPositionStore:
    def test_empty_store_returns_none_for_get(self, store):
        assert store.get("abc") is None

    def test_empty_store_lists_empty(self, store):
        assert store.list() == {}

    def test_set_then_get(self, store):
        rec = store.set("AABBCC", 21.3069, -157.8583)
        assert rec["lat"] == 21.3069
        assert rec["lon"] == -157.8583
        assert "set_at" in rec

        # get is case-insensitive on the pubkey
        got = store.get("aabbcc")
        assert got is not None
        assert got["lat"] == 21.3069

    def test_set_with_optional_fields(self, store):
        rec = store.set(
            "abc", 10.0, 20.0,
            alt=12.5, name="My Node", notes="QTH",
        )
        assert rec["alt"] == 12.5
        assert rec["name"] == "My Node"
        assert rec["notes"] == "QTH"

    def test_set_validates_lat(self, store):
        with pytest.raises(ValueError):
            store.set("abc", 91.0, 0.0)
        with pytest.raises(ValueError):
            store.set("abc", -91.0, 0.0)

    def test_set_validates_lon(self, store):
        with pytest.raises(ValueError):
            store.set("abc", 0.0, 181.0)
        with pytest.raises(ValueError):
            store.set("abc", 0.0, -181.0)

    def test_set_requires_pubkey(self, store):
        with pytest.raises(ValueError):
            store.set("", 0.0, 0.0)
        with pytest.raises(ValueError):
            store.set("   ", 0.0, 0.0)

    def test_overwrite_existing(self, store):
        store.set("abc", 1.0, 2.0)
        store.set("abc", 3.0, 4.0, name="renamed")
        rec = store.get("abc")
        assert rec["lat"] == 3.0
        assert rec["lon"] == 4.0
        assert rec["name"] == "renamed"

    def test_delete_removes_pin(self, store):
        store.set("abc", 1.0, 2.0)
        assert store.delete("abc") is True
        assert store.get("abc") is None

    def test_delete_unknown_returns_false(self, store):
        assert store.delete("nope") is False

    def test_delete_empty_pubkey_returns_false(self, store):
        assert store.delete("") is False

    def test_list_returns_copy(self, store):
        store.set("abc", 1.0, 2.0)
        store.set("def", 3.0, 4.0)
        listing = store.list()
        assert set(listing.keys()) == {"abc", "def"}
        # mutation of the returned dict doesn't affect storage
        listing.clear()
        assert len(store) == 2

    def test_persists_to_disk(self, tmp_path: Path):
        path = tmp_path / "p.json"
        s1 = MeshCorePositionStore(path=path)
        s1.set("abc", 21.3069, -157.8583, name="Test")
        # New store reads the same file
        s2 = MeshCorePositionStore(path=path)
        rec = s2.get("abc")
        assert rec["lat"] == 21.3069
        assert rec["name"] == "Test"

    def test_corrupt_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "p.json"
        path.write_text("not json {{{")
        store = MeshCorePositionStore(path=path)
        assert store.list() == {}

    def test_non_dict_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps([1, 2, 3]))
        store = MeshCorePositionStore(path=path)
        assert store.list() == {}

    def test_pubkey_normalization_with_0x(self, store):
        store.set("0xABC", 1.0, 2.0)
        assert store.get("abc") is not None
        assert store.get("ABC") is not None
        assert store.get("0xabc") is not None

    def test_atomic_write_doesnt_leave_tmp(self, tmp_path: Path):
        path = tmp_path / "p.json"
        store = MeshCorePositionStore(path=path)
        store.set("abc", 1.0, 2.0)
        leftover = list(tmp_path.glob(".meshcore_positions.*.tmp"))
        assert leftover == []

    def test_alt_and_notes_optional(self, store):
        store.set("abc", 1.0, 2.0)
        rec = store.get("abc")
        assert "alt" not in rec
        assert "notes" not in rec
        assert "name" not in rec
