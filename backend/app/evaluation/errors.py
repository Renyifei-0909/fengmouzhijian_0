from __future__ import annotations

from typing import Any


class EvaluationError(RuntimeError):
    """Base class for machine-readable evaluation failures."""

    exit_code = 2
    category = "contract"

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }
        if self.path is not None:
            payload["path"] = self.path
        if self.details:
            payload["details"] = self.details
        return payload


class ContractError(EvaluationError):
    """The submitted JSON or declared business contract is invalid."""


class IntegrityError(EvaluationError):
    """Frozen bytes, split isolation, or content identities are inconsistent."""

    exit_code = 3
    category = "integrity"


class ExecutionError(EvaluationError):
    """A development runner failed without producing a valid scoreable result."""

    exit_code = 4
    category = "execution"


__all__ = ["ContractError", "EvaluationError", "ExecutionError", "IntegrityError"]
