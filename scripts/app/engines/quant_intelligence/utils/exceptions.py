"""
utils/exceptions.py -- Structured exception hierarchy for quant_intelligence.

Every exception carries a machine-readable `code` plus a human `context`
dict so the explainability engine (and callers/logs) can surface *why*
something failed without parsing strings.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class QuantIntelligenceError(Exception):
    """Base class for all errors raised by this module."""

    code: str = "QI_ERROR"

    def __init__(self, message: str, *, context: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.context:
            return f"[{self.code}] {self.message} | context={self.context}"
        return f"[{self.code}] {self.message}"


class ConfigError(QuantIntelligenceError):
    """Raised when configuration is missing, malformed, or invalid."""

    code = "QI_CONFIG_ERROR"


class ValidationError(QuantIntelligenceError):
    """Raised when an input Signal fails structural/range validation."""

    code = "QI_VALIDATION_ERROR"


class StorageError(QuantIntelligenceError):
    """Raised on local pattern/history storage read or write failures."""

    code = "QI_STORAGE_ERROR"


class EngineError(QuantIntelligenceError):
    """Raised when a sub-engine (probability/risk/regime/...) fails internally."""

    code = "QI_ENGINE_ERROR"
