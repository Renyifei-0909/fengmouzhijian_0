from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DesignBaseline,
    EvidenceAsset,
    FindingCase,
    HumanReview,
    Project,
    SensorEvent,
    VerificationJob,
)
from .analyzers.contracts import delivery_classification
from .remediation import remediation_context_for_job


def _iso_utc(value: datetime) -> str:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def _sensor_payload(event: SensorEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "device_id": event.device_id,
        "kind": event.kind,
        "value": event.value,
        "unit": event.unit,
        "captured_at": _iso_utc(event.captured_at),
        "sha256": event.sha256,
    }


def _finding_case_payload(case: FindingCase) -> dict[str, object]:
    return {
        "id": case.id,
        "finding_index": case.finding_index,
        "finding_key": case.finding_key,
        "finding_sha256": case.finding_sha256,
        "finding_code": case.finding_code,
        "proposed_severity": case.proposed_severity,
        "confirmed_severity": case.confirmed_severity,
        "message": case.finding_message,
        "scope": case.scope,
        "status_at_seal": case.status,
        "analysis_mode": case.analysis_mode,
        "source_synthetic": case.source_synthetic,
        "source_evidence_grade": case.source_evidence_grade,
        "decision_reason": case.decision_reason,
        "confirmed_by": case.confirmed_by,
        "confirmed_at": _iso_utc(case.confirmed_at) if case.confirmed_at else None,
        "version_at_seal": case.version,
    }


def _report_truth_boundary(result: dict[str, object] | None) -> list[str]:
    """Describe the persisted analyzer mode without promoting human approval."""

    payload = result or {}
    mode = payload.get("analysis_mode")
    evidence_grade = payload.get("evidence_grade") is True
    boundaries = [
        "A sealed report records workflow evidence; it is not an accuracy evaluation by itself.",
        "Analyzer findings are candidate observations; only reviewer-triaged operational cases count as alarms, and demo cases are excluded from operational totals.",
    ]
    if mode == "demo_fixture":
        boundaries.append(
            "demo_fixture is deterministic synthetic output and is never valid as model performance evidence."
        )
    elif mode == "stub":
        boundaries.append(
            "stub is a safe workflow placeholder and makes no physical measurement or recognition-capability claim."
        )
    elif mode == "remote_http":
        boundaries.append(
            "remote_http records one pinned remote inference response, not a frozen EvaluationRun or a verified accuracy metric."
        )
    elif evidence_grade:
        boundaries.append(
            "evidence_grade=true must remain bound to a server-controlled frozen EvaluationRun and its immutable artifacts."
        )
    else:
        boundaries.append(
            "This analyzer output is not marked as evaluation evidence and cannot support a competition metric claim."
        )
    boundaries.append(
        "Human approval records a review decision; it does not change the analyzer mode or upgrade evidence eligibility."
    )
    return boundaries


def render_final_report(
    db: Session,
    *,
    job: VerificationJob,
    review: HumanReview,
    report_id: str,
    created_at: datetime,
) -> tuple[dict[str, object], str, bytes, bytes]:
    """Render deterministic report bytes without publishing or mutating the DB."""

    project = db.get(Project, job.project_id)
    baseline = db.get(DesignBaseline, job.baseline_id)
    evidence = db.get(EvidenceAsset, job.evidence_id)
    if project is None or baseline is None or evidence is None:
        raise RuntimeError("Cannot build report because a linked record is missing")

    sensors = db.scalars(
        select(SensorEvent)
        .where(SensorEvent.project_id == project.id, SensorEvent.site_id == baseline.site_id)
        .order_by(SensorEvent.captured_at.asc(), SensorEvent.id.asc())
    ).all()
    finding_cases = db.scalars(
        select(FindingCase)
        .where(FindingCase.source_job_id == job.id)
        .order_by(FindingCase.finding_index.asc(), FindingCase.id.asc())
    ).all()
    evidence_grade = bool((job.result_json or {}).get("evidence_grade", False))
    report_status, _ = delivery_classification(job.result_json)
    content = {
        "schema_version": "1.0",
        "report_id": report_id,
        "status": report_status,
        "evidence_grade": evidence_grade,
        "created_at": _iso_utc(created_at),
        "project": {
            "id": project.id,
            "code": project.code,
            "name": project.name,
            "location": project.location,
        },
        "design_baseline": {
            "id": baseline.id,
            "site_id": baseline.site_id,
            "procedure_code": baseline.procedure_code,
            "version": baseline.version,
            "source_type": baseline.source_type,
            "expected": baseline.expected,
            "sha256": baseline.sha256,
        },
        "evidence": {
            "id": evidence.id,
            "original_name": evidence.original_name,
            "content_type": evidence.content_type,
            "size_bytes": evidence.size_bytes,
            "sha256": evidence.sha256,
            "captured_at": _iso_utc(evidence.captured_at) if evidence.captured_at else None,
            "device_id": evidence.device_id,
        },
        "analysis": job.result_json,
        "human_review": {
            "id": review.id,
            "decision": review.decision,
            "reviewer": review.reviewer,
            "note": review.note,
            "reviewed_at": _iso_utc(review.reviewed_at),
        },
        "related_sensor_events": [_sensor_payload(item) for item in sensors],
        "finding_cases": [_finding_case_payload(item) for item in finding_cases],
        "remediation_context": remediation_context_for_job(db, job),
        "truth_boundary": _report_truth_boundary(job.result_json),
    }

    json_bytes, html_bytes = render_report_bytes(content, report_id=report_id)
    return content, report_status, json_bytes, html_bytes


def render_report_bytes(content: dict[str, object], *, report_id: str) -> tuple[bytes, bytes]:
    """Render a previously frozen report snapshot into JSON and HTML bytes."""

    json_bytes = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    safe_json = html.escape(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True))
    html_bytes = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>隐蔽工程结构化验真报告</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "max-width:1000px;margin:40px auto;padding:0 24px;color:#0f172a}"
        "h1{color:#075985}pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;"
        "border:1px solid #e2e8f0;border-radius:16px;padding:24px;line-height:1.55}</style>"
        "</head><body><h1>隐蔽工程结构化验真报告</h1>"
        f"<p>报告编号：{html.escape(report_id)}</p><pre>{safe_json}</pre></body></html>"
    ).encode("utf-8")
    return json_bytes, html_bytes
