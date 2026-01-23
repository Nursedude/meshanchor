"""
Tests for expanded diagnostic rules (Phase 4 expansion from 17 to 58 rules).

Tests verify:
- Each new rule matches its intended symptom patterns
- Correct category assignment
- Suggestions are actionable
- Confidence levels are reasonable
- No false positives across categories

Run with: pytest tests/test_diagnostic_rules_expanded.py -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.diagnostic_engine import (
    DiagnosticEngine, Category, Severity, DiagnosticRule
)


@pytest.fixture
def engine():
    """Create a fresh diagnostic engine (no DB persistence)."""
    return DiagnosticEngine(persist_history=False)


class TestRuleCount:
    """Verify overall rule count meets target."""

    def test_minimum_rule_count(self, engine):
        """Engine should have at least 50 rules."""
        assert len(engine._rules) >= 50

    def test_all_categories_covered(self, engine):
        """Every category should have at least 2 rules."""
        categories = set(r.category for r in engine._rules)
        for cat in Category:
            matching = [r for r in engine._rules if r.category == cat]
            # PREDICTIVE category may not have built-in rules
            if cat != Category.PREDICTIVE:
                assert len(matching) >= 2, f"Category {cat.value} has fewer than 2 rules"

    def test_unique_rule_names(self, engine):
        """All rules should have unique names."""
        names = [r.name for r in engine._rules]
        assert len(names) == len(set(names)), "Duplicate rule names found"


class TestConnectivityRulesExpanded:
    """Test new connectivity rules."""

    def test_dns_resolution_failed(self, engine):
        diagnosis = engine.report_symptom(
            "DNS lookup failed for mqtt.meshtastic.org",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "dns" in diagnosis.likely_cause.lower() or "resolve" in diagnosis.likely_cause.lower()

    def test_dns_timeout(self, engine):
        diagnosis = engine.report_symptom(
            "hostname resolution timeout for api.example.com",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None

    def test_network_interface_down(self, engine):
        diagnosis = engine.report_symptom(
            "wlan0 interface is down, no carrier",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "interface" in diagnosis.likely_cause.lower() or "link" in diagnosis.likely_cause.lower()

    def test_eth0_link_lost(self, engine):
        diagnosis = engine.report_symptom(
            "eth0 link lost, network disconnected",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None

    def test_mqtt_subscription_lost(self, engine):
        diagnosis = engine.report_symptom(
            "MQTT subscription lost for topic msh/US/2/json/#",
            category=Category.CONNECTIVITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert diagnosis.auto_recoverable is True

    def test_rns_path_not_found(self, engine):
        diagnosis = engine.report_symptom(
            "RNS path not found for destination a1b2c3d4e5",
            category=Category.CONNECTIVITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "path" in diagnosis.likely_cause.lower() or "unreachable" in diagnosis.likely_cause.lower()

    def test_tcp_handshake_timeout(self, engine):
        diagnosis = engine.report_symptom(
            "TCP connection handshake timeout to 192.168.1.100:4403",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None

    def test_bridge_reconnecting(self, engine):
        diagnosis = engine.report_symptom(
            "Bridge gateway reconnect attempt 5 with backoff",
            category=Category.CONNECTIVITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert diagnosis.auto_recoverable is True

    def test_websocket_disconnected(self, engine):
        diagnosis = engine.report_symptom(
            "WebSocket connection closed unexpectedly",
            category=Category.CONNECTIVITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_api_endpoint_unreachable(self, engine):
        diagnosis = engine.report_symptom(
            "API endpoint unreachable, HTTP 503 Service Unavailable",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None


class TestHardwareRulesExpanded:
    """Test new hardware rules."""

    def test_usb_power_insufficient(self, engine):
        diagnosis = engine.report_symptom(
            "USB power insufficient, device brownout detected",
            category=Category.HARDWARE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "power" in diagnosis.likely_cause.lower() or "usb" in diagnosis.likely_cause.lower()

    def test_usb_over_current(self, engine):
        diagnosis = engine.report_symptom(
            "USB over-current condition on port 2",
            category=Category.HARDWARE,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None

    def test_gps_lock_lost(self, engine):
        diagnosis = engine.report_symptom(
            "GPS no fix, position unavailable",
            category=Category.HARDWARE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "gps" in diagnosis.likely_cause.lower() or "satellite" in diagnosis.likely_cause.lower()

    def test_gps_timeout(self, engine):
        diagnosis = engine.report_symptom(
            "GPS location timeout, no lock acquired",
            category=Category.HARDWARE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_radio_reset_detected(self, engine):
        diagnosis = engine.report_symptom(
            "LoRa radio chip reset unexpectedly, reinitializing",
            category=Category.HARDWARE,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "reset" in diagnosis.likely_cause.lower() or "radio" in diagnosis.likely_cause.lower()

    def test_battery_low(self, engine):
        diagnosis = engine.report_symptom(
            "Battery level critical at 15%, node may shutdown",
            category=Category.HARDWARE,
            severity=Severity.CRITICAL,
        )
        assert diagnosis is not None
        assert "battery" in diagnosis.likely_cause.lower()

    def test_overheating(self, engine):
        diagnosis = engine.report_symptom(
            "Temperature high warning: CPU thermal throttling active",
            category=Category.HARDWARE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "overheat" in diagnosis.likely_cause.lower() or "thermal" in diagnosis.likely_cause.lower() or "heat" in diagnosis.likely_cause.lower()

    def test_spi_bus_error(self, engine):
        diagnosis = engine.report_symptom(
            "SPI bus error communicating with radio chip",
            category=Category.HARDWARE,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "bus" in diagnosis.likely_cause.lower() or "communication" in diagnosis.likely_cause.lower()

    def test_firmware_flash_failed(self, engine):
        diagnosis = engine.report_symptom(
            "Firmware flash failed, verify error on block 0x4000",
            category=Category.HARDWARE,
            severity=Severity.CRITICAL,
        )
        assert diagnosis is not None
        assert "firmware" in diagnosis.likely_cause.lower() or "flash" in diagnosis.likely_cause.lower()


class TestProtocolRulesExpanded:
    """Test new protocol rules."""

    def test_channel_config_mismatch(self, engine):
        diagnosis = engine.report_symptom(
            "Channel frequency mismatch detected between nodes",
            category=Category.PROTOCOL,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "channel" in diagnosis.likely_cause.lower() or "frequenc" in diagnosis.likely_cause.lower()

    def test_crc_error_high(self, engine):
        diagnosis = engine.report_symptom(
            "CRC error rate is high (15% of packets failing checksum)",
            category=Category.PROTOCOL,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "crc" in diagnosis.likely_cause.lower() or "interference" in diagnosis.likely_cause.lower()

    def test_mesh_routing_loop(self, engine):
        diagnosis = engine.report_symptom(
            "Routing loop detected, TTL expired on message",
            category=Category.PROTOCOL,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "loop" in diagnosis.likely_cause.lower() or "routing" in diagnosis.likely_cause.lower()

    def test_beacon_timeout(self, engine):
        diagnosis = engine.report_symptom(
            "Node !abc123 beacon timeout, 3 heartbeats missed",
            category=Category.PROTOCOL,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_identity_collision(self, engine):
        diagnosis = engine.report_symptom(
            "Identity hash collision detected between two nodes",
            category=Category.PROTOCOL,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "collision" in diagnosis.likely_cause.lower() or "identity" in diagnosis.likely_cause.lower()

    def test_lxmf_delivery_failed(self, engine):
        diagnosis = engine.report_symptom(
            "LXMF message delivery failed, timeout after 120s",
            category=Category.PROTOCOL,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert diagnosis.auto_recoverable is True

    def test_packet_decode_error(self, engine):
        diagnosis = engine.report_symptom(
            "Packet decode error: failed to deserialize mesh protobuf",
            category=Category.PROTOCOL,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "decode" in diagnosis.likely_cause.lower() or "version" in diagnosis.likely_cause.lower()


class TestPerformanceRulesExpanded:
    """Test new performance rules."""

    def test_high_packet_loss(self, engine):
        diagnosis = engine.report_symptom(
            "Packet loss rate high at 35% on link to !def456",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "packet loss" in diagnosis.likely_cause.lower() or "loss" in diagnosis.likely_cause.lower()

    def test_latency_spike(self, engine):
        diagnosis = engine.report_symptom(
            "Latency spike detected: response time > 30s",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_tx_duty_cycle_exceeded(self, engine):
        diagnosis = engine.report_symptom(
            "TX duty cycle limit exceeded for EU_868 regulation",
            category=Category.PERFORMANCE,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "duty cycle" in diagnosis.likely_cause.lower()

    def test_hop_count_excessive(self, engine):
        diagnosis = engine.report_symptom(
            "Message relay hop count exceeds limit (6 > 5)",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_retransmission_high(self, engine):
        diagnosis = engine.report_symptom(
            "Retransmit rate high: 8 retries for last message",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_airtime_limit(self, engine):
        diagnosis = engine.report_symptom(
            "Channel airtime limit approaching at 85%",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "airtime" in diagnosis.likely_cause.lower()


class TestResourceRulesExpanded:
    """Test new resource rules."""

    def test_cpu_overload(self, engine):
        diagnosis = engine.report_symptom(
            "CPU load high at 100%, system unresponsive",
            category=Category.RESOURCE,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "cpu" in diagnosis.likely_cause.lower() or "overload" in diagnosis.likely_cause.lower()

    def test_file_descriptor_limit(self, engine):
        diagnosis = engine.report_symptom(
            "Too many open files (EMFILE), cannot create socket",
            category=Category.RESOURCE,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "file descriptor" in diagnosis.likely_cause.lower()

    def test_database_corruption(self, engine):
        diagnosis = engine.report_symptom(
            "SQLite database corruption detected: malformed disk image",
            category=Category.RESOURCE,
            severity=Severity.CRITICAL,
        )
        assert diagnosis is not None
        assert "corrupt" in diagnosis.likely_cause.lower() or "database" in diagnosis.likely_cause.lower()

    def test_log_file_too_large(self, engine):
        diagnosis = engine.report_symptom(
            "Log file growing too large at > 500MB",
            category=Category.RESOURCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "log" in diagnosis.likely_cause.lower()


class TestConfigurationRulesExpanded:
    """Test new configuration rules."""

    def test_permission_denied(self, engine):
        diagnosis = engine.report_symptom(
            "Permission denied accessing /dev/ttyUSB0",
            category=Category.CONFIGURATION,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "permission" in diagnosis.likely_cause.lower()

    def test_port_conflict(self, engine):
        diagnosis = engine.report_symptom(
            "Port 5000 address already in use, bind failed",
            category=Category.CONFIGURATION,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "port" in diagnosis.likely_cause.lower() or "in use" in diagnosis.likely_cause.lower()

    def test_invalid_frequency(self, engine):
        diagnosis = engine.report_symptom(
            "Frequency setting invalid for region US, not allowed",
            category=Category.CONFIGURATION,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "frequency" in diagnosis.likely_cause.lower() or "region" in diagnosis.likely_cause.lower()

    def test_duplicate_node_id(self, engine):
        diagnosis = engine.report_symptom(
            "Node device duplicate ID conflict detected in mesh",
            category=Category.CONFIGURATION,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None

    def test_wrong_modem_preset(self, engine):
        diagnosis = engine.report_symptom(
            "LoRa modem preset mismatch — nodes using different settings",
            category=Category.CONFIGURATION,
            severity=Severity.ERROR,
        )
        assert diagnosis is not None
        assert "preset" in diagnosis.likely_cause.lower() or "modem" in diagnosis.likely_cause.lower()


class TestSecurityRules:
    """Test security rules (new category)."""

    def test_unauthorized_access(self, engine):
        diagnosis = engine.report_symptom(
            "Unauthorized login attempt from 192.168.1.50",
            category=Category.SECURITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "unauthorized" in diagnosis.likely_cause.lower() or "access" in diagnosis.likely_cause.lower()

    def test_auth_failed(self, engine):
        diagnosis = engine.report_symptom(
            "Authentication failed for API key xyz123",
            category=Category.SECURITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None

    def test_rogue_node(self, engine):
        diagnosis = engine.report_symptom(
            "Unknown foreign node detected on mesh with ID !rogue99",
            category=Category.SECURITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "node" in diagnosis.likely_cause.lower() or "unauthorized" in diagnosis.likely_cause.lower()

    def test_key_rotation_needed(self, engine):
        diagnosis = engine.report_symptom(
            "Channel encryption key is stale, rotation recommended",
            category=Category.SECURITY,
            severity=Severity.INFO,
        )
        assert diagnosis is not None
        assert "key" in diagnosis.likely_cause.lower() or "rotation" in diagnosis.likely_cause.lower()

    def test_insecure_channel(self, engine):
        diagnosis = engine.report_symptom(
            "Channel 0 is unencrypted, messages sent in plaintext",
            category=Category.SECURITY,
            severity=Severity.WARNING,
        )
        assert diagnosis is not None
        assert "encrypt" in diagnosis.likely_cause.lower() or "insecure" in diagnosis.likely_cause.lower() or "not encrypted" in diagnosis.likely_cause.lower()


class TestNoFalsePositives:
    """Ensure rules don't match unrelated symptoms."""

    def test_no_match_for_gibberish(self, engine):
        diagnosis = engine.report_symptom(
            "Everything is working fine, no issues here",
            category=Category.CONNECTIVITY,
            severity=Severity.INFO,
        )
        assert diagnosis is None

    def test_no_match_for_unrelated_error(self, engine):
        diagnosis = engine.report_symptom(
            "The weather is nice today",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING,
        )
        assert diagnosis is None

    def test_hardware_symptom_wrong_category_still_matches(self, engine):
        """Connectivity rules check all categories, so some cross-matching is expected."""
        # Serial port busy is HARDWARE category
        diagnosis = engine.report_symptom(
            "Serial port /dev/ttyUSB0 is busy",
            category=Category.HARDWARE,
            severity=Severity.ERROR,
        )
        # Should still match because the rule category matches
        assert diagnosis is not None


