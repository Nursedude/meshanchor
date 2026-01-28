"""
Dependency Guard - Isolation layer for external libraries.

Protects MeshForge from upstream bugs in:
- Meshtastic (protocol changes, API breaks)
- RNS/Reticulum (cryptography issues, interface changes)
- LXMF (message format changes)
- Future protocols (Meshcore, etc.)

Strategies:
1. Safe imports with BaseException handling (pyo3 crashes, etc.)
2. Version compatibility checks
3. Feature contracts (expected API surface)
4. Graceful degradation with stub fallbacks
5. Centralized error reporting
"""

import importlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, Type
from packaging import version as pkg_version

logger = logging.getLogger(__name__)


# ============================================================================
# Exception Hierarchy
# ============================================================================

class DependencyError(Exception):
    """Base exception for all dependency-related errors."""

    def __init__(self, dependency: str, message: str, fix_hint: Optional[str] = None):
        self.dependency = dependency
        self.fix_hint = fix_hint
        super().__init__(f"[{dependency}] {message}")


class DependencyNotInstalled(DependencyError):
    """Dependency package is not installed."""
    pass


class DependencyImportFailed(DependencyError):
    """Dependency installed but import crashed (e.g., pyo3 panic)."""
    pass


class DependencyVersionMismatch(DependencyError):
    """Dependency version doesn't meet requirements."""

    def __init__(self, dependency: str, required: str, found: str):
        self.required = required
        self.found = found
        super().__init__(
            dependency,
            f"Version {found} found, but {required} required",
            fix_hint=f"pip install --upgrade {dependency}"
        )


class DependencyContractViolation(DependencyError):
    """Dependency API changed - expected interface not found."""

    def __init__(self, dependency: str, missing_attr: str):
        self.missing_attr = missing_attr
        super().__init__(
            dependency,
            f"Missing expected attribute: {missing_attr}",
            fix_hint=f"Dependency API may have changed. Check {dependency} changelog."
        )


# ============================================================================
# Dependency Status
# ============================================================================

class DependencyState(Enum):
    """State of a dependency."""
    AVAILABLE = "available"        # Working correctly
    NOT_INSTALLED = "not_installed"  # Not present
    IMPORT_FAILED = "import_failed"  # Crashes on import
    VERSION_MISMATCH = "version_mismatch"  # Wrong version
    CONTRACT_BROKEN = "contract_broken"  # API changed
    UNKNOWN = "unknown"


@dataclass
class DependencyStatus:
    """Status of a single dependency."""
    name: str
    state: DependencyState
    version: Optional[str] = None
    error: Optional[str] = None
    fix_hint: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.state == DependencyState.AVAILABLE

    def __bool__(self) -> bool:
        return self.available


# ============================================================================
# Dependency Contracts
# ============================================================================

@dataclass
class DependencyContract:
    """
    Defines expected API surface for a dependency.

    Used to detect breaking changes early.
    """
    package_name: str
    import_name: str  # What to import (may differ from package name)
    min_version: Optional[str] = None
    max_version: Optional[str] = None
    required_attrs: List[str] = field(default_factory=list)
    required_callables: List[str] = field(default_factory=list)
    fix_hint: str = ""

    def validate(self, module: Any) -> Tuple[bool, List[str]]:
        """Validate module meets contract. Returns (valid, issues)."""
        issues = []

        # Check version
        module_version = getattr(module, '__version__', None)
        if module_version:
            try:
                v = pkg_version.parse(module_version)
                if self.min_version and v < pkg_version.parse(self.min_version):
                    issues.append(f"Version {module_version} < minimum {self.min_version}")
                if self.max_version and v > pkg_version.parse(self.max_version):
                    issues.append(f"Version {module_version} > maximum {self.max_version}")
            except Exception:
                pass  # Version parsing failed, skip check

        # Check required attributes
        for attr in self.required_attrs:
            if not hasattr(module, attr):
                issues.append(f"Missing attribute: {attr}")

        # Check required callables
        for name in self.required_callables:
            obj = getattr(module, name, None)
            if obj is None:
                issues.append(f"Missing callable: {name}")
            elif not callable(obj):
                issues.append(f"Not callable: {name}")

        return len(issues) == 0, issues


# ============================================================================
# Known Dependency Contracts
# ============================================================================

