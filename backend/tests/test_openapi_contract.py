from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
OPENAPI_ARTIFACT = PROJECT_ROOT / "docs" / "openapi-v1.json"
EXPORT_SCRIPT = BACKEND_ROOT / "scripts" / "export_openapi.py"


def _contract() -> dict[str, Any]:
    return json.loads(OPENAPI_ARTIFACT.read_text(encoding="utf-8"))


def _response_ref(operation: dict[str, Any], status_code: str) -> str:
    return operation["responses"][status_code]["content"]["application/json"]["schema"]["$ref"]


def _schema_ref(property_schema: dict[str, Any]) -> str | None:
    if "$ref" in property_schema:
        return property_schema["$ref"]
    for option in property_schema.get("anyOf", []):
        if "$ref" in option:
            return option["$ref"]
    return None


def _all_refs(value: Any):
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None:
            yield reference
        for child in value.values():
            yield from _all_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_refs(child)


def test_committed_openapi_artifact_matches_live_fastapi_schema() -> None:
    result = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT), "--check", "--output", str(OPENAPI_ARTIFACT)],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "verified" in result.stdout


def test_core_workflow_paths_keep_expected_methods_and_response_models() -> None:
    paths = _contract()["paths"]
    expected = {
        ("/api/v1/projects", "post", "201"): "ProjectRead",
        ("/api/v1/projects/{project_id}/overview", "get", "200"): "ProjectOverview",
        ("/api/v1/projects/{project_id}/baselines", "post", "201"): "BaselineRead",
        ("/api/v1/verifications", "post", "202"): "VerificationRead",
        ("/api/v1/verifications/{job_id}", "get", "200"): "VerificationDetail",
        ("/api/v1/verifications/{job_id}/review", "post", "200"): "ReviewOutcome",
        (
            "/api/v1/operations/verification-dispatch",
            "get",
            "200",
        ): "VerificationOperationsSnapshot",
        ("/api/v1/proofs/{proof_id}/verify", "get", "200"): "IntegrityCheck",
    }
    for (path, method, status_code), schema_name in expected.items():
        operation = paths[path][method]
        assert _response_ref(operation, status_code) == f"#/components/schemas/{schema_name}"

    metrics = paths["/api/v1/operations/verification-dispatch/metrics"]["get"]
    assert metrics["responses"]["200"]["content"] == {
        "text/plain": {"schema": {"type": "string"}}
    }
    assert "application/json" not in metrics["responses"]["200"]["content"]

    upload = paths["/api/v1/verifications"]["post"]
    assert set(upload["requestBody"]["content"]) == {"multipart/form-data"}
    assert upload["requestBody"]["required"] is True

    review = paths["/api/v1/verifications/{job_id}/review"]["post"]
    assert review["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ReviewRequest"
    }

    # These artifact endpoints are part of the delivery chain even though
    # FastAPI represents their FileResponse bodies with a generic schema.
    assert "get" in paths["/api/v1/reports/{report_id}/download"]
    assert "get" in paths["/api/v1/proofs/{proof_id}/archive"]

    evidence_content = paths["/api/v1/evidence-assets/{evidence_id}/content"]["get"]
    assert {"200", "206", "401", "404", "409", "410", "416"} <= set(evidence_content["responses"])
    expected_media = {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/webm",
        "image/jpeg",
        "image/png",
    }
    for status_code in ("200", "206"):
        media = evidence_content["responses"][status_code]["content"]
        assert set(media) == expected_media
        assert all(item["schema"] == {"type": "string", "format": "binary"} for item in media.values())
    for status_code in ("401", "404", "409", "410", "416"):
        assert _response_ref(evidence_content, status_code) == "#/components/schemas/EvidenceContentError"


