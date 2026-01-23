"""
Tests for expanded Knowledge Base content.

Tests verify:
- New entries load correctly
- Keyword search finds new content
- RNS troubleshooting guides work
- AREDN entries are queryable
- RF fundamentals expansion is accessible
- MQTT entries are findable
- Cross-references are valid

Run with: pytest tests/test_knowledge_base_expanded.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.knowledge_base import (
    KnowledgeBase, KnowledgeTopic, KnowledgeEntry, TroubleshootingGuide
)


@pytest.fixture
def kb():
    """Create a fresh knowledge base."""
    return KnowledgeBase()


class TestKnowledgeBaseSize:
    """Verify expanded knowledge base meets targets."""

    def test_minimum_entries(self, kb):
        """Should have 30+ entries."""
        assert len(kb._entries) >= 30

    def test_minimum_guides(self, kb):
        """Should have 5+ troubleshooting guides."""
        assert len(kb._guides) >= 5

    def test_all_topics_covered(self, kb):
        """All major topics should have entries."""
        topics = set(e.topic for e in kb._entries.values())
        assert KnowledgeTopic.RF_FUNDAMENTALS in topics
        assert KnowledgeTopic.MESHTASTIC in topics
        assert KnowledgeTopic.RETICULUM in topics
        assert KnowledgeTopic.NETWORKING in topics  # AREDN
        assert KnowledgeTopic.MQTT in topics

    def test_rf_fundamentals_rich(self, kb):
        """RF fundamentals should have 10+ entries."""
        rf_entries = kb.get_entries_by_topic(KnowledgeTopic.RF_FUNDAMENTALS)
        assert len(rf_entries) >= 10

    def test_reticulum_rich(self, kb):
        """Reticulum should have 5+ entries."""
        rns_entries = kb.get_entries_by_topic(KnowledgeTopic.RETICULUM)
        assert len(rns_entries) >= 5


class TestRNSTroubleshooting:
    """Test RNS troubleshooting content."""

    def test_rns_identity_entry(self, kb):
        entry = kb.get_entry("RNS Identity Management")
        assert entry is not None
        assert "curve25519" in entry.content.lower() or "Curve25519" in entry.content
        assert "back up" in entry.content.lower()

    def test_rns_transport_entry(self, kb):
        entry = kb.get_entry("RNS Transport and Routing")
        assert entry is not None
        assert "transport" in entry.content.lower()
        assert "path table" in entry.content.lower() or "path_table" in entry.content.lower()

    def test_lxmf_entry(self, kb):
        entry = kb.get_entry("LXMF Message Protocol")
        assert entry is not None
        assert "propagation" in entry.content.lower()
        assert "delivery" in entry.content.lower()

    def test_rnsd_troubleshooting_guide(self, kb):
        guide = kb.get_troubleshooting_guide("rnsd_not_starting")
        assert guide is not None
        assert len(guide.steps) >= 4
        assert any("systemctl" in (s.command or "") for s in guide.steps)

    def test_rns_path_failure_guide(self, kb):
        guide = kb.get_troubleshooting_guide("rns_path_failure")
        assert guide is not None
        assert len(guide.steps) >= 4
        assert "offline" in guide.steps[0].if_fail or "destination" in guide.description.lower()

    def test_rns_interface_config_guide(self, kb):
        guide = kb.get_troubleshooting_guide("rns_interface_config")
        assert guide is not None
        assert len(guide.steps) >= 4

    def test_query_rns_identity(self, kb):
        """Should find identity info when querying about RNS keys."""
        results = kb.query("RNS identity keys backup")
        assert len(results) > 0
        titles = [r[0].title for r in results]
        assert any("Identity" in t for t in titles)

    def test_query_lxmf(self, kb):
        """Should find LXMF info."""
        results = kb.query("LXMF message delivery offline")
        assert len(results) > 0
        titles = [r[0].title for r in results]
        assert any("LXMF" in t for t in titles)

    def test_query_rns_routing(self, kb):
        """Should find transport/routing info."""
        results = kb.query("reticulum transport routing path")
        assert len(results) > 0


class TestAREDNKnowledge:
    """Test AREDN entries."""

    def test_aredn_overview(self, kb):
        entry = kb.get_entry("AREDN Network Overview")
        assert entry is not None
        assert "amateur radio" in entry.content.lower() or "AREDN" in entry.content
        assert "olsr" in entry.content.lower() or "OLSR" in entry.content
        assert entry.topic == KnowledgeTopic.NETWORKING

    def test_aredn_discovery(self, kb):
        entry = kb.get_entry("AREDN Node Discovery")
        assert entry is not None
        assert "topology" in entry.content.lower()
        assert "9090" in entry.content  # OLSR port

    def test_aredn_services(self, kb):
        entry = kb.get_entry("AREDN Services")
        assert entry is not None
        assert "voip" in entry.content.lower() or "VoIP" in entry.content
        assert "meshchat" in entry.content.lower() or "MeshChat" in entry.content

    def test_query_aredn(self, kb):
        """Should find AREDN info when querying."""
        results = kb.query("AREDN mesh network ham radio")
        assert len(results) > 0
        titles = [r[0].title for r in results]
        assert any("AREDN" in t for t in titles)

    def test_query_olsr(self, kb):
        """Should find AREDN discovery when querying OLSR."""
        results = kb.query("OLSR topology discovery")
        assert len(results) > 0


class TestRFFundamentalsExpanded:
    """Test expanded RF fundamentals."""

    def test_fspl_entry(self, kb):
        entry = kb.get_entry("Free Space Path Loss (FSPL)")
        assert entry is not None
        assert "32.44" in entry.content  # FSPL constant
        assert "link budget" in entry.content.lower()

    def test_antenna_types(self, kb):
        entry = kb.get_entry("Antenna Types for LoRa")
        assert entry is not None
        assert "yagi" in entry.content.lower() or "Yagi" in entry.content
        assert "omnidirectional" in entry.content.lower()
        assert "dbi" in entry.content.lower() or "dBi" in entry.content

    def test_propagation_models(self, kb):
        entry = kb.get_entry("RF Propagation Models")
        assert entry is not None
        assert "friis" in entry.content.lower() or "Friis" in entry.content
        assert "hata" in entry.content.lower() or "Hata" in entry.content

    def test_ism_regulations(self, kb):
        entry = kb.get_entry("ISM Band Regulations")
        assert entry is not None
        assert "915" in entry.content  # US frequency
        assert "868" in entry.content  # EU frequency
        assert "duty cycle" in entry.content.lower()

    def test_link_budget_calculation(self, kb):
        entry = kb.get_entry("LoRa Link Budget Calculation")
        assert entry is not None
        assert "sensitivity" in entry.content.lower()
        assert "margin" in entry.content.lower()

    def test_interference_entry(self, kb):
        entry = kb.get_entry("RF Interference and Noise")
        assert entry is not None
        assert "-174" in entry.content  # Thermal noise floor
        assert "noise floor" in entry.content.lower()

    def test_terrain_effects(self, kb):
        entry = kb.get_entry("Terrain Effects on RF Propagation")
        assert entry is not None
        assert "diffraction" in entry.content.lower()
        assert "line of sight" in entry.content.lower()

    def test_solar_power(self, kb):
        entry = kb.get_entry("Solar Power for Remote Nodes")
        assert entry is not None
        assert "18650" in entry.content
        assert "panel" in entry.content.lower()
        assert "charge" in entry.content.lower()

    def test_query_link_budget(self, kb):
        """Should find link budget info."""
        results = kb.query("link budget calculation sensitivity")
        assert len(results) > 0
        titles = [r[0].title for r in results]
        assert any("Link Budget" in t or "FSPL" in t for t in titles)

    def test_query_interference(self, kb):
        """Should find interference info."""
        results = kb.query("RF interference noise floor")
        assert len(results) > 0

    def test_query_solar(self, kb):
        """Should find solar power info."""
        results = kb.query("solar power battery remote node")
        assert len(results) > 0


class TestMQTTKnowledge:
    """Test MQTT entries."""

    def test_mqtt_meshtastic_entry(self, kb):
        entry = kb.get_entry("MQTT for Meshtastic")
        assert entry is not None
        assert "msh/" in entry.content  # Topic format
        assert "broker" in entry.content.lower()

    def test_mqtt_broker_setup(self, kb):
        entry = kb.get_entry("MQTT Broker Setup")
        assert entry is not None
        assert "mosquitto" in entry.content.lower()
        assert "tls" in entry.content.lower() or "TLS" in entry.content

    def test_query_mqtt(self, kb):
        """Should find MQTT info."""
        results = kb.query("MQTT broker subscribe topic")
        assert len(results) > 0
        titles = [r[0].title for r in results]
        assert any("MQTT" in t for t in titles)


class TestCrossReferences:
    """Test that related_entries point to valid entries."""

    def test_all_related_entries_exist(self, kb):
        """Every related_entries reference should point to a real entry."""
        missing = []
        for title, entry in kb._entries.items():
            for related in entry.related_entries:
                if related not in kb._entries:
                    missing.append(f"{title} -> {related}")

        assert len(missing) == 0, f"Missing related entries: {missing}"


class TestEntryQuality:
    """Test quality of entries."""

    def test_all_entries_have_content(self, kb):
        """Every entry should have substantial content."""
        for title, entry in kb._entries.items():
            assert len(entry.content) > 50, \
                f"Entry '{title}' has too-short content ({len(entry.content)} chars)"

    def test_all_entries_have_keywords(self, kb):
        """Every entry should have at least 2 keywords."""
        for title, entry in kb._entries.items():
            assert len(entry.keywords) >= 2, \
                f"Entry '{title}' has fewer than 2 keywords"

    def test_all_guides_have_steps(self, kb):
        """Every guide should have at least 3 steps."""
        for problem, guide in kb._guides.items():
            assert len(guide.steps) >= 3, \
                f"Guide '{problem}' has fewer than 3 steps"

    def test_expertise_levels_valid(self, kb):
        """All expertise levels should be valid."""
        valid = {'novice', 'intermediate', 'expert'}
        for title, entry in kb._entries.items():
            assert entry.expertise_level in valid, \
                f"Entry '{title}' has invalid expertise level: {entry.expertise_level}"

    def test_keyword_index_populated(self, kb):
        """Keyword index should have substantial entries."""
        assert len(kb._keyword_index) > 30, \
            f"Keyword index only has {len(kb._keyword_index)} entries"


class TestSearchRelevance:
    """Test that queries return relevant results."""

    def test_antenna_query(self, kb):
        """Antenna query should return antenna-related entries."""
        results = kb.query("antenna gain directional yagi")
        assert len(results) > 0
        # First result should be about antennas
        assert "antenna" in results[0][0].title.lower() or \
               "antenna" in results[0][0].content.lower()

    def test_battery_query(self, kb):
        """Battery query should find solar/power info."""
        results = kb.query("battery solar power charging")
        assert len(results) > 0

    def test_encryption_query(self, kb):
        """Encryption query should find crypto entries."""
        results = kb.query("encryption key AES")
        assert len(results) > 0

    def test_propagation_query(self, kb):
        """Propagation query should find terrain/FSPL entries."""
        results = kb.query("propagation terrain path loss model")
        assert len(results) > 0

    def test_duty_cycle_query(self, kb):
        """Duty cycle query should find regulation/channel entries."""
        results = kb.query("duty cycle regulation limit")
        assert len(results) > 0
