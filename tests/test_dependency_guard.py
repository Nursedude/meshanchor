"""
Tests for Dependency Guard module.

Tests the isolation layer that protects MeshForge from upstream bugs.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from utils.dependency_guard import (
    DependencyError,
    DependencyNotInstalled,
    DependencyImportFailed,
    DependencyContractViolation,
    DependencyVersionMismatch,
    DependencyState,
    DependencyStatus,
    DependencyContract,
    DependencyRegistry,
    safe_import,
    require_dependency,
    requires,
    check_dependency,
    get_dependency_summary,
)


class TestDependencyExceptions:
    """Test exception hierarchy."""

    def test_base_exception(self):
        """DependencyError has correct attributes."""
        err = DependencyError("test_pkg", "Something broke", fix_hint="Try this")

        assert err.dependency == "test_pkg"
        assert err.fix_hint == "Try this"
        assert "test_pkg" in str(err)
        assert "Something broke" in str(err)

    def test_not_installed_exception(self):
        """DependencyNotInstalled is a DependencyError."""
        err = DependencyNotInstalled("missing_pkg", "Not found")

        assert isinstance(err, DependencyError)
        assert err.dependency == "missing_pkg"

    def test_import_failed_exception(self):
        """DependencyImportFailed for crash scenarios."""
        err = DependencyImportFailed("crashing_pkg", "pyo3 panic")

        assert isinstance(err, DependencyError)
        assert "pyo3 panic" in str(err)

    def test_version_mismatch_exception(self):
        """DependencyVersionMismatch has version info."""
        err = DependencyVersionMismatch("old_pkg", ">=2.0", "1.5")

        assert err.required == ">=2.0"
        assert err.found == "1.5"
        assert "1.5" in str(err)
        assert ">=2.0" in str(err)

    def test_contract_violation_exception(self):
        """DependencyContractViolation for API changes."""
        err = DependencyContractViolation("changed_pkg", "missing_function")

        assert err.missing_attr == "missing_function"
        assert "API" in err.fix_hint


class TestDependencyStatus:
    """Test DependencyStatus dataclass."""

    def test_available_status(self):
        """Available status is truthy."""
        status = DependencyStatus(
            name="test",
            state=DependencyState.AVAILABLE,
            version="1.0.0"
        )

        assert status.available is True
        assert bool(status) is True

    def test_not_installed_status(self):
        """Not installed status is falsy."""
        status = DependencyStatus(
            name="test",
            state=DependencyState.NOT_INSTALLED,
            error="Not found"
        )

        assert status.available is False
        assert bool(status) is False

    def test_import_failed_status(self):
        """Import failed status includes error."""
        status = DependencyStatus(
            name="test",
            state=DependencyState.IMPORT_FAILED,
            error="Panic!"
        )

        assert status.available is False
        assert status.error == "Panic!"


class TestDependencyContract:
    """Test contract validation."""

    def test_valid_contract(self):
        """Module meeting contract passes validation."""
        contract = DependencyContract(
            package_name="test",
            import_name="test",
            required_attrs=["__version__"],
            required_callables=[]
        )

        mock_module = MagicMock()
        mock_module.__version__ = "1.0.0"

        valid, issues = contract.validate(mock_module)

        assert valid is True
        assert len(issues) == 0

    def test_missing_attribute(self):
        """Missing attribute fails validation."""
        contract = DependencyContract(
            package_name="test",
            import_name="test",
            required_attrs=["important_thing"]
        )

        mock_module = MagicMock(spec=[])  # No attributes

        valid, issues = contract.validate(mock_module)

        assert valid is False
        assert any("important_thing" in i for i in issues)

    def test_missing_callable(self):
        """Missing callable fails validation."""
        contract = DependencyContract(
            package_name="test",
            import_name="test",
            required_callables=["do_stuff"]
        )

        mock_module = MagicMock(spec=[])

        valid, issues = contract.validate(mock_module)

        assert valid is False
        assert any("do_stuff" in i for i in issues)

    def test_version_too_low(self):
        """Version below minimum fails."""
        contract = DependencyContract(
            package_name="test",
            import_name="test",
            min_version="2.0.0"
        )

        mock_module = MagicMock()
        mock_module.__version__ = "1.5.0"

        valid, issues = contract.validate(mock_module)

        assert valid is False
        assert any("1.5.0" in i for i in issues)

    def test_version_too_high(self):
        """Version above maximum fails."""
        contract = DependencyContract(
            package_name="test",
            import_name="test",
            max_version="1.9.9"
        )

        mock_module = MagicMock()
        mock_module.__version__ = "2.0.0"

        valid, issues = contract.validate(mock_module)

        assert valid is False
        assert any("2.0.0" in i for i in issues)


class TestSafeImport:
    """Test safe_import function."""

    def test_import_stdlib(self):
        """Can import standard library."""
        module, status = safe_import("os")

        assert module is not None
        assert status.available is True
        assert status.state == DependencyState.AVAILABLE

    def test_import_nonexistent(self):
        """Nonexistent module returns None with status."""
        module, status = safe_import("definitely_not_a_real_package_xyz")

        assert module is None
        assert status.available is False
        assert status.state == DependencyState.NOT_INSTALLED
        assert status.fix_hint is not None

    def test_import_with_contract(self):
        """Contract validation during import."""
        contract = DependencyContract(
            package_name="os",
            import_name="os",
            required_attrs=["path"]
        )

        module, status = safe_import("os", contract)

        assert module is not None
        assert status.available is True

    def test_contract_failure(self):
        """Failed contract returns None."""
        contract = DependencyContract(
            package_name="os",
            import_name="os",
            required_attrs=["definitely_not_in_os"]
        )

        module, status = safe_import("os", contract)

        assert module is None
        assert status.state == DependencyState.CONTRACT_BROKEN


class TestRequireDependency:
    """Test require_dependency function."""

    def test_require_available(self):
        """Requiring available module returns it."""
        module = require_dependency("os")
        assert module is not None

    def test_require_missing_raises(self):
        """Requiring missing module raises."""
        with pytest.raises(DependencyNotInstalled):
            require_dependency("not_real_package_xyz")


class TestRequiresDecorator:
    """Test @requires decorator."""

    def test_decorator_allows_available(self):
        """Decorator allows execution with available deps."""
        @requires('os')
        def my_func():
            return "worked"

        result = my_func()
        assert result == "worked"

    def test_decorator_blocks_missing(self):
        """Decorator blocks with missing deps."""
        @requires('not_real_xyz')
        def my_func():
            return "worked"

        with pytest.raises(DependencyError):
            my_func()

    def test_graceful_returns_none(self):
        """Graceful mode returns None instead of raising."""
        @requires('not_real_xyz', graceful=True)
        def my_func():
            return "worked"

        result = my_func()
        assert result is None

    def test_metadata_attached(self):
        """Decorator attaches metadata."""
        @requires('os', 'sys')
        def my_func():
            pass

        assert hasattr(my_func, '_required_dependencies')
        assert 'os' in my_func._required_dependencies


class TestDependencyRegistry:
    """Test DependencyRegistry class."""

    def test_check_caches_result(self):
        """Registry caches check results."""
        reg = DependencyRegistry()

        status1 = reg.check("os")
        status2 = reg.check("os")

        assert status1.available is True
        assert status2.available is True

    def test_force_recheck(self):
        """Force flag bypasses cache."""
        reg = DependencyRegistry()

        status1 = reg.check("os")
        status2 = reg.check("os", force=True)

        # Both should work
        assert status1.available is True
        assert status2.available is True

    def test_get_module(self):
        """Can retrieve cached module."""
        reg = DependencyRegistry()

        module = reg.get_module("os")
        assert module is not None

    def test_get_summary(self):
        """Summary includes expected fields."""
        reg = DependencyRegistry()
        reg.check("os")

        summary = reg.get_summary()

        assert 'total' in summary
        assert 'available' in summary
        assert 'missing' in summary
        assert 'dependencies' in summary

    def test_listener_notification(self):
        """Listeners are notified of checks."""
        reg = DependencyRegistry()
        notifications = []

        def listener(name, status):
            notifications.append((name, status))

        reg.add_listener(listener)
        reg.check("os", force=True)

        assert len(notifications) == 1
        assert notifications[0][0] == "os"


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_check_dependency_stdlib(self):
        """check_dependency returns bool."""
        result = check_dependency("os")
        assert result is True

    def test_check_dependency_missing(self):
        """check_dependency false for missing."""
        result = check_dependency("not_real_xyz")
        assert result is False

    def test_get_dependency_summary_format(self):
        """get_dependency_summary returns dict."""
        summary = get_dependency_summary()

        assert isinstance(summary, dict)
        assert 'total' in summary
        assert 'available' in summary


class TestRealDependencies:
    """Test with actual MeshForge dependencies (may skip if unavailable)."""

    def test_meshtastic_check(self):
        """Check meshtastic dependency status."""
        module, status = safe_import("meshtastic")

        # Either available or not - both are valid
        assert isinstance(status, DependencyStatus)
        assert status.state in DependencyState

    def test_rns_check(self):
        """Check RNS dependency status."""
        module, status = safe_import("RNS")

        # Handle pyo3 crash gracefully
        assert isinstance(status, DependencyStatus)
        if status.state == DependencyState.IMPORT_FAILED:
            assert "error" in status.error.lower() or "panic" in status.error.lower()

    def test_lxmf_check(self):
        """Check LXMF dependency status."""
        module, status = safe_import("LXMF")

        assert isinstance(status, DependencyStatus)


def run_self_test():
    """Run module self-test."""
    from utils.dependency_guard import _self_test
    _self_test()


if __name__ == "__main__":
    run_self_test()
