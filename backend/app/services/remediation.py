from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal
import zipfile

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import (
    AuditEvent,
    FindingCase,
    FindingCaseCommand,
    ProofRecord,
    RemediationAttempt,
    StructuredReport,
    VerificationJob,
    new_id,
    utcnow,
)
from .proof import verify_proof_archive
from .storage import FileStorage, canonical_json_bytes, sha256_bytes


SYSTEM_NOTICE_CODES = frozenset(
    {
        "MODEL_NOT_CONNECTED",
        "DEMO_FIXTURE_ONLY",
        "REMOTE_RESULT_REQUIRES_VALIDATION",
    }
)
CASE_STATUSES = frozenset(
    {
        "pending_triage",
        "open",
        "remediation_in_progress",
        "verification_pending",
        "closed",
        "dismissed",
    }
)
SEVERITIES = frozenset({"info", "warning", "error", "critical"})


class RemediationConflictError(RuntimeError):
    pass


class RemediationIntegrityError(RuntimeError):
    pass


class RemediationValidationError(RuntimeError):
    pass


def _digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _iso(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def _audit(
    db: Session,
    *,
    case_id: str,
    action: str,
    actor: str,
    payload: dict[str, Any],
) -> None:
    db.add(
        AuditEvent(
            entity_type="finding_case",
            entity_id=case_id,
            action=action,
            actor=actor,
            payload_json=payload,
        )
    )


def _expected_scope(result: dict[str, Any]) -> str:
    provenance = result.get("provenance") or {}
    return "demo" if result.get("analysis_mode") == "demo_fixture" or provenance.get("synthetic") is True else "operational"


def _finding_identity(job: VerificationJob, result: dict[str, Any], index: int, finding: dict[str, Any]) -> tuple[str, str, str]:
    result_sha256 = _digest(result)
    finding_sha256 = _digest(finding)
    finding_key = _digest(
        {
            "job_id": job.id,
            "result_sha256": result_sha256,
            "finding_index": index,
            "finding": finding,
        }
    )
    return result_sha256, finding_sha256, finding_key


def materialize_finding_cases(db: Session, job: VerificationJob) -> list[FindingCase]:
    """Persist candidate cases for domain findings, excluding system truth notices.

    This function is idempotent and is called in the same transaction that
    moves the analysis job to ``needs_review``.
    """

    result = job.result_json
    if not isinstance(result, dict):
        raise RemediationIntegrityError("Verification job has no normalized analyzer result")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise RemediationIntegrityError("Analyzer result findings are not a list")
    analyzer = result.get("analyzer") or {}
    provenance = result.get("provenance") or {}
    scope = _expected_scope(result)
    created: list[FindingCase] = []
    for index, raw_finding in enumerate(findings):
        if not isinstance(raw_finding, dict):
            raise RemediationIntegrityError("Normalized analyzer finding is not an object")
        finding = dict(raw_finding)
        code = finding.get("code")
        if code in SYSTEM_NOTICE_CODES:
            continue
        severity = finding.get("severity")
        message = finding.get("message")
        if not isinstance(code, str) or severity not in SEVERITIES or not isinstance(message, str):
            raise RemediationIntegrityError("Normalized analyzer finding has invalid stable fields")
        if severity == "info":
            continue
        result_sha256, finding_sha256, finding_key = _finding_identity(job, result, index, finding)
        existing = db.scalar(
            select(FindingCase).where(
                FindingCase.source_job_id == job.id,
                FindingCase.finding_index == index,
            )
        )
        stable = (
            job.project_id,
            job.evidence_id,
            job.baseline_id,
            finding_key,
            finding_sha256,
            result_sha256,
            finding,
            job.analyzer_name,
            job.analyzer_version,
            result.get("analysis_mode"),
            provenance.get("synthetic") is True,
            result.get("evidence_grade") is True,
            code,
            severity,
            message,
            scope,
        )
        if existing is not None:
            actual = (
                existing.project_id,
                existing.source_evidence_id,
                existing.baseline_id,
                existing.finding_key,
                existing.finding_sha256,
                existing.source_result_sha256,
                existing.source_finding_json,
                existing.analyzer_name,
                existing.analyzer_version,
                existing.analysis_mode,
                existing.source_synthetic,
                existing.source_evidence_grade,
                existing.finding_code,
                existing.proposed_severity,
                existing.finding_message,
                existing.scope,
            )
            if actual != stable:
                raise RemediationIntegrityError("Existing finding case conflicts with its analyzer source")
            created.append(existing)
            continue
        case = FindingCase(
            id=new_id(),
            project_id=job.project_id,
            source_job_id=job.id,
            source_evidence_id=job.evidence_id,
            baseline_id=job.baseline_id,
            finding_key=finding_key,
            finding_index=index,
            finding_sha256=finding_sha256,
            source_result_sha256=result_sha256,
            source_finding_json=finding,
            analyzer_name=job.analyzer_name,
            analyzer_version=job.analyzer_version,
            analysis_mode=str(result.get("analysis_mode")),
            source_synthetic=provenance.get("synthetic") is True,
            source_evidence_grade=result.get("evidence_grade") is True,
            finding_code=code,
            proposed_severity=severity,
            finding_message=message,
            scope=scope,
            status="pending_triage",
        )
        db.add(case)
        db.flush()
        _audit(
            db,
            case_id=case.id,
            action="case_materialized_from_finding",
            actor="system",
            payload={
                "project_id": case.project_id,
                "source_job_id": case.source_job_id,
                "finding_index": index,
                "finding_sha256": finding_sha256,
                "scope": scope,
                "truth_boundary": "candidate finding; not a confirmed operational alarm",
            },
        )
        created.append(case)
    return created


def validate_case_integrity(db: Session, case: FindingCase) -> None:
    if case.status not in CASE_STATUSES:
        raise RemediationIntegrityError("Finding case has an unknown state")
    job = db.get(VerificationJob, case.source_job_id)
    if job is None or not isinstance(job.result_json, dict):
        raise RemediationIntegrityError("Finding case lost its source verification result")
    if (job.project_id, job.evidence_id, job.baseline_id) != (
        case.project_id,
        case.source_evidence_id,
        case.baseline_id,
    ):
        raise RemediationIntegrityError("Finding case project or source binding changed")
    findings = job.result_json.get("findings")
    if not isinstance(findings, list) or case.finding_index < 0 or case.finding_index >= len(findings):
        raise RemediationIntegrityError("Finding case source position no longer exists")
    raw = findings[case.finding_index]
    if not isinstance(raw, dict):
        raise RemediationIntegrityError("Finding case source is no longer an object")
    finding = dict(raw)
    result_sha256, finding_sha256, finding_key = _finding_identity(job, job.result_json, case.finding_index, finding)
    provenance = job.result_json.get("provenance") or {}
    expected = (
        finding_key,
        finding_sha256,
        result_sha256,
        finding,
        job.analyzer_name,
        job.analyzer_version,
        str(job.result_json.get("analysis_mode")),
        provenance.get("synthetic") is True,
        job.result_json.get("evidence_grade") is True,
        finding.get("code"),
        finding.get("severity"),
        finding.get("message"),
        _expected_scope(job.result_json),
    )
    actual = (
        case.finding_key,
        case.finding_sha256,
        case.source_result_sha256,
        case.source_finding_json,
        case.analyzer_name,
        case.analyzer_version,
        case.analysis_mode,
        case.source_synthetic,
        case.source_evidence_grade,
        case.finding_code,
        case.proposed_severity,
        case.finding_message,
        case.scope,
    )
    if actual != expected:
        raise RemediationIntegrityError("Finding case differs from its immutable source snapshot")


def _command_replay(
    db: Session,
    *,
    request_id: str,
    case_id: str,
    command: str,
    payload_sha256: str,
) -> FindingCase | None:
    existing = db.get(FindingCaseCommand, request_id)
    if existing is None:
        return None
    if (
        existing.case_id != case_id
        or existing.command != command
        or existing.payload_sha256 != payload_sha256
    ):
        raise RemediationConflictError("Idempotency key was already used with a different command")
    case = db.get(FindingCase, case_id)
    if case is None:
        raise RemediationIntegrityError("Idempotent command lost its finding case")
    validate_case_integrity(db, case)
    return case


def _apply_case_command(
    db: Session,
    *,
    case: FindingCase,
    request_id: str,
    command: str,
    actor: str,
    actor_role: str,
    expected_version: int,
    payload: dict[str, Any],
    target_status: str,
    values: dict[str, Any],
) -> FindingCase:
    payload_sha256 = _digest(payload)
    replay = _command_replay(
        db,
        request_id=request_id,
        case_id=case.id,
        command=command,
        payload_sha256=payload_sha256,
    )
    if replay is not None:
        return replay
    previous_status = case.status
    now = utcnow()
    transition = db.execute(
        update(FindingCase)
        .where(FindingCase.id == case.id, FindingCase.version == expected_version)
        .values(status=target_status, version=expected_version + 1, updated_at=now, **values)
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        raise RemediationConflictError("Finding case changed concurrently; refresh and retry")
    db.add(
        FindingCaseCommand(
            id=request_id,
            case_id=case.id,
            command=command,
            from_status=previous_status,
            to_status=target_status,
            actor=actor,
            actor_role=actor_role,
            payload_sha256=payload_sha256,
            result_version=expected_version + 1,
        )
    )
    _audit(
        db,
        case_id=case.id,
        action=command,
        actor=actor,
        payload={
            "from_status": previous_status,
            "to_status": target_status,
            "previous_version": expected_version,
            "new_version": expected_version + 1,
            "command_payload_sha256": payload_sha256,
            "finding_sha256": case.finding_sha256,
        },
    )
    db.flush()
    db.refresh(case)
    return case


def triage_case(
    db: Session,
    *,
    case: FindingCase,
    request_id: str,
    expected_version: int,
    decision: Literal["confirm", "dismiss"],
    confirmed_severity: str | None,
    reason: str,
    actor: str,
    actor_role: str,
) -> FindingCase:
    validate_case_integrity(db, case)
    payload = {
        "request_id": request_id,
        "expected_version": expected_version,
        "decision": decision,
        "confirmed_severity": confirmed_severity,
        "reason": reason,
    }
    replay = _command_replay(
        db,
        request_id=request_id,
        case_id=case.id,
        command="finding_confirmed" if decision == "confirm" else "finding_dismissed",
        payload_sha256=_digest(payload),
    )
    if replay is not None:
        return replay
    source_job = db.get(VerificationJob, case.source_job_id)
    allowed_source_states = {"needs_review", "approved"} if case.scope == "demo" else {"needs_review"}
    if source_job is None or source_job.status not in allowed_source_states:
        raise RemediationConflictError(
            "Operational finding triage must finish before source review; demo triage remains demonstration-only"
        )
    if case.status != "pending_triage" or case.version != expected_version:
        raise RemediationConflictError("Finding case is no longer pending triage")
    now = utcnow()
    if decision == "confirm":
        severity = confirmed_severity or case.proposed_severity
        if severity not in SEVERITIES:
            raise RemediationValidationError("Confirmed severity is invalid")
        return _apply_case_command(
            db,
            case=case,
            request_id=request_id,
            command="finding_confirmed",
            actor=actor,
            actor_role=actor_role,
            expected_version=expected_version,
            payload=payload,
            target_status="open",
            values={
                "confirmed_severity": severity,
                "decision_reason": reason,
                "confirmed_by": actor,
                "confirmed_at": now,
            },
        )
    return _apply_case_command(
        db,
        case=case,
        request_id=request_id,
        command="finding_dismissed",
        actor=actor,
        actor_role=actor_role,
        expected_version=expected_version,
        payload=payload,
        target_status="dismissed",
        values={
            "decision_reason": reason,
            "confirmed_by": actor,
            "confirmed_at": now,
        },
    )


def start_remediation(
    db: Session,
    *,
    case: FindingCase,
    request_id: str,
    expected_version: int,
    assignee: str,
    action_description: str,
    due_at: datetime | None,
    actor: str,
    actor_role: str,
) -> FindingCase:
    validate_case_integrity(db, case)
    source_job = db.get(VerificationJob, case.source_job_id)
    if source_job is None or source_job.status != "approved":
        raise RemediationConflictError("Remediation can start only after the source verification is approved and sealed")
    payload = {
        "request_id": request_id,
        "expected_version": expected_version,
        "assignee": assignee,
        "action_description": action_description,
        "due_at": due_at.isoformat() if due_at else None,
    }
    replay = _command_replay(
        db,
        request_id=request_id,
        case_id=case.id,
        command="remediation_started",
        payload_sha256=_digest(payload),
    )
    if replay is not None:
        return replay
    if case.status != "open" or case.version != expected_version:
        raise RemediationConflictError("Only an open confirmed case can start remediation")
    now = utcnow()
    return _apply_case_command(
        db,
        case=case,
        request_id=request_id,
        command="remediation_started",
        actor=actor,
        actor_role=actor_role,
        expected_version=expected_version,
        payload=payload,
        target_status="remediation_in_progress",
        values={
            "acknowledged_by": actor,
            "acknowledged_at": now,
            "assigned_to": assignee,
            "due_at": due_at,
        },
    )


def create_remediation_attempt(
    db: Session,
    *,
    case: FindingCase,
    client_request_id: str,
    expected_version: int,
    action_description: str,
    actor: str,
    actor_role: str,
) -> RemediationAttempt:
    validate_case_integrity(db, case)
    existing = db.scalar(
        select(RemediationAttempt).where(RemediationAttempt.client_request_id == client_request_id)
    )
    if existing is not None:
        if existing.case_id != case.id or existing.action_description != action_description:
            raise RemediationConflictError("Idempotency key was already used for another remediation attempt")
        return existing
    if case.status != "remediation_in_progress" or case.version != expected_version:
        raise RemediationConflictError("Case is not ready for a remediation submission")
    pending = db.scalar(
        select(RemediationAttempt).where(
            RemediationAttempt.case_id == case.id,
            RemediationAttempt.resolution_decision == "pending",
        )
    )
    if pending is not None:
        raise RemediationConflictError("Case already has a pending remediation attempt")
    next_number = (
        db.scalar(select(func.max(RemediationAttempt.attempt_no)).where(RemediationAttempt.case_id == case.id)) or 0
    ) + 1
    now = utcnow()
    transition = db.execute(
        update(FindingCase)
        .where(FindingCase.id == case.id, FindingCase.version == expected_version)
        .values(active_attempt_no=next_number, version=expected_version + 1, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if transition.rowcount != 1:
        raise RemediationConflictError("Finding case changed concurrently; refresh and retry")
    attempt = RemediationAttempt(
        id=new_id(),
        case_id=case.id,
        attempt_no=next_number,
        client_request_id=client_request_id,
        action_description=action_description,
        submitted_by=actor,
        submitted_at=now,
    )
    db.add(attempt)
    payload = {
        "client_request_id": client_request_id,
        "expected_version": expected_version,
        "action_description": action_description,
    }
    payload_sha256 = _digest(payload)
    db.add(
        FindingCaseCommand(
            id=client_request_id,
            case_id=case.id,
            command="remediation_attempt_created",
            from_status=case.status,
            to_status=case.status,
            actor=actor,
            actor_role=actor_role,
            payload_sha256=payload_sha256,
            result_version=expected_version + 1,
        )
    )
    _audit(
        db,
        case_id=case.id,
        action="remediation_attempt_created",
        actor=actor,
        payload={
            "attempt_id": attempt.id,
            "attempt_no": next_number,
            "previous_version": expected_version,
            "new_version": expected_version + 1,
            "command_payload_sha256": payload_sha256,
        },
    )
    db.flush()
    return attempt


def bind_attempt_to_verification(
    db: Session,
    *,
    attempt: RemediationAttempt,
    job: VerificationJob,
    actor: str,
) -> FindingCase:
    case = db.get(FindingCase, attempt.case_id)
    if case is None:
        raise RemediationIntegrityError("Remediation attempt lost its finding case")
    validate_case_integrity(db, case)
    if attempt.resolution_decision != "pending" or attempt.verification_job_id is not None:
        raise RemediationConflictError("Remediation attempt is already bound or resolved")
    if case.status != "remediation_in_progress" or case.active_attempt_no != attempt.attempt_no:
        raise RemediationConflictError("Finding case is not waiting for this remediation attempt")
    if (job.project_id, job.baseline_id) != (case.project_id, case.baseline_id):
        raise RemediationValidationError("Re-verification must use the same project and design baseline")
    previous_version = case.version
    now = utcnow()
    attempt_transition = db.execute(
        update(RemediationAttempt)
        .where(
            RemediationAttempt.id == attempt.id,
            RemediationAttempt.case_id == case.id,
            RemediationAttempt.resolution_decision == "pending",
            RemediationAttempt.verification_job_id.is_(None),
        )
        .values(verification_job_id=job.id)
        .execution_options(synchronize_session=False)
    )
    if attempt_transition.rowcount != 1:
        raise RemediationConflictError("Remediation attempt changed concurrently; refresh and retry")
    case_transition = db.execute(
        update(FindingCase)
        .where(
            FindingCase.id == case.id,
            FindingCase.status == "remediation_in_progress",
            FindingCase.active_attempt_no == attempt.attempt_no,
            FindingCase.version == previous_version,
        )
        .values(status="verification_pending", version=previous_version + 1, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if case_transition.rowcount != 1:
        raise RemediationConflictError("Finding case changed concurrently; refresh and retry")
    _audit(
        db,
        case_id=case.id,
        action="remediation_verification_bound",
        actor=actor,
        payload={
            "attempt_id": attempt.id,
            "verification_job_id": job.id,
            "from_status": "remediation_in_progress",
            "to_status": "verification_pending",
            "previous_version": previous_version,
            "new_version": previous_version + 1,
        },
    )
    db.flush()
    db.refresh(attempt)
    db.refresh(case)
    return case


def validate_attempt_for_verification(
    db: Session,
    *,
    attempt: RemediationAttempt,
    project_id: str,
    baseline_id: str,
) -> FindingCase:
    case = db.get(FindingCase, attempt.case_id)
    if case is None:
        raise RemediationIntegrityError("Remediation attempt lost its finding case")
    validate_case_integrity(db, case)
    if attempt.resolution_decision != "pending" or attempt.verification_job_id is not None:
        raise RemediationConflictError("Remediation attempt is already bound or resolved")
    if case.status != "remediation_in_progress" or case.active_attempt_no != attempt.attempt_no:
        raise RemediationConflictError("Finding case is not waiting for this remediation attempt")
    if (project_id, baseline_id) != (case.project_id, case.baseline_id):
        raise RemediationValidationError("Re-verification must use the same project and design baseline")
    return case


def prepare_remediation_review(
    db: Session,
    *,
    job: VerificationJob,
    review_decision: str,
    resolution_decision: str | None,
    resolution_note: str | None,
    actor: str,
) -> RemediationAttempt | None:
    attempt = db.scalar(select(RemediationAttempt).where(RemediationAttempt.verification_job_id == job.id))
    if attempt is None:
        if resolution_decision is not None:
            raise RemediationValidationError("A normal verification cannot include a remediation resolution")
        return None
    case = db.get(FindingCase, attempt.case_id)
    if case is None:
        raise RemediationIntegrityError("Remediation verification lost its finding case")
    validate_case_integrity(db, case)
    if attempt.resolution_decision != "pending":
        if attempt.resolution_decision == resolution_decision and attempt.resolution_note == resolution_note:
            return attempt
        raise RemediationConflictError("Remediation attempt already has a different resolution")
    if case.status != "verification_pending" or case.active_attempt_no != attempt.attempt_no:
        raise RemediationConflictError("Finding case is not waiting for this re-verification")
    if review_decision == "approve":
        if resolution_decision not in {"resolved", "not_resolved"}:
            raise RemediationValidationError("Re-verification approval requires an explicit remediation resolution")
        if not resolution_note or len(resolution_note.strip()) < 2:
            raise RemediationValidationError("Remediation resolution requires a reviewer note")
    else:
        if resolution_decision not in {None, "not_resolved"}:
            raise RemediationValidationError("A rejected re-verification cannot resolve the finding case")
        resolution_decision = "not_resolved"
        resolution_note = resolution_note or "Re-verification job was rejected by the reviewer."
    attempt.resolution_decision = resolution_decision
    attempt.resolution_note = resolution_note
    attempt.resolved_by = actor
    attempt.resolved_at = utcnow()
    if review_decision == "reject":
        previous_version = case.version
        case.status = "remediation_in_progress"
        case.active_attempt_no = None
        case.version += 1
        case.updated_at = utcnow()
        _audit(
            db,
            case_id=case.id,
            action="remediation_not_resolved",
            actor=actor,
            payload={
                "attempt_id": attempt.id,
                "verification_job_id": job.id,
                "reason": resolution_note,
                "previous_version": previous_version,
                "new_version": case.version,
            },
        )
    return attempt


def dismiss_cases_for_rejected_job(db: Session, *, job: VerificationJob, actor: str, reason: str) -> None:
    cases = list(db.scalars(select(FindingCase).where(FindingCase.source_job_id == job.id)).all())
    for case in cases:
        validate_case_integrity(db, case)
        if case.status == "dismissed":
            continue
        if case.status not in {"pending_triage", "open"}:
            raise RemediationConflictError("Source verification cannot be rejected after remediation has started")
        previous_status = case.status
        previous_version = case.version
        case.status = "dismissed"
        case.decision_reason = reason
        case.confirmed_by = actor
        case.confirmed_at = utcnow()
        case.version += 1
        case.updated_at = utcnow()
        _audit(
            db,
            case_id=case.id,
            action="case_dismissed_with_rejected_verification",
            actor=actor,
            payload={
                "source_job_id": job.id,
                "from_status": previous_status,
                "to_status": "dismissed",
                "previous_version": previous_version,
                "new_version": case.version,
                "reason": reason,
            },
        )


def remediation_context_for_job(db: Session, job: VerificationJob) -> dict[str, Any] | None:
    attempt = db.scalar(select(RemediationAttempt).where(RemediationAttempt.verification_job_id == job.id))
    if attempt is None:
        return None
    case = db.get(FindingCase, attempt.case_id)
    if case is None:
        raise RemediationIntegrityError("Remediation verification lost its finding case")
    validate_case_integrity(db, case)
    return {
        "case": {
            "id": case.id,
            "project_id": case.project_id,
            "source_job_id": case.source_job_id,
            "source_evidence_id": case.source_evidence_id,
            "baseline_id": case.baseline_id,
            "finding_key": case.finding_key,
            "finding_sha256": case.finding_sha256,
            "finding_code": case.finding_code,
            "proposed_severity": case.proposed_severity,
            "confirmed_severity": case.confirmed_severity,
            "finding_message": case.finding_message,
            "scope": case.scope,
            "analysis_mode": case.analysis_mode,
            "source_synthetic": case.source_synthetic,
            "source_evidence_grade": case.source_evidence_grade,
            "status_at_seal": case.status,
            "version_at_seal": case.version,
        },
        "attempt": {
            "id": attempt.id,
            "attempt_no": attempt.attempt_no,
            "action_description": attempt.action_description,
            "submitted_by": attempt.submitted_by,
            "submitted_at": _iso(attempt.submitted_at),
            "verification_job_id": attempt.verification_job_id,
            "resolution_decision": attempt.resolution_decision,
            "resolution_note": attempt.resolution_note,
            "resolved_by": attempt.resolved_by,
            "resolved_at": _iso(attempt.resolved_at) if attempt.resolved_at else None,
        },
        "truth_boundary": (
            "This is a point-in-time remediation context bound to the re-verification report. "
            "A closed case is valid only while its linked proof passes fresh integrity verification."
        ),
    }


def validate_frozen_remediation_context(db: Session, job: VerificationJob, frozen: Any) -> None:
    current = remediation_context_for_job(db, job)
    if current != frozen:
        raise RemediationIntegrityError("Remediation context differs from the frozen seal snapshot")


def finalize_remediation_after_seal(
    db: Session,
    *,
    job: VerificationJob,
    report: StructuredReport,
    proof: ProofRecord,
) -> None:
    attempt = db.scalar(select(RemediationAttempt).where(RemediationAttempt.verification_job_id == job.id))
    if attempt is None:
        return
    case = db.get(FindingCase, attempt.case_id)
    if case is None:
        raise RemediationIntegrityError("Remediation verification lost its finding case")
    if attempt.resolution_decision not in {"resolved", "not_resolved"}:
        raise RemediationIntegrityError("Remediation re-verification has no frozen reviewer resolution")
    if attempt.report_id is not None or attempt.proof_id is not None:
        if attempt.report_id != report.id or attempt.proof_id != proof.id:
            raise RemediationIntegrityError("Remediation attempt conflicts with its sealed artifacts")
        return
    previous_version = case.version
    attempt.report_id = report.id
    attempt.proof_id = proof.id
    case.active_attempt_no = None
    case.version += 1
    case.updated_at = utcnow()
    if attempt.resolution_decision == "resolved":
        case.status = "closed"
        case.closed_by = attempt.resolved_by
        case.closed_at = attempt.resolved_at or utcnow()
        case.closure_proof_id = proof.id
        action = "case_closed_with_sealed_reverification"
    else:
        case.status = "remediation_in_progress"
        case.closure_proof_id = None
        action = "remediation_not_resolved"
    _audit(
        db,
        case_id=case.id,
        action=action,
        actor=attempt.resolved_by or "system",
        payload={
            "attempt_id": attempt.id,
            "verification_job_id": job.id,
            "report_id": report.id,
            "proof_id": proof.id,
            "record_hash": proof.record_hash,
            "resolution_decision": attempt.resolution_decision,
            "previous_version": previous_version,
            "new_version": case.version,
        },
    )


def finding_case_summary(db: Session, project_id: str | None = None) -> dict[str, Any]:
    statement = select(FindingCase.scope, FindingCase.status, func.count()).group_by(
        FindingCase.scope, FindingCase.status
    )
    if project_id:
        statement = statement.where(FindingCase.project_id == project_id)
    counts = {(scope, state): count for scope, state, count in db.execute(statement).all()}
    return {
        "pending_triage": sum(count for (scope, state), count in counts.items() if state == "pending_triage"),
        "confirmed_open_operational": counts.get(("operational", "open"), 0),
        "remediation_in_progress_operational": counts.get(("operational", "remediation_in_progress"), 0),
        "verification_pending_operational": counts.get(("operational", "verification_pending"), 0),
        "closed_operational": counts.get(("operational", "closed"), 0),
        "dismissed_operational": counts.get(("operational", "dismissed"), 0),
        "demo_cases": sum(count for (scope, _state), count in counts.items() if scope == "demo"),
        "truth_note": (
            "Only reviewer-confirmed scope=operational cases enter operational alarm counts. "
            "Pending findings and demo_fixture cases are reported separately and are not model metrics."
        ),
    }


def _closed_case_snapshots(
    storage: FileStorage,
    *,
    proof: ProofRecord,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_path = storage.archive_dir / f"{proof.archive_id}.zip"
    archive_path = Path(proof.archive_path)
    if archive_path != expected_path or archive_path.is_symlink():
        raise RemediationIntegrityError("Closure proof archive path is not canonical")
    if not verify_proof_archive(proof, storage)["valid"]:
        raise RemediationIntegrityError("Closed case proof is missing or invalid")
    try:
        with zipfile.ZipFile(archive_path, "r") as bundle:
            frozen_case = json.loads(bundle.read("remediation/case.json"))
            frozen_attempt = json.loads(bundle.read("remediation/attempt.json"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise RemediationIntegrityError("Closure proof has no readable remediation snapshots") from exc
    if not isinstance(frozen_case, dict) or not isinstance(frozen_attempt, dict):
        raise RemediationIntegrityError("Closure proof remediation snapshots are not objects")
    return frozen_case, frozen_attempt


def _validate_closed_case_graph(
    db: Session,
    storage: FileStorage,
    *,
    case: FindingCase,
    attempts: list[RemediationAttempt],
) -> None:
    if case.active_attempt_no is not None:
        raise RemediationIntegrityError("Closed case still has an active remediation attempt")
    if case.closure_proof_id is None:
        raise RemediationIntegrityError("Closed case has no closure proof")
    closure_attempts = [item for item in attempts if item.proof_id == case.closure_proof_id]
    if len(closure_attempts) != 1:
        raise RemediationIntegrityError("Closed case does not have exactly one proof-bound remediation attempt")
    attempt = closure_attempts[0]
    if attempt.resolution_decision != "resolved":
        raise RemediationIntegrityError("Closed case remediation attempt is not resolved")
    if attempt.report_id is None or attempt.proof_id is None or attempt.verification_job_id is None:
        raise RemediationIntegrityError("Closed case remediation attempt has incomplete sealed artifacts")
    if attempt.proof_id != case.closure_proof_id:
        raise RemediationIntegrityError("Closed case proof differs from its remediation attempt")

    proof = db.get(ProofRecord, attempt.proof_id)
    report = db.get(StructuredReport, attempt.report_id)
    job = db.get(VerificationJob, attempt.verification_job_id)
    if proof is None or report is None or job is None:
        raise RemediationIntegrityError("Closed case lost its re-verification report or proof")
    if proof.report_id != attempt.report_id:
        raise RemediationIntegrityError("Closure proof is not bound to the remediation report")
    if report.job_id != attempt.verification_job_id:
        raise RemediationIntegrityError("Remediation report is not bound to the re-verification job")
    if (job.project_id, job.baseline_id, report.project_id) != (
        case.project_id,
        case.baseline_id,
        case.project_id,
    ):
        raise RemediationIntegrityError("Closed remediation graph crosses a project or design baseline")
    if job.status != "approved":
        raise RemediationIntegrityError("Closed case re-verification is not approved and sealed")
    if attempt.resolved_by is None or attempt.resolved_at is None:
        raise RemediationIntegrityError("Closed case has no reviewer resolution identity")
    if case.closed_by != attempt.resolved_by or case.closed_at is None or _iso(case.closed_at) != _iso(attempt.resolved_at):
        raise RemediationIntegrityError("Closed case identity differs from the reviewer resolution")

    frozen_case, frozen_attempt = _closed_case_snapshots(storage, proof=proof)
    expected_case_fields = {
        "id": case.id,
        "project_id": case.project_id,
        "source_job_id": case.source_job_id,
        "source_evidence_id": case.source_evidence_id,
        "baseline_id": case.baseline_id,
        "finding_key": case.finding_key,
        "finding_sha256": case.finding_sha256,
        "finding_code": case.finding_code,
        "proposed_severity": case.proposed_severity,
        "confirmed_severity": case.confirmed_severity,
        "finding_message": case.finding_message,
        "scope": case.scope,
        "analysis_mode": case.analysis_mode,
        "source_synthetic": case.source_synthetic,
        "source_evidence_grade": case.source_evidence_grade,
    }
    if any(frozen_case.get(key) != value for key, value in expected_case_fields.items()):
        raise RemediationIntegrityError("Closure proof case snapshot differs from the finding case")
    if frozen_case.get("status_at_seal") != "verification_pending" or frozen_case.get("version_at_seal") != case.version - 1:
        raise RemediationIntegrityError("Closure proof case state is not the pre-closure state")
    expected_attempt_fields = {
        "id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "action_description": attempt.action_description,
        "submitted_by": attempt.submitted_by,
        "submitted_at": _iso(attempt.submitted_at),
        "verification_job_id": attempt.verification_job_id,
        "resolution_decision": attempt.resolution_decision,
        "resolution_note": attempt.resolution_note,
        "resolved_by": attempt.resolved_by,
        "resolved_at": _iso(attempt.resolved_at),
    }
    if any(frozen_attempt.get(key) != value for key, value in expected_attempt_fields.items()):
        raise RemediationIntegrityError("Closure proof attempt snapshot differs from the remediation attempt")
    report_context = report.content_json.get("remediation_context") if isinstance(report.content_json, dict) else None
    if not isinstance(report_context, dict):
        raise RemediationIntegrityError("Remediation report has no frozen remediation context")
    if report_context.get("case") != frozen_case or report_context.get("attempt") != frozen_attempt:
        raise RemediationIntegrityError("Remediation report context differs from the closure proof")


def validate_remediation_graph(
    db: Session,
    storage: FileStorage,
    case: FindingCase,
    *,
    attempts: list[RemediationAttempt] | None = None,
) -> None:
    """Validate state-machine and cross-artifact bindings for one finding case."""

    related = attempts if attempts is not None else list(
        db.scalars(
            select(RemediationAttempt)
            .where(RemediationAttempt.case_id == case.id)
            .order_by(RemediationAttempt.attempt_no)
        ).all()
    )
    for attempt in related:
        if attempt.case_id != case.id:
            raise RemediationIntegrityError("Remediation attempt crosses finding cases")
        linked_job = db.get(VerificationJob, attempt.verification_job_id) if attempt.verification_job_id else None
        if attempt.verification_job_id and (
            linked_job is None
            or (linked_job.project_id, linked_job.baseline_id) != (case.project_id, case.baseline_id)
        ):
            raise RemediationIntegrityError("Remediation attempt has an invalid re-verification binding")
        if (attempt.report_id is None) != (attempt.proof_id is None):
            raise RemediationIntegrityError("Remediation attempt has an incomplete report/proof pair")
        if attempt.proof_id is not None:
            report = db.get(StructuredReport, attempt.report_id)
            proof = db.get(ProofRecord, attempt.proof_id)
            if (
                report is None
                or proof is None
                or linked_job is None
                or proof.report_id != report.id
                or report.job_id != linked_job.id
                or report.project_id != case.project_id
            ):
                raise RemediationIntegrityError("Remediation attempt sealed artifacts have invalid bindings")
        if attempt.resolution_decision == "pending":
            if attempt.resolution_note is not None or attempt.resolved_by is not None or attempt.resolved_at is not None:
                raise RemediationIntegrityError("Pending remediation attempt has reviewer resolution metadata")
        elif attempt.resolved_by is None or attempt.resolved_at is None or not attempt.resolution_note:
            raise RemediationIntegrityError("Resolved remediation attempt has incomplete reviewer metadata")

    active = [item for item in related if item.attempt_no == case.active_attempt_no]
    if case.active_attempt_no is not None and len(active) != 1:
        raise RemediationIntegrityError("Finding case active remediation attempt is missing or ambiguous")
    if case.status == "verification_pending":
        if len(active) != 1 or active[0].verification_job_id is None:
            raise RemediationIntegrityError("Verification-pending case has no active re-verification")
    elif case.status in {"pending_triage", "open", "dismissed"}:
        if case.active_attempt_no is not None or related:
            raise RemediationIntegrityError("Pre-remediation case unexpectedly has remediation attempts")
    elif case.status == "remediation_in_progress":
        if active:
            if active[0].resolution_decision != "pending" or active[0].verification_job_id is not None:
                raise RemediationIntegrityError("Active remediation submission is already bound or resolved")
        elif any(item.resolution_decision == "pending" for item in related):
            raise RemediationIntegrityError("Pending remediation submission is not the active attempt")
    elif case.status == "closed":
        _validate_closed_case_graph(db, storage, case=case, attempts=related)


def scan_remediation_integrity(db: Session, storage: FileStorage) -> list[str]:
    issues: list[str] = []
    cases = list(db.scalars(select(FindingCase)).all())
    for case in cases:
        try:
            validate_case_integrity(db, case)
            attempts = list(
                db.scalars(
                    select(RemediationAttempt)
                    .where(RemediationAttempt.case_id == case.id)
                    .order_by(RemediationAttempt.attempt_no)
                ).all()
            )
            numbers = [item.attempt_no for item in attempts]
            if numbers != list(range(1, len(numbers) + 1)):
                raise RemediationIntegrityError("Remediation attempt numbering is not contiguous")
            validate_remediation_graph(db, storage, case, attempts=attempts)
        except Exception as exc:
            issues.append(f"finding case {case.id}: {str(exc)[:300]}")
    return issues


__all__ = [
    "RemediationConflictError",
    "RemediationIntegrityError",
    "RemediationValidationError",
    "bind_attempt_to_verification",
    "create_remediation_attempt",
    "dismiss_cases_for_rejected_job",
    "finalize_remediation_after_seal",
    "finding_case_summary",
    "materialize_finding_cases",
    "prepare_remediation_review",
    "remediation_context_for_job",
    "scan_remediation_integrity",
    "start_remediation",
    "triage_case",
    "validate_case_integrity",
    "validate_remediation_graph",
    "validate_attempt_for_verification",
    "validate_frozen_remediation_context",
]
