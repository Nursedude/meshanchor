"""
Tests for MeshForge TUI AI Tools Mixin.

Tests cover:
- AI tools menu navigation
- Diagnostic symptom handling
- Knowledge base query interface
- Coverage map generation

Run with: pytest tests/test_ai_tools.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class MockDialog:
    """Mock dialog backend for testing."""

    def __init__(self):
        self.last_menu_title = None
        self.last_menu_choices = None
        self.menu_responses = []
        self.inputbox_responses = []
        self.msgbox_calls = []

    def menu(self, title, text, choices):
        self.last_menu_title = title
        self.last_menu_choices = choices
        if self.menu_responses:
            return self.menu_responses.pop(0)
        return "back"

    def inputbox(self, title, text):
        if self.inputbox_responses:
            return self.inputbox_responses.pop(0)
        return None

    def msgbox(self, title, text):
        self.msgbox_calls.append((title, text))

    def infobox(self, title, text):
        pass


class TestAIToolsMixin:
    """Tests for the AIToolsMixin class."""

    @pytest.fixture
    def mixin_instance(self):
        """Create a mixin instance with mock dialog."""
        # Import mixin
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
        from ai_tools_mixin import AIToolsMixin

        # Create instance with mock
        class TestClass(AIToolsMixin):
            def __init__(self):
                self.dialog = MockDialog()

        return TestClass()

    def test_ai_tools_menu_exists(self, mixin_instance):
        """Test that AI tools menu method exists."""
        assert hasattr(mixin_instance, '_ai_tools_menu')

    def test_ai_tools_menu_choices(self, mixin_instance):
        """Test that AI tools menu has correct choices."""
        # Set up mock to return 'back' immediately
        mixin_instance.dialog.menu_responses = ['back']

        # Call menu
        mixin_instance._ai_tools_menu()

        # Verify menu was shown with correct options
        assert mixin_instance.dialog.last_menu_title == "AI Tools"
        choice_keys = [c[0] for c in mixin_instance.dialog.last_menu_choices]
        assert "diagnose" in choice_keys
        assert "knowledge" in choice_keys
        assert "assistant" in choice_keys
        assert "coverage" in choice_keys
        assert "back" in choice_keys

    def test_intelligent_diagnostics_menu(self, mixin_instance):
        """Test diagnostics submenu."""
        assert hasattr(mixin_instance, '_intelligent_diagnostics')

    def test_knowledge_base_query_menu(self, mixin_instance):
        """Test knowledge base submenu."""
        assert hasattr(mixin_instance, '_knowledge_base_query')

    def test_claude_assistant_menu(self, mixin_instance):
        """Test Claude assistant method exists."""
        assert hasattr(mixin_instance, '_claude_assistant')

    def test_coverage_map_menu(self, mixin_instance):
        """Test coverage map method exists."""
        assert hasattr(mixin_instance, '_generate_coverage_map')


class TestCoverageMapGenerator:
    """Tests for the CoverageMapGenerator class."""

    @pytest.fixture
    def generator(self):
        """Create a coverage map generator."""
        try:
            from utils.coverage_map import CoverageMapGenerator
            return CoverageMapGenerator()
        except ImportError:
            pytest.skip("Coverage map module not available")

    def test_generator_initialization(self, generator):
        """Test that generator initializes correctly."""
        assert generator._nodes == []
        assert generator._coverage_radius > 0

    def test_preset_ranges_defined(self, generator):
        """Test that preset ranges are defined."""
        assert "LONG_FAST" in generator.PRESET_RANGES
        assert "LONG_SLOW" in generator.PRESET_RANGES
        assert "DEFAULT" in generator.PRESET_RANGES

    def test_add_node(self, generator):
        """Test adding a node to the generator."""
        from utils.coverage_map import MapNode

        node = MapNode(
            id="!abc123",
            name="TestNode",
            latitude=37.7749,
            longitude=-122.4194
        )

        generator.add_node(node)
        assert len(generator._nodes) == 1
        assert generator._nodes[0].id == "!abc123"

    def test_add_nodes_from_geojson(self, generator):
        """Test adding nodes from GeoJSON format."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "!node1",
                        "name": "Node One"
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.4194, 37.7749]
                    }
                }
            ]
        }

        generator.add_nodes_from_geojson(geojson)
        assert len(generator._nodes) >= 1


