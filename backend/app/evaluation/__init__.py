"""Offline, content-addressed evaluation primitives.

This package is intentionally independent from the verification job, database,
HTTP API, and analyzer result contract.  A single inference response can never
become a competition metric through this module.
"""

from .bundle import (
    DevelopmentEvidenceReceipt,
    publish_development_evidence_bundle,
    verify_development_evidence_bundle,
)
from .controlled_bundle import verify_controlled_local_evidence_bundle
from .errors import ContractError, EvaluationError, ExecutionError, IntegrityError
from .executor import evaluator_source_sha256, run_development_plan
from .registry import (
    commit_holdout_exposure,
    finalize_holdout_attempt,
    get_holdout_attempt,
    list_holdout_attempts,
    mark_holdout_incident,
    reserve_holdout_attempt,
)
from .service import score_dataset, validate_dataset

__all__ = [
    "ContractError",
    "DevelopmentEvidenceReceipt",
    "EvaluationError",
    "ExecutionError",
    "IntegrityError",
    "commit_holdout_exposure",
    "evaluator_source_sha256",
    "finalize_holdout_attempt",
    "get_holdout_attempt",
    "list_holdout_attempts",
    "mark_holdout_incident",
    "publish_development_evidence_bundle",
    "reserve_holdout_attempt",
    "run_development_plan",
    "score_dataset",
    "validate_dataset",
    "verify_development_evidence_bundle",
    "verify_controlled_local_evidence_bundle",
]
