from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
import os
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Protocol

from ...models import DesignBaseline, EvidenceAsset


class Analyzer(Protocol):
    name: str
    version: str

    def analyze(self, evidence: EvidenceAsset, baseline: DesignBaseline) -> dict[str, Any]: ...


class ValidatedEvidenceSource(Protocol):
    """Open, integrity-checked media source bound by the orchestration layer."""

    path: Path
    stat_result: os.stat_result
    content_type: str
    stored_name: str | None
    sha256: str | None

    def fileno(self) -> int: ...


_VALIDATED_EVIDENCE_SOURCE: ContextVar[ValidatedEvidenceSource | None] = ContextVar(
    "validated_evidence_source",
    default=None,
)


@contextmanager
def bind_validated_evidence_source(source: ValidatedEvidenceSource) -> Iterator[None]:
    """Make one already-open source available during a synchronous analyzer call."""

    token: Token[ValidatedEvidenceSource | None] = _VALIDATED_EVIDENCE_SOURCE.set(source)
    try:
        yield
    finally:
        _VALIDATED_EVIDENCE_SOURCE.reset(token)


def current_validated_evidence_source() -> ValidatedEvidenceSource | None:
    return _VALIDATED_EVIDENCE_SOURCE.get()