DEPENDENCY_CONTRACTS: Dict[str, DependencyContract] = {
    'meshtastic': DependencyContract(
        package_name='meshtastic',
        import_name='meshtastic',
        min_version='2.0.0',
        required_attrs=['__version__'],
        required_callables=[],
        fix_hint="pip install --upgrade meshtastic"
    ),
    'RNS': DependencyContract(
        package_name='rns',
        import_name='RNS',
        min_version='0.6.0',
        required_attrs=['__version__', 'Reticulum', 'Identity', 'Destination', 'Transport'],
        required_callables=[],
        fix_hint="pipx install rns  (or pipx upgrade rns)"
    ),
    'LXMF': DependencyContract(
        package_name='lxmf',
        import_name='LXMF',
        min_version='0.3.0',
        required_attrs=['__version__', 'LXMessage', 'LXMRouter'],
        required_callables=[],
        fix_hint="pip install --upgrade lxmf"
    ),
}


# ============================================================================
# Safe Import Functions
# ============================================================================

def safe_import(
    module_name: str,
    contract: Optional[DependencyContract] = None
) -> Tuple[Optional[Any], DependencyStatus]:
    """
    Safely import a module with full error handling.

    Handles:
    - ImportError (not installed)
    - pyo3 PanicException (cryptography crashes)
    - Version mismatches
    - API contract violations

    Returns:
        Tuple of (module or None, DependencyStatus)
    """
    status = DependencyStatus(
        name=module_name,
        state=DependencyState.UNKNOWN
    )

    try:
        module = importlib.import_module(module_name)
        status.version = getattr(module, '__version__', 'unknown')

        # Validate contract if provided
        if contract:
            valid, issues = contract.validate(module)
            if not valid:
                status.state = DependencyState.CONTRACT_BROKEN
                status.error = "; ".join(issues)
                status.fix_hint = contract.fix_hint
                logger.warning(f"Dependency {module_name} contract broken: {issues}")
                return None, status

        status.state = DependencyState.AVAILABLE
        return module, status

    except ImportError as e:
        status.state = DependencyState.NOT_INSTALLED
        status.error = str(e)
        status.fix_hint = f"pip install {module_name}"
        return None, status

    except (SystemExit, KeyboardInterrupt, GeneratorExit):
        raise

    except BaseException as e:
        # Catch pyo3 PanicException and other crashes
        status.state = DependencyState.IMPORT_FAILED
        status.error = f"{type(e).__name__}: {e}"
        status.fix_hint = f"Reinstall: pip install --force-reinstall {module_name}"
        logger.error(f"Dependency {module_name} crashed on import: {e}")
        return None, status


def require_dependency(
    module_name: str,
    contract: Optional[DependencyContract] = None
) -> Any:
    """
    Import a dependency or raise DependencyError.

    Use when the dependency is required for operation.
    """
    module, status = safe_import(module_name, contract)

    if module is not None:
        return module

    if status.state == DependencyState.NOT_INSTALLED:
        raise DependencyNotInstalled(
            module_name,
            "Package not installed",
            fix_hint=status.fix_hint
        )
    elif status.state == DependencyState.IMPORT_FAILED:
        raise DependencyImportFailed(
            module_name,
            f"Import crashed: {status.error}",
            fix_hint=status.fix_hint
        )
    elif status.state == DependencyState.CONTRACT_BROKEN:
        raise DependencyContractViolation(
            module_name,
            status.error or "Unknown contract violation"
        )
    else:
        raise DependencyError(
            module_name,
            f"Unknown error: {status.error}"
        )


# ============================================================================
# Decorator for Protected Functions
# ============================================================================