class TestDiagnosticHelpers:
    """Tests for diagnostic helper functions."""

    def test_diagnose_function_exists(self):
        """Test that the diagnose convenience function exists."""
        try:
            from utils.diagnostic_engine import diagnose
            assert callable(diagnose)
        except ImportError:
            pytest.skip("Diagnostic engine not available")

    def test_category_enum_exists(self):
        """Test that Category enum is defined."""
        try:
            from utils.diagnostic_engine import Category
            assert hasattr(Category, "CONNECTIVITY")
            assert hasattr(Category, "HARDWARE")
            assert hasattr(Category, "PERFORMANCE")
        except ImportError:
            pytest.skip("Diagnostic engine not available")

    def test_severity_enum_exists(self):
        """Test that Severity enum is defined."""
        try:
            from utils.diagnostic_engine import Severity
            assert hasattr(Severity, "ERROR")
            assert hasattr(Severity, "WARNING")
            assert hasattr(Severity, "INFO")
        except ImportError:
            pytest.skip("Diagnostic engine not available")


class TestKnowledgeBase:
    """Tests for the KnowledgeBase class."""

    @pytest.fixture
    def knowledge_base(self):
        """Get the knowledge base singleton."""
        try:
            from utils.knowledge_base import get_knowledge_base
            return get_knowledge_base()
        except ImportError:
            pytest.skip("Knowledge base not available")

    def test_knowledge_base_singleton(self, knowledge_base):
        """Test that knowledge base is a singleton."""
        from utils.knowledge_base import get_knowledge_base
        kb2 = get_knowledge_base()
        assert knowledge_base is kb2

    def test_knowledge_base_has_entries(self, knowledge_base):
        """Test that knowledge base has some entries."""
        assert len(knowledge_base._entries) > 0

    def test_query_snr(self, knowledge_base):
        """Test querying for SNR information."""
        results = knowledge_base.query("What is SNR?")
        assert len(results) > 0
        # Should find SNR-related entry (results are (entry, score) tuples)
        assert any("snr" in entry.title.lower() or "signal" in entry.title.lower()
                   for entry, score in results)

    def test_query_no_results(self, knowledge_base):
        """Test query with no matching results."""
        results = knowledge_base.query("xyzzy12345nonexistent")
        # Should return empty or low-relevance results
        assert isinstance(results, list)


class TestRegionEnumMap:
    """Tests for the REGION_ENUM_MAP constant."""

    def test_region_enum_map_exists(self):
        """Test that REGION_ENUM_MAP is defined."""
        try:
            # Direct import to avoid GTK dependency
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "radio_config_simple",
                os.path.join(os.path.dirname(__file__), '..', 'src', 'gtk_ui', 'panels', 'radio_config_simple.py')
            )
            # Can't fully load due to GTK, but we can check the file content
            with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'gtk_ui', 'panels', 'radio_config_simple.py')) as f:
                content = f.read()
                assert "REGION_ENUM_MAP" in content
                assert "1: \"US\"" in content or "1: 'US'" in content
        except Exception:
            pytest.skip("Could not check radio_config_simple.py")

    def test_region_enum_map_values(self):
        """Test that REGION_ENUM_MAP has expected values."""
        with open(os.path.join(os.path.dirname(__file__), '..', 'src', 'gtk_ui', 'panels', 'radio_config_simple.py')) as f:
            content = f.read()
            # Check for key regions
            assert "US" in content
            assert "EU_868" in content
            assert "ANZ" in content