class TestConfidenceAndSuggestions:
    """Verify confidence levels and suggestion quality."""

    def test_all_rules_have_suggestions(self, engine):
        """Every rule should have at least one suggestion."""
        for rule in engine._rules:
            assert len(rule.suggestions) > 0, f"Rule '{rule.name}' has no suggestions"

    def test_all_rules_have_valid_confidence(self, engine):
        """Confidence should be between 0.5 and 1.0."""
        for rule in engine._rules:
            assert 0.5 <= rule.confidence_base <= 1.0, \
                f"Rule '{rule.name}' has invalid confidence: {rule.confidence_base}"

    def test_all_rules_have_cause_template(self, engine):
        """Every rule should have a non-empty cause template."""
        for rule in engine._rules:
            assert len(rule.cause_template) > 10, \
                f"Rule '{rule.name}' has too-short cause template"

    def test_auto_recoverable_has_action(self, engine):
        """Rules marked auto_recoverable must have recovery_action."""
        for rule in engine._rules:
            if rule.auto_recoverable:
                assert rule.recovery_action is not None, \
                    f"Rule '{rule.name}' is auto_recoverable but has no recovery_action"

    def test_critical_rules_have_high_confidence(self, engine):
        """Rules for critical hardware/security issues should have high base confidence."""
        critical_rules = [r for r in engine._rules
                         if r.name in ('firmware_flash_failed', 'battery_low',
                                       'database_corruption', 'insecure_channel_detected')]
        for rule in critical_rules:
            assert rule.confidence_base >= 0.85, \
                f"Critical rule '{rule.name}' has low confidence: {rule.confidence_base}"


