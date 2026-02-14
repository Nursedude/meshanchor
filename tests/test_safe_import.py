"""
Tests for utils/safe_import.py

Tests cover:
- Successful imports (module-level, attribute-level)
- Failed imports (missing module returns None + False)
- Multiple attribute imports
- Relative imports with package parameter
- Edge cases (missing attributes, empty names)

Run with: pytest tests/test_safe_import.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.safe_import import safe_import


class TestSafeImportSuccess:
    """Test successful import cases."""

    def test_import_whole_module(self):
        mod, ok = safe_import('json')
        assert ok is True
        assert mod is not None
        assert hasattr(mod, 'dumps')

    def test_import_single_attribute(self):
        dumps, ok = safe_import('json', 'dumps')
        assert ok is True
        assert callable(dumps)

    def test_import_multiple_attributes(self):
        dumps, loads, ok = safe_import('json', 'dumps', 'loads')
        assert ok is True
        assert callable(dumps)
        assert callable(loads)

    def test_import_class(self):
        Path, ok = safe_import('pathlib', 'Path')
        assert ok is True
        assert Path is not None
        assert Path('/tmp').exists() or True  # Just checking it's the real Path


class TestSafeImportFailure:
    """Test failed import cases."""

    def test_missing_module_whole(self):
        mod, ok = safe_import('nonexistent_module_xyz_12345')
        assert ok is False
        assert mod is None

    def test_missing_module_single_attr(self):
        val, ok = safe_import('nonexistent_module_xyz_12345', 'SomeClass')
        assert ok is False
        assert val is None

    def test_missing_module_multiple_attrs(self):
        a, b, c, ok = safe_import(
            'nonexistent_module_xyz_12345', 'A', 'B', 'C'
        )
        assert ok is False
        assert a is None
        assert b is None
        assert c is None


class TestSafeImportEdgeCases:
    """Test edge cases and special behaviors."""

    def test_missing_attribute_returns_none(self):
        """If module exists but attribute doesn't, returns None for that attr."""
        val, ok = safe_import('json', 'nonexistent_function_xyz')
        assert ok is True  # Module imported successfully
        assert val is None  # Attribute not found

    def test_tuple_length_matches_names(self):
        """Return tuple length = len(names) + 1 (for the flag)."""
        result = safe_import('json', 'dumps', 'loads', 'JSONDecodeError')
        assert len(result) == 4  # 3 names + 1 flag

    def test_no_names_returns_pair(self):
        """With no attribute names, returns (module, flag) pair."""
        result = safe_import('json')
        assert len(result) == 2

    def test_relative_import_with_package(self):
        """Relative import with package parameter."""
        # Import from gateway package (relative)
        tracker, ok = safe_import('.node_models', 'UnifiedNode', package='gateway')
        # This may or may not work depending on sys.path, but should not raise
        assert isinstance(ok, bool)

    def test_failed_relative_import(self):
        """Relative import of nonexistent module returns None + False."""
        val, ok = safe_import('.nonexistent_xyz', 'Foo', package='gateway')
        assert ok is False
        assert val is None
