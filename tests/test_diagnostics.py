"""
Tests for MeshForge Diagnostic Intelligence System.

Tests cover:
- DiagnosticEngine rule matching and symptom correlation
- KnowledgeBase query and retrieval
- ClaudeAssistant standalone mode

Run with: pytest tests/test_diagnostics.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestDiagnosticEngine:
    """Tests for the DiagnosticEngine class."""

    @pytest.fixture
    def engine(self):
        """Create a fresh diagnostic engine for each test."""
        from utils.diagnostic_engine import DiagnosticEngine
        return DiagnosticEngine()

    def test_engine_initialization(self, engine):
        """Test that engine initializes with built-in rules."""
        assert len(engine._rules) > 0
        assert engine._stats["symptoms_processed"] == 0

    def test_diagnose_connection_refused(self, engine):
        """Test diagnosis of connection refused error."""
        from utils.diagnostic_engine import Category, Severity

        diagnosis = engine.report_symptom(
            message="Connection refused to meshtasticd on port 4403",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
            context={"port": 4403}
        )

        assert diagnosis is not None
        assert "client" in diagnosis.likely_cause.lower() or "another" in diagnosis.likely_cause.lower()
        assert len(diagnosis.suggestions) > 0
        assert diagnosis.confidence > 0.5

    def test_diagnose_serial_port_busy(self, engine):
        """Test diagnosis of serial port busy error."""
        from utils.diagnostic_engine import Category, Severity

        diagnosis = engine.report_symptom(
            message="Serial port /dev/ttyUSB0 is busy",
            category=Category.HARDWARE,
            severity=Severity.ERROR
        )

        assert diagnosis is not None
        assert "serial" in diagnosis.likely_cause.lower() or "port" in diagnosis.likely_cause.lower()
        assert any("lsof" in s or "permission" in s.lower() for s in diagnosis.suggestions)

    def test_diagnose_weak_signal(self, engine):
        """Test diagnosis of weak signal warning."""
        from utils.diagnostic_engine import Category, Severity

        diagnosis = engine.report_symptom(
            message="SNR is very low at -15 dB",
            category=Category.PERFORMANCE,
            severity=Severity.WARNING
        )

        assert diagnosis is not None
        assert "signal" in diagnosis.likely_cause.lower() or "snr" in diagnosis.likely_cause.lower()

    def test_diagnose_unknown_symptom(self, engine):
        """Test that unknown symptoms return None."""
        from utils.diagnostic_engine import Category, Severity

        diagnosis = engine.report_symptom(
            message="Something completely random happened xyz123",
            category=Category.CONNECTIVITY,
            severity=Severity.INFO
        )

        # Unknown symptoms may or may not match a rule
        # The important thing is it doesn't crash
        assert engine._stats["symptoms_processed"] == 1

    def test_symptom_correlation(self, engine):
        """Test that related symptoms are correlated."""
        from utils.diagnostic_engine import Category, Severity

        # Report multiple related symptoms
        engine.report_symptom(
            message="Connection timeout to meshtasticd",
            category=Category.CONNECTIVITY,
            severity=Severity.WARNING
        )

        # Small delay to ensure different timestamps
        import time
        time.sleep(0.01)

        diagnosis = engine.report_symptom(
            message="Connection refused to meshtasticd",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR
        )

        # The second diagnosis should find the first as related
        if diagnosis:
            # Note: correlation requires symptoms within time window
            assert engine._stats["symptoms_processed"] == 2

    def test_health_summary(self, engine):
        """Test health summary generation."""
        from utils.diagnostic_engine import Category, Severity

        # Report some symptoms
        engine.report_symptom("Warning test", Category.CONNECTIVITY, Severity.WARNING)
        engine.report_symptom("Error test", Category.CONNECTIVITY, Severity.ERROR)

        summary = engine.get_health_summary()

        assert "overall_health" in summary
        assert "symptoms_last_hour" in summary
        assert summary["symptoms_last_hour"] == 2
        assert "by_severity" in summary

    def test_to_log_format(self, engine):
        """Test diagnosis log formatting."""
        from utils.diagnostic_engine import Category, Severity

        diagnosis = engine.report_symptom(
            message="Connection refused to meshtasticd",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR
        )

        if diagnosis:
            log_output = diagnosis.to_log_format()
            assert "[DIAGNOSIS]" in log_output
            assert "Likely cause:" in log_output

    def test_stats_tracking(self, engine):
        """Test that statistics are tracked correctly."""
        from utils.diagnostic_engine import Category, Severity

        initial_stats = engine.get_stats()
        assert initial_stats["symptoms_processed"] == 0

        engine.report_symptom("Test 1", Category.CONNECTIVITY, Severity.INFO)
        engine.report_symptom("Test 2", Category.HARDWARE, Severity.WARNING)

        final_stats = engine.get_stats()
        assert final_stats["symptoms_processed"] == 2


class TestKnowledgeBase:
    """Tests for the KnowledgeBase class."""

    @pytest.fixture
    def kb(self):
        """Create a fresh knowledge base for each test."""
        from utils.knowledge_base import KnowledgeBase
        return KnowledgeBase()

    def test_kb_initialization(self, kb):
        """Test that knowledge base initializes with entries."""
        assert len(kb._entries) > 0
        assert len(kb._keyword_index) > 0

    def test_query_snr(self, kb):
        """Test querying for SNR information."""
        results = kb.query("What is SNR?")

        assert len(results) > 0
        entry, score = results[0]
        assert "snr" in entry.title.lower() or "signal" in entry.title.lower()

    def test_query_channel_utilization(self, kb):
        """Test querying for channel utilization."""
        results = kb.query("channel utilization high")

        assert len(results) > 0
        # Should find channel-related entry
        found_channel = any("channel" in e.title.lower() for e, _ in results)
        assert found_channel

    def test_query_meshtasticd(self, kb):
        """Test querying for meshtasticd information."""
        results = kb.query("meshtasticd daemon")

        assert len(results) > 0

    def test_get_entry_by_title(self, kb):
        """Test getting entry by exact title."""
        # Get any entry title
        if kb._entries:
            title = list(kb._entries.keys())[0]
            entry = kb.get_entry(title)
            assert entry is not None
            assert entry.title == title

    def test_get_troubleshooting_guide(self, kb):
        """Test getting troubleshooting guide."""
        guide = kb.get_troubleshooting_guide("no_connection_meshtasticd")

        if guide:
            assert guide.problem == "no_connection_meshtasticd"
            assert len(guide.steps) > 0

    def test_list_topics(self, kb):
        """Test listing all topics."""
        topics = kb.list_topics()

        assert len(topics) > 0
        assert all(isinstance(t, str) for t in topics)

    def test_get_entries_by_topic(self, kb):
        """Test getting entries by topic."""
        from utils.knowledge_base import KnowledgeTopic

        entries = kb.get_entries_by_topic(KnowledgeTopic.RF_FUNDAMENTALS)

        assert len(entries) > 0
        assert all(e.topic == KnowledgeTopic.RF_FUNDAMENTALS for e in entries)

    def test_empty_query(self, kb):
        """Test that empty query returns empty results."""
        results = kb.query("")

        # Should handle gracefully
        assert isinstance(results, list)

    def test_query_max_results(self, kb):
        """Test that max_results is respected."""
        results = kb.query("signal noise", max_results=1)

        assert len(results) <= 1


class TestClaudeAssistant:
    """Tests for the ClaudeAssistant in standalone mode."""

    @pytest.fixture
    def assistant(self):
        """Create assistant in standalone mode (no API key)."""
        from utils.claude_assistant import ClaudeAssistant
        return ClaudeAssistant(api_key=None)

    def test_assistant_initialization(self, assistant):
        """Test that assistant initializes in standalone mode."""
        from utils.claude_assistant import AssistantMode

        assert assistant._mode == AssistantMode.STANDALONE
        assert assistant._diagnostic_engine is not None
        assert assistant._knowledge_base is not None

    def test_ask_standalone(self, assistant):
        """Test asking a question in standalone mode."""
        response = assistant.ask("What is SNR?")

        assert response is not None
        assert len(response.answer) > 0
        assert response.confidence > 0

    def test_ask_unknown_topic(self, assistant):
        """Test asking about unknown topic."""
        response = assistant.ask("xyz random unknown topic 12345")

        assert response is not None
        # Should return a helpful message about what topics are available
        assert len(response.answer) > 0

    def test_analyze_logs(self, assistant):
        """Test log analysis."""
        logs = [
            "ERROR: Connection refused to meshtasticd",
            "WARNING: SNR is low at -12 dB",
        ]

        response = assistant.analyze_logs(logs)

        assert response is not None
        # Should identify issues or say none found
        assert len(response.answer) > 0

    def test_get_help(self, assistant):
        """Test getting help on a topic."""
        response = assistant.get_help("weak_signal")

        assert response is not None
        assert len(response.answer) > 0

    def test_expertise_levels(self, assistant):
        """Test expertise level adaptation."""
        from utils.claude_assistant import ExpertiseLevel

        # Set to novice
        assistant.set_expertise_level(ExpertiseLevel.NOVICE)
        novice_response = assistant.ask("What is SNR?", include_history=False)

        # Set to expert
        assistant.set_expertise_level(ExpertiseLevel.EXPERT)
        expert_response = assistant.ask("What is SNR?", include_history=False)

        # Both should return valid responses
        assert novice_response is not None
        assert expert_response is not None

    def test_conversation_history(self, assistant):
        """Test that conversation history is maintained."""
        assistant.ask("What is SNR?")
        assistant.ask("Tell me more")

        assert len(assistant._conversation) >= 2

    def test_clear_conversation(self, assistant):
        """Test clearing conversation history."""
        assistant.ask("Test question")
        assert len(assistant._conversation) > 0

        assistant.clear_conversation()
        assert len(assistant._conversation) == 0

    def test_health_explanation(self, assistant):
        """Test health summary explanation."""
        health_summary = {
            "overall_health": "warning",
            "symptoms_last_hour": 5,
            "by_category": {"connectivity": 3, "performance": 2},
        }

        explanation = assistant.get_health_explanation(health_summary)

        assert "warning" in explanation.lower() or "warnings" in explanation.lower()
        assert "5" in explanation  # Should mention symptom count

    def test_network_context(self, assistant):
        """Test setting network context."""
        context = {
            "node_count": 10,
            "health_summary": {"overall_health": "healthy"},
        }

        assistant.set_network_context(context)

        assert assistant._network_context == context

    def test_mode_detection(self, assistant):
        """Test mode detection methods."""
        assert not assistant.is_pro_enabled()
        assert assistant.get_mode().value == "standalone"


class TestIntegration:
    """Integration tests for the diagnostic system."""

    def test_full_diagnostic_flow(self):
        """Test complete diagnostic flow from symptom to suggestion."""
        from utils.diagnostic_engine import diagnose, Category, Severity

        # Simulate a connection error
        diagnosis = diagnose(
            "Connection refused to meshtasticd on port 4403",
            category=Category.CONNECTIVITY,
            severity=Severity.ERROR,
            context={"port": 4403, "service_running": True},
            source="test"
        )

        if diagnosis:
            # Verify complete diagnosis
            assert diagnosis.symptom is not None
            assert len(diagnosis.likely_cause) > 0
            assert diagnosis.confidence > 0
            assert len(diagnosis.suggestions) > 0
            assert len(diagnosis.explanation) > 0

    def test_assistant_with_diagnostics(self):
        """Test assistant analyzing diagnostic results."""
        from utils.claude_assistant import ClaudeAssistant

        assistant = ClaudeAssistant(api_key=None)

        # Analyze some logs
        logs = [
            "ERROR: Connection refused to meshtasticd",
            "WARNING: Channel utilization high at 65%",
            "ERROR: Serial port /dev/ttyUSB0 busy",
        ]

        response = assistant.analyze_logs(logs)

        assert response is not None
        # Should find multiple issues
        assert "issue" in response.answer.lower() or "detect" in response.answer.lower()

    def test_knowledge_base_query_relevance(self):
        """Test that knowledge base returns relevant results."""
        from utils.knowledge_base import get_knowledge_base

        kb = get_knowledge_base()

        # Query for specific technical term
        results = kb.query("LoRa spreading factor range")

        assert len(results) > 0
        # Top result should be about spreading factor
        top_entry, _ = results[0]
        content_lower = top_entry.content.lower()
        assert "spreading" in content_lower or "sf" in content_lower or "lora" in content_lower


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_log_analysis(self):
        """Test analyzing empty logs."""
        from utils.claude_assistant import ClaudeAssistant

        assistant = ClaudeAssistant(api_key=None)
        response = assistant.analyze_logs([])

        assert response is not None
        assert "no" in response.answer.lower() or len(response.answer) > 0

    def test_very_long_symptom(self):
        """Test handling very long symptom messages."""
        from utils.diagnostic_engine import diagnose, Category, Severity

        long_message = "Error: " + "x" * 10000

        # Should not crash
        diagnosis = diagnose(long_message, Category.CONNECTIVITY, Severity.ERROR)
        # May or may not match a rule, but shouldn't crash

    def test_special_characters_in_query(self):
        """Test handling special characters in queries."""
        from utils.knowledge_base import get_knowledge_base

        kb = get_knowledge_base()

        # Query with special characters
        results = kb.query("What's the SNR? <test> & more")

        # Should handle gracefully
        assert isinstance(results, list)

    def test_concurrent_symptom_reporting(self):
        """Test thread safety of symptom reporting."""
        from utils.diagnostic_engine import get_diagnostic_engine, Category, Severity
        import threading

        engine = get_diagnostic_engine()
        initial_count = engine._stats["symptoms_processed"]

        def report_symptom():
            engine.report_symptom(
                f"Test symptom from thread",
                Category.CONNECTIVITY,
                Severity.INFO
            )

        # Create multiple threads
        threads = [threading.Thread(target=report_symptom) for _ in range(10)]

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # All symptoms should be processed
        final_count = engine._stats["symptoms_processed"]
        assert final_count >= initial_count + 10