class TestDiagnosticStats:
    """Test that expanded rules maintain proper statistics."""

    def test_stats_increment(self, engine):
        """Stats should increment with each symptom processed."""
        engine.report_symptom(
            "Connection refused to meshtasticd",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
        )
        assert engine._stats["symptoms_processed"] == 1
        assert engine._stats["diagnoses_made"] == 1

    def test_multiple_symptoms_tracked(self, engine):
        """Multiple symptoms should all be tracked."""
        symptoms = [
            ("DNS lookup failed for host", Category.CONNECTIVITY),
            ("Battery level critical", Category.HARDWARE),
            ("CRC error rate high", Category.PROTOCOL),
        ]
        for msg, cat in symptoms:
            engine.report_symptom(msg, category=cat, severity=Severity.WARNING)

        assert engine._stats["symptoms_processed"] == 3
        assert engine._stats["diagnoses_made"] == 3

    def test_health_summary_reflects_symptoms(self, engine):
        """Health summary should reflect reported symptoms."""
        engine.report_symptom(
            "Database corruption detected: malformed disk image",
            category=Category.RESOURCE,
            severity=Severity.CRITICAL,
        )
        summary = engine.get_health_summary()
        assert summary["overall_health"] == "critical"
        assert summary["symptoms_last_hour"] == 1
