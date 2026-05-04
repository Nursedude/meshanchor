"""Phase 7 — deployment profile defaults + docs (matrix pinning).

Phase 7 corrects two latent defects in `deployment_profiles.py`:

1. `GATEWAY.feature_flags["tactical"]` was True; flipped to False.
   Tactical Ops (SITREP, zones, QR, ATAK) is unrelated to bridging — flip
   on via Settings if you want it.
2. `list_profiles()` returned `[RADIO_MAPS, MONITOR, MESHCORE, ...]`;
   reordered to `[MESHCORE, RADIO_MAPS, MONITOR, GATEWAY, FULL]` so the
   Settings TUI shows the recommended MeshCore-primary default at the top.

These tests pin the entire profile matrix as a regression guard so future
phases can't silently drift it. They also assert that the foundation doc
referenced from CLAUDE.md exists on disk (Phase 7 created it; before this
PR the link was broken).
"""

import os
import sys
from pathlib import Path

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from utils.deployment_profiles import (
    PROFILES,
    ProfileDefinition,
    ProfileName,
    detect_profile,
    get_profile_by_name,
    list_profiles,
    save_profile,
    load_profile,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Feature-flag matrix per profile
# ─────────────────────────────────────────────────────────────────────


# Ground truth — keep this in lockstep with PROFILES + the foundation doc
# at .claude/foundations/deployment_profiles.md.
_EXPECTED_FLAGS = {
    ProfileName.MESHCORE: {
        "meshtastic": False, "meshcore": True, "rns": False,
        "gateway": False, "mqtt": False, "maps": False, "tactical": False,
    },
    ProfileName.RADIO_MAPS: {
        "meshtastic": False, "meshcore": True, "rns": False,
        "gateway": False, "mqtt": False, "maps": True, "tactical": False,
    },
    ProfileName.MONITOR: {
        # `meshcore=False` is intentional — see foundation doc for why.
        "meshtastic": False, "meshcore": False, "rns": False,
        "gateway": False, "mqtt": True, "maps": False, "tactical": False,
    },
    ProfileName.GATEWAY: {
        "meshtastic": True, "meshcore": True, "rns": True,
        "gateway": True, "mqtt": True, "maps": True,
        # Phase 7 correction: tactical defaults off under GATEWAY.
        "tactical": False,
    },
    ProfileName.FULL: {
        "meshtastic": True, "meshcore": True, "rns": True,
        "gateway": True, "mqtt": True, "maps": True, "tactical": True,
    },
}

# Canonical flag set every profile should expose, full stop. New flags
# should be added to every profile's dict; this catches drift.
_CANONICAL_FLAGS = frozenset({
    "meshtastic", "meshcore", "rns", "gateway", "mqtt", "maps", "tactical",
})


class TestProfileMatrix:
    @pytest.mark.parametrize("name", list(ProfileName))
    def test_profile_present_in_PROFILES(self, name):
        """Every ProfileName has a matching ProfileDefinition."""
        assert name in PROFILES, f"{name.value} missing from PROFILES dict"
        assert isinstance(PROFILES[name], ProfileDefinition)

    @pytest.mark.parametrize("name", list(ProfileName))
    def test_profile_carries_canonical_flag_set(self, name):
        """Each profile's feature_flags dict has exactly the canonical flags."""
        flags = PROFILES[name].feature_flags
        assert frozenset(flags.keys()) == _CANONICAL_FLAGS, (
            f"{name.value} flag set drifted: "
            f"missing={_CANONICAL_FLAGS - flags.keys()}, "
            f"extra={flags.keys() - _CANONICAL_FLAGS}"
        )

    @pytest.mark.parametrize("name", list(ProfileName))
    def test_flag_values_match_expected_matrix(self, name):
        actual = PROFILES[name].feature_flags
        expected = _EXPECTED_FLAGS[name]
        assert actual == expected, (
            f"{name.value} flag values diverged from the Phase 7 matrix: "
            f"{actual} vs expected {expected}"
        )

    def test_meshcore_flag_only_off_under_monitor(self):
        """MONITOR is the only profile that turns meshcore off — it's the
        'no radio required' profile by design. Guards against accidental
        drift in either direction."""
        for name, flags in _EXPECTED_FLAGS.items():
            if name is ProfileName.MONITOR:
                assert flags["meshcore"] is False
            else:
                assert flags["meshcore"] is True, (
                    f"{name.value} should have meshcore=True (only MONITOR opts out)"
                )

    def test_meshtastic_rns_gateway_only_under_bridge_profiles(self):
        """meshtastic / rns / gateway flags are on iff the profile is one
        of GATEWAY / FULL — the two profiles that surface bridging UI."""
        bridge_profiles = {ProfileName.GATEWAY, ProfileName.FULL}
        for name, flags in _EXPECTED_FLAGS.items():
            on_bridge = name in bridge_profiles
            assert flags["meshtastic"] is on_bridge, f"meshtastic wrong for {name.value}"
            assert flags["rns"] is on_bridge, f"rns wrong for {name.value}"
            assert flags["gateway"] is on_bridge, f"gateway wrong for {name.value}"

    def test_gateway_tactical_flipped_to_false(self):
        """Phase 7 correction: GATEWAY.tactical was True; should now be False.
        Tactical (SITREP/zones/QR/ATAK) is a UX surfacing concern, not a
        bridging concern. FULL still has tactical=True for the kitchen-sink
        profile."""
        assert PROFILES[ProfileName.GATEWAY].feature_flags["tactical"] is False
        assert PROFILES[ProfileName.FULL].feature_flags["tactical"] is True

    def test_maps_off_under_meshcore_and_monitor(self):
        """RADIO_MAPS / GATEWAY / FULL surface maps; MESHCORE / MONITOR do not."""
        assert PROFILES[ProfileName.MESHCORE].feature_flags["maps"] is False
        assert PROFILES[ProfileName.MONITOR].feature_flags["maps"] is False
        assert PROFILES[ProfileName.RADIO_MAPS].feature_flags["maps"] is True
        assert PROFILES[ProfileName.GATEWAY].feature_flags["maps"] is True
        assert PROFILES[ProfileName.FULL].feature_flags["maps"] is True

    def test_only_full_has_tactical_on(self):
        """Phase 7: tactical is now FULL-only. Pre-Phase-7, GATEWAY also had it."""
        for name, flags in _EXPECTED_FLAGS.items():
            if name is ProfileName.FULL:
                assert flags["tactical"] is True
            else:
                assert flags["tactical"] is False, (
                    f"{name.value} should have tactical=False (only FULL turns it on)"
                )

    def test_mqtt_off_only_under_no_mqtt_profiles(self):
        """MESHCORE + RADIO_MAPS deliberately keep mqtt off."""
        assert PROFILES[ProfileName.MESHCORE].feature_flags["mqtt"] is False
        assert PROFILES[ProfileName.RADIO_MAPS].feature_flags["mqtt"] is False
        for name in (ProfileName.MONITOR, ProfileName.GATEWAY, ProfileName.FULL):
            assert PROFILES[name].feature_flags["mqtt"] is True

    def test_all_flag_values_are_bools(self):
        """Defensive: a typo turning a flag into a truthy string would break
        feature_enabled() which uses `dict.get(name, True)` — tests would
        pass on truthy strings but the runtime would behave wrong."""
        for name in ProfileName:
            for flag, value in PROFILES[name].feature_flags.items():
                assert isinstance(value, bool), (
                    f"{name.value}.{flag} is {type(value).__name__}, expected bool"
                )


# ─────────────────────────────────────────────────────────────────────
# 2. Required + optional services per profile
# ─────────────────────────────────────────────────────────────────────


class TestRequiredServicesAndPackages:
    def test_meshcore_and_radio_maps_have_no_required_services(self):
        """MESHCORE / RADIO_MAPS both run with zero systemd services."""
        assert PROFILES[ProfileName.MESHCORE].required_services == []
        assert PROFILES[ProfileName.RADIO_MAPS].required_services == []

    def test_monitor_has_mosquitto_optional(self):
        assert "mosquitto" in PROFILES[ProfileName.MONITOR].optional_services
        # MONITOR doesn't *require* mosquitto — a user may want to point
        # at a remote broker, in which case there's no local service to
        # require. Health detection still warns when no broker is reachable.
        assert PROFILES[ProfileName.MONITOR].required_services == []

    def test_gateway_required_services_empty(self):
        """Bridge can be MeshCore<>Meshtastic OR MeshCore<>RNS — neither
        is singularly required at the profile level. See foundation doc."""
        assert PROFILES[ProfileName.GATEWAY].required_services == []
        assert set(PROFILES[ProfileName.GATEWAY].optional_services) == {
            "meshtasticd", "rnsd", "mosquitto",
        }

    def test_full_requires_rnsd_and_mosquitto(self):
        """FULL requires RNS + MQTT; meshtasticd is OPTIONAL even under
        FULL (Phase 5 decision — users may run RNS+MQTT only without a
        Meshtastic radio attached). Don't flip this without re-reading
        Phase 5 in the tracker."""
        assert set(PROFILES[ProfileName.FULL].required_services) == {"rnsd", "mosquitto"}
        assert PROFILES[ProfileName.FULL].optional_services == ["meshtasticd"]

    def test_every_profile_has_baseline_packages(self):
        """rich, yaml, requests are required by every profile."""
        for name in ProfileName:
            required = set(PROFILES[name].required_packages)
            for baseline in ("rich", "yaml", "requests"):
                assert baseline in required, (
                    f"{name.value} missing baseline package {baseline}"
                )


# ─────────────────────────────────────────────────────────────────────
# 3. list_profiles() ordering
# ─────────────────────────────────────────────────────────────────────


class TestListProfilesOrdering:
    def test_meshcore_first(self):
        """Phase 7 correction: MeshCore is the primary recommended default;
        Settings TUI uses this order verbatim for the picker."""
        ordered = list_profiles()
        assert ordered[0].name is ProfileName.MESHCORE

    def test_full_sequence_matches_phase7_ordering(self):
        ordered = [p.name for p in list_profiles()]
        assert ordered == [
            ProfileName.MESHCORE,
            ProfileName.RADIO_MAPS,
            ProfileName.MONITOR,
            ProfileName.GATEWAY,
            ProfileName.FULL,
        ]

    def test_no_profile_dropped(self):
        """Every PROFILES entry is included in list_profiles()."""
        ordered = {p.name for p in list_profiles()}
        assert ordered == set(PROFILES.keys())


# ─────────────────────────────────────────────────────────────────────
# 4. detect_profile() priority preserved
# ─────────────────────────────────────────────────────────────────────


def _patch_detection_env(monkeypatch, *,
                         has_meshtasticd=False, has_rnsd=False,
                         has_mosquitto=False, packages=()):
    """Stub out the service + package detection helpers so detect_profile()
    sees a controlled environment."""
    import utils.deployment_profiles as dp

    service_map = {
        "meshtasticd": has_meshtasticd,
        "rnsd": has_rnsd,
        "mosquitto": has_mosquitto,
    }

    def fake_service(name):
        return service_map.get(name, False)

    package_set = set(packages)

    def fake_package(name):
        return name in package_set

    monkeypatch.setattr(dp, "_check_service_available", fake_service)
    monkeypatch.setattr(dp, "_check_package", fake_package)


class TestDetectProfilePriority:
    def test_full_when_rnsd_and_mosquitto_running(self, monkeypatch):
        _patch_detection_env(monkeypatch, has_rnsd=True, has_mosquitto=True)
        assert detect_profile().name is ProfileName.FULL

    def test_gateway_when_only_meshtasticd(self, monkeypatch):
        _patch_detection_env(monkeypatch, has_meshtasticd=True)
        assert detect_profile().name is ProfileName.GATEWAY

    def test_gateway_when_only_rnsd(self, monkeypatch):
        _patch_detection_env(monkeypatch, has_rnsd=True)
        assert detect_profile().name is ProfileName.GATEWAY

    def test_radio_maps_when_folium_installed_and_no_services(self, monkeypatch):
        _patch_detection_env(monkeypatch, packages=("folium",))
        assert detect_profile().name is ProfileName.RADIO_MAPS

    def test_monitor_when_paho_only_no_meshcore(self, monkeypatch):
        _patch_detection_env(monkeypatch, packages=("paho",))
        assert detect_profile().name is ProfileName.MONITOR

    def test_meshcore_default_for_meshcore_user_without_folium(self, monkeypatch):
        """A user with the meshcore package installed but no folium and no
        services running ends up on MESHCORE — the safe default."""
        _patch_detection_env(monkeypatch, packages=("meshcore", "paho"))
        # paho present + meshcore present → falls through to default
        # (the paho-only branch requires *not* meshcore).
        assert detect_profile().name is ProfileName.MESHCORE


# ─────────────────────────────────────────────────────────────────────
# 5. Serialization + lookup helpers
# ─────────────────────────────────────────────────────────────────────


class TestProfileSerialization:
    @pytest.mark.parametrize("name", list(ProfileName))
    def test_to_dict_contains_name_and_display(self, name):
        d = PROFILES[name].to_dict()
        assert d["name"] == name.value
        assert d["display_name"] == PROFILES[name].display_name

    def test_get_profile_by_name_round_trips(self):
        for name in ProfileName:
            looked_up = get_profile_by_name(name.value)
            assert looked_up is not None
            assert looked_up.name is name

    def test_get_profile_by_name_returns_none_for_unknown(self):
        assert get_profile_by_name("not_a_profile") is None
        assert get_profile_by_name("") is None


# ─────────────────────────────────────────────────────────────────────
# 6. Doc reference integrity (regression guard against link rot)
# ─────────────────────────────────────────────────────────────────────


class TestDocReferenceIntegrity:
    def test_foundation_doc_exists_on_disk(self):
        """CLAUDE.md references `.claude/foundations/deployment_profiles.md`.
        Phase 7 created it. This test guards against future deletes."""
        repo_root = Path(__file__).parent.parent
        doc = repo_root / ".claude" / "foundations" / "deployment_profiles.md"
        assert doc.exists(), (
            f"{doc} missing — CLAUDE.md still references it"
        )
        # Sanity: not just an empty placeholder.
        assert doc.stat().st_size > 1000, (
            f"{doc} suspiciously small — should contain the full matrix"
        )

    def test_claude_md_still_references_foundation_doc(self):
        """Mirror guard: if someone removes the CLAUDE.md reference, this
        test fails so the foundation doc isn't orphaned."""
        repo_root = Path(__file__).parent.parent
        claude_md = repo_root / "CLAUDE.md"
        content = claude_md.read_text()
        assert ".claude/foundations/deployment_profiles.md" in content, (
            "CLAUDE.md no longer references the foundation doc — "
            "either restore the reference or move the doc"
        )