def requires(*dependencies: str, graceful: bool = False):
    """
    Decorator to protect functions that need external dependencies.

    Args:
        dependencies: Module names required
        graceful: If True, return None instead of raising on failure

    Example:
        @requires('RNS', 'LXMF')
        def send_lxmf_message(content):
            ...

        @requires('meshtastic', graceful=True)
        def get_node_info():
            ...  # Returns None if meshtastic unavailable
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            for dep in dependencies:
                contract = DEPENDENCY_CONTRACTS.get(dep)
                module, status = safe_import(dep, contract)

                if not status.available:
                    if graceful:
                        logger.debug(f"{func.__name__} skipped: {dep} unavailable")
                        return None
                    else:
                        raise DependencyError(
                            dep,
                            f"Required for {func.__name__}",
                            fix_hint=status.fix_hint
                        )

            return func(*args, **kwargs)

        # Attach metadata
        wrapper._required_dependencies = dependencies
        wrapper._graceful = graceful
        return wrapper

    return decorator


# ============================================================================
# Dependency Registry
# ============================================================================

class DependencyRegistry:
    """
    Central registry for tracking dependency status.

    Provides:
    - Cached import results
    - Batch status checks
    - Change detection (for hot-reload scenarios)
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[Optional[Any], DependencyStatus]] = {}
        self._listeners: List[Callable[[str, DependencyStatus], None]] = []

    def check(self, module_name: str, force: bool = False) -> DependencyStatus:
        """Check dependency status, using cache unless forced."""
        if not force and module_name in self._cache:
            return self._cache[module_name][1]

        contract = DEPENDENCY_CONTRACTS.get(module_name)
        module, status = safe_import(module_name, contract)
        self._cache[module_name] = (module, status)

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(module_name, status)
            except Exception as e:
                logger.error(f"Dependency listener error: {e}")

        return status

    def get_module(self, module_name: str) -> Optional[Any]:
        """Get cached module if available."""
        if module_name not in self._cache:
            self.check(module_name)
        return self._cache.get(module_name, (None, None))[0]

    def check_all(self, force: bool = False) -> Dict[str, DependencyStatus]:
        """Check all known dependencies."""
        results = {}
        for name in DEPENDENCY_CONTRACTS:
            results[name] = self.check(name, force=force)
        return results

    def add_listener(self, callback: Callable[[str, DependencyStatus], None]):
        """Add listener for dependency status changes."""
        self._listeners.append(callback)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all dependency states."""
        statuses = self.check_all()

        return {
            'total': len(statuses),
            'available': sum(1 for s in statuses.values() if s.available),
            'missing': sum(1 for s in statuses.values() if s.state == DependencyState.NOT_INSTALLED),
            'broken': sum(1 for s in statuses.values() if s.state in (
                DependencyState.IMPORT_FAILED,
                DependencyState.CONTRACT_BROKEN
            )),
            'dependencies': {
                name: {
                    'state': status.state.value,
                    'version': status.version,
                    'error': status.error,
                    'fix_hint': status.fix_hint
                }
                for name, status in statuses.items()
            }
        }


# Global registry instance
registry = DependencyRegistry()


# ============================================================================
# Convenience Functions
# ============================================================================

def check_dependency(name: str) -> bool:
    """Quick check if dependency is available."""
    return registry.check(name).available


def get_dependency_summary() -> Dict[str, Any]:
    """Get summary of all tracked dependencies."""
    return registry.get_summary()


def refresh_dependencies():
    """Force recheck all dependencies."""
    registry.check_all(force=True)


# ============================================================================
# Protocol-Specific Helpers
# ============================================================================

def get_rns() -> Optional[Any]:
    """Get RNS module if available, None otherwise."""
    return registry.get_module('RNS')


def get_lxmf() -> Optional[Any]:
    """Get LXMF module if available, None otherwise."""
    return registry.get_module('LXMF')


def get_meshtastic() -> Optional[Any]:
    """Get meshtastic module if available, None otherwise."""
    return registry.get_module('meshtastic')


def is_rns_available() -> bool:
    """Check if RNS is available and working."""
    return check_dependency('RNS')


def is_lxmf_available() -> bool:
    """Check if LXMF is available and working."""
    return check_dependency('LXMF')


def is_meshtastic_available() -> bool:
    """Check if meshtastic is available and working."""
    return check_dependency('meshtastic')


# ============================================================================
# Self-Test
# ============================================================================

def _self_test():
    """Run self-test of dependency guard."""
    print("Dependency Guard Self-Test")
    print("=" * 50)

    summary = get_dependency_summary()
    print(f"Total tracked: {summary['total']}")
    print(f"Available: {summary['available']}")
    print(f"Missing: {summary['missing']}")
    print(f"Broken: {summary['broken']}")
    print()

    for name, info in summary['dependencies'].items():
        state = info['state']
        version = info['version'] or 'N/A'
        icon = '✓' if state == 'available' else '✗'
        print(f"  {icon} {name}: {state} (v{version})")
        if info['error']:
            print(f"      Error: {info['error']}")
        if info['fix_hint']:
            print(f"      Fix: {info['fix_hint']}")


if __name__ == '__main__':
    _self_test()