def test_api_key_security_scheme_covers_every_non_public_operation() -> None:
    document = _contract()
    assert document["components"]["securitySchemes"] == {
        "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }

    public_operations = {
        ("/api/v1/healthz", "get"),
        ("/api/v1/readyz", "get"),
        ("/api/v1/meta", "get"),
    }
    operation_methods = {"get", "post", "put", "patch", "delete"}
    seen_public: set[tuple[str, str]] = set()
    for path, path_item in document["paths"].items():
        for method, operation in path_item.items():
            if method not in operation_methods:
                continue
            if (path, method) in public_operations:
                assert "security" not in operation
                seen_public.add((path, method))
            else:
                assert operation.get("security") == [{"APIKeyHeader": []}], f"{method.upper()} {path}"
    assert seen_public == public_operations


def test_core_component_schemas_preserve_required_fields_and_semantics() -> None:
    schemas = _contract()["components"]["schemas"]

    assert set(schemas["ProjectCreate"]["required"]) == {"code", "name", "location"}
    assert schemas["BaselineCreate"]["properties"]["source_type"]["enum"] == [
        "manual",
        "gis",
        "cad",
        "api",
    ]

    upload = schemas["Body_create_verification_api_v1_verifications_post"]
    assert set(upload["required"]) == {"project_id", "baseline_id", "file"}
    assert upload["properties"]["file"]["type"] == "string"
    assert upload["properties"]["file"]["contentMediaType"] == "application/octet-stream"
    assert upload["properties"]["analyzer"]["default"] == "stub"
    assert upload["properties"]["metadata"]["default"] == "{}"

    review = schemas["ReviewRequest"]
    assert set(review["required"]) == {"decision", "reviewer"}
    assert review["properties"]["decision"]["enum"] == ["approve", "reject"]

    detail = schemas["VerificationDetail"]
    assert set(detail["required"]) == {
        "job",
        "dispatch",
        "attempts",
        "evidence",
        "recovery",
    }
    dispatch = schemas["VerificationDispatch"]
    assert dispatch["properties"]["execution_mode"]["enum"] == ["inline", "external"]
    assert dispatch["properties"]["state"]["enum"] == [
        "unclaimed",
        "leased",
        "released",
        "dead_letter",
    ]
    assert _schema_ref(detail["properties"]["job"]) == "#/components/schemas/VerificationRead"
    assert _schema_ref(detail["properties"]["evidence"]) == "#/components/schemas/EvidenceRead"
    assert _schema_ref(detail["properties"]["report"]) == "#/components/schemas/ReportRead"
    assert _schema_ref(detail["properties"]["proof"]) == "#/components/schemas/ProofRead"
    assert _schema_ref(detail["properties"]["recovery"]) == "#/components/schemas/VerificationRecovery"
    assert detail["properties"]["attempts"]["items"]["$ref"] == (
        "#/components/schemas/VerificationAttemptRead"
    )

    attempt = schemas["VerificationAttemptRead"]
    assert {
        "id",
        "job_id",
        "generation",
        "attempt_no",
        "worker_ref",
        "execution_mode",
        "analyzer_name",
        "analyzer_version",
        "evidence_sha256",
        "baseline_sha256",
        "max_attempts",
        "claimed_at",
        "outcome",
    } == set(attempt["required"])
    assert attempt["properties"]["worker_ref"]["pattern"] == (
        "^sha256:[0-9a-f]{64}$"
    )
    assert "worker_id" not in attempt["properties"]
    attempt_outcome = schemas["VerificationAttemptOutcomeRead"]
    assert attempt_outcome["properties"]["disposition"]["enum"] == [
        "committed_success",
        "committed_failure",
        "lease_expired",
        "lease_lost",
        "write_fenced",
    ]
    assert "result_json" not in attempt_outcome["properties"]

    operations = schemas["VerificationOperationsSnapshot"]
    assert set(operations["required"]) == {
        "status",
        "generated_at",
        "execution_mode",
        "thresholds",
        "jobs",
        "dispatch",
        "attempts",
        "integrity",
        "alerts",
        "truth_note",
    }
    assert operations["properties"]["status"]["enum"] == [
        "healthy",
        "attention",
        "incident",
    ]
    assert operations["properties"]["execution_mode"]["enum"] == [
        "inline",
        "external",
    ]
    assert _schema_ref(operations["properties"]["dispatch"]) == (
        "#/components/schemas/VerificationOperationsDispatch"
    )
    assert _schema_ref(operations["properties"]["attempts"]) == (
        "#/components/schemas/VerificationOperationsAttempts"
    )
    operations_alert = schemas["VerificationOperationsAlert"]
    assert operations_alert["properties"]["severity"]["enum"] == [
        "warning",
        "incident",
    ]
    assert operations_alert["properties"]["code"]["enum"] == [
        "INTEGRITY_INCIDENT",
        "DEAD_LETTER_PRESENT",
        "QUEUE_WAIT_EXCEEDED",
        "RECENT_LEASE_INSTABILITY",
    ]
    assert "job_id" not in schemas["VerificationOperationsJobs"]["properties"]
    assert "worker_id" not in schemas["VerificationOperationsDispatch"]["properties"]
    assert schemas["VerificationOperationsJobs"]["properties"]["by_status"][
        "additionalProperties"
    ] == {"minimum": 0.0, "type": "integer"}
    assert schemas["VerificationOperationsJobs"]["properties"]["by_status"][
        "propertyNames"
    ]["enum"] == [
        "queued",
        "running",
        "needs_review",
        "sealing",
        "approved",
        "rejected",
        "failed",
        "other",
    ]
    for field in (
        "outcomes_total_by_disposition",
        "outcomes_window_by_disposition",
    ):
        assert schemas["VerificationOperationsAttempts"]["properties"][field][
            "additionalProperties"
        ] == {"minimum": 0.0, "type": "integer"}
        assert schemas["VerificationOperationsAttempts"]["properties"][field][
            "propertyNames"
        ]["enum"] == [
            "committed_success",
            "committed_failure",
            "lease_expired",
            "lease_lost",
            "write_fenced",
        ]

    recovery = schemas["VerificationRecovery"]
    assert recovery["properties"]["action"]["enum"] == [
        "none",
        "retry_analysis",
        "resume_sealing",
        "integrity_review",
    ]
    assert recovery["properties"]["retryable"]["type"] == "boolean"

    assert {"content", "sha256", "html_sha256"} <= set(schemas["ReportRead"]["required"])
    assert {
        "purpose",
        "evidence_grade",
        "merkle_root",
        "manifest_sha256",
        "archive_sha256",
        "record_hash",
        "ledger_index",
    } <= set(schemas["ProofRead"]["required"])
    assert schemas["ProofRead"]["properties"]["evidence_grade"]["type"] == "boolean"
    assert schemas["ProofRead"]["properties"]["ledger_index"]["type"] == "integer"

    integrity = schemas["IntegrityCheck"]
    assert set(integrity["required"]) == {"valid", "archive_id", "checked_at", "checks", "errors"}
    assert integrity["properties"]["checks"]["additionalProperties"] == {"type": "boolean"}
    assert integrity["properties"]["errors"]["items"] == {"type": "string"}


def test_every_local_schema_reference_resolves() -> None:
    document = _contract()
    available = set(document["components"]["schemas"])
    for reference in _all_refs(document):
        prefix = "#/components/schemas/"
        assert reference.startswith(prefix), reference
        assert reference.removeprefix(prefix) in available, reference
