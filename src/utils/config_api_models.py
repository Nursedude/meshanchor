"""Data classes and validator factory for the configuration API.

Code-motion extraction from ``utils/config_api.py`` (1,500-line rule):
the change/result/validation dataclasses and the :class:`ConfigValidator`
factory of reusable field validators. Import them from
``utils.config_api`` (the hub) — that module re-exports every public name
here, so external import paths are unchanged. Zero behavior change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


# =============================================================================
# Data Classes
# =============================================================================


class ConfigChangeType(Enum):
    """Type of configuration change."""
    SET = "set"
    DELETE = "delete"
    RESET = "reset"


@dataclass
class ConfigResult:
    """Result of a configuration operation."""
    success: bool
    path: str = ""
    value: Any = None
    error: Optional[str] = None
    change_type: Optional[ConfigChangeType] = None
    previous_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "path": self.path,
        }
        if self.value is not None:
            result["value"] = self.value
        if self.error:
            result["error"] = self.error
        if self.change_type:
            result["change_type"] = self.change_type.value
        if self.previous_value is not None:
            result["previous_value"] = self.previous_value
        return result


@dataclass
class ValidationResult:
    """Result of configuration validation."""
    valid: bool
    error: Optional[str] = None
    suggestion: Optional[str] = None

    @staticmethod
    def ok() -> ValidationResult:
        """Create a successful validation result."""
        return ValidationResult(valid=True)

    @staticmethod
    def fail(error: str, suggestion: str = None) -> ValidationResult:
        """Create a failed validation result."""
        return ValidationResult(valid=False, error=error, suggestion=suggestion)


@dataclass
class ConfigChange:
    """Record of a configuration change for audit logging."""
    timestamp: float
    path: str
    change_type: ConfigChangeType
    old_value: Any
    new_value: Any
    source: str = "api"  # api, file_reload, reset

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "path": self.path,
            "change_type": self.change_type.value,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "source": self.source,
        }


# =============================================================================
# Configuration Validators
# =============================================================================


class ConfigValidator:
    """Factory for common configuration validators."""

    @staticmethod
    def port_validator(min_port: int = 1, max_port: int = 65535) -> Callable[[Any], ValidationResult]:
        """Validate a port number."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, int):
                return ValidationResult.fail(
                    f"Port must be an integer, got {type(value).__name__}",
                    "Use an integer value like 37428"
                )
            if value < min_port or value > max_port:
                return ValidationResult.fail(
                    f"Port must be between {min_port} and {max_port}, got {value}",
                    f"Choose a port in valid range ({min_port}-{max_port})"
                )
            # Check if port is in common reserved range
            if value < 1024:
                return ValidationResult.fail(
                    f"Port {value} is in reserved range (requires root)",
                    "Choose a port >= 1024 for non-root operation"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def integer_validator(
        min_val: Optional[int] = None,
        max_val: Optional[int] = None
    ) -> Callable[[Any], ValidationResult]:
        """Validate an integer within optional bounds."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, int):
                return ValidationResult.fail(
                    f"Value must be an integer, got {type(value).__name__}"
                )
            if min_val is not None and value < min_val:
                return ValidationResult.fail(
                    f"Value must be >= {min_val}, got {value}"
                )
            if max_val is not None and value > max_val:
                return ValidationResult.fail(
                    f"Value must be <= {max_val}, got {value}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def float_validator(
        min_val: Optional[float] = None,
        max_val: Optional[float] = None
    ) -> Callable[[Any], ValidationResult]:
        """Validate a float within optional bounds."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, (int, float)):
                return ValidationResult.fail(
                    f"Value must be a number, got {type(value).__name__}"
                )
            if min_val is not None and value < min_val:
                return ValidationResult.fail(
                    f"Value must be >= {min_val}, got {value}"
                )
            if max_val is not None and value > max_val:
                return ValidationResult.fail(
                    f"Value must be <= {max_val}, got {value}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def string_validator(
        min_length: int = 0,
        max_length: Optional[int] = None,
        pattern: Optional[str] = None
    ) -> Callable[[Any], ValidationResult]:
        """Validate a string with optional length and pattern constraints."""
        compiled_pattern = re.compile(pattern) if pattern else None

        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, str):
                return ValidationResult.fail(
                    f"Value must be a string, got {type(value).__name__}"
                )
            if len(value) < min_length:
                return ValidationResult.fail(
                    f"String must be at least {min_length} characters"
                )
            if max_length is not None and len(value) > max_length:
                return ValidationResult.fail(
                    f"String must be at most {max_length} characters"
                )
            if compiled_pattern and not compiled_pattern.match(value):
                return ValidationResult.fail(
                    f"String does not match required pattern: {pattern}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def boolean_validator() -> Callable[[Any], ValidationResult]:
        """Validate a boolean value."""
        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, bool):
                return ValidationResult.fail(
                    f"Value must be a boolean, got {type(value).__name__}",
                    "Use true or false"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def enum_validator(allowed_values: List[Any]) -> Callable[[Any], ValidationResult]:
        """Validate value is in allowed set."""
        def validate(value: Any) -> ValidationResult:
            if value not in allowed_values:
                return ValidationResult.fail(
                    f"Value must be one of {allowed_values}, got {value!r}"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def hostname_validator() -> Callable[[Any], ValidationResult]:
        """Validate a hostname."""
        # RFC 1123 hostname pattern
        hostname_pattern = re.compile(
            r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'
            r'(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
        )

        def validate(value: Any) -> ValidationResult:
            if not isinstance(value, str):
                return ValidationResult.fail(
                    f"Hostname must be a string, got {type(value).__name__}"
                )
            if len(value) > 253:
                return ValidationResult.fail(
                    "Hostname exceeds maximum length of 253 characters"
                )
            if not hostname_pattern.match(value):
                return ValidationResult.fail(
                    f"Invalid hostname: {value}",
                    "Use valid hostname (e.g., localhost, node1.mesh.local)"
                )
            return ValidationResult.ok()
        return validate

    @staticmethod
    def composite(*validators: Callable[[Any], ValidationResult]) -> Callable[[Any], ValidationResult]:
        """Combine multiple validators (all must pass)."""
        def validate(value: Any) -> ValidationResult:
            for validator in validators:
                result = validator(value)
                if not result.valid:
                    return result
            return ValidationResult.ok()
        return validate
