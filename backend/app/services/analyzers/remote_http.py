from __future__ import annotations

import json
import os
from typing import Annotated, Any, Callable, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ...models import DesignBaseline, EvidenceAsset
from ..storage import canonical_json_bytes, sha256_bytes
from .base import current_validated_evidence_source
from .contracts import AnalyzerFinding, AnalyzerObservations, invalid_json_scalar_path, protected_claim_path


REMOTE_CONTRACT_VERSION = "1.0"
REMOTE_BRIDGE_VERSION = "remote-http-v1"
MAX_REMOTE_REQUEST_BYTES = 64 * 1024


class RemoteAnalyzerError(RuntimeError):
    """A bounded, user-safe failure raised by the remote model bridge."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.safe_message = message
        self.retryable = retryable
        self.upstream_status = upstream_status


class RemoteModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


RuntimeCapability = Annotated[
    str,
    Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_.-]*$"),
]


class RemoteRuntimeIdentity(BaseModel):
    """Machine-enforced distinction between a real model and a contract stub."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"mode": {"const": "model"}}},
                    "then": {
                        "properties": {
                            "model_loaded": {"const": True},
                            "capabilities": {"minItems": 1},
                        },
                    },
                },
                {
                    "if": {"properties": {"mode": {"const": "stub"}}},
                    "then": {
                        "properties": {
                            "model_loaded": {"const": False},
                            "capabilities": {"maxItems": 0},
                        }
                    },
                },
            ]
        },
    )

    mode: Literal["model", "stub"]
    model_loaded: bool
    capabilities: list[RuntimeCapability] = Field(
        max_length=100,
        json_schema_extra={"uniqueItems": True},
    )

    @model_validator(mode="after")
    def validate_runtime_identity(self) -> "RemoteRuntimeIdentity":
        if self.mode == "model" and not self.model_loaded:
            raise ValueError("model runtime must report model_loaded=true")
        if self.mode == "stub" and self.model_loaded:
            raise ValueError("stub runtime must report model_loaded=false")
        if self.mode == "stub" and self.capabilities:
            raise ValueError("stub runtime must not declare model capabilities")
        if self.mode == "model" and not self.capabilities:
            raise ValueError("model runtime must declare at least one capability")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("runtime capabilities must be unique")
        return self


class RemoteAlignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=100)
    differences: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


class RemoteAnalyzerResponse(BaseModel):
    """Fields an algorithm service is allowed to return.

    Protected business fields such as evidence_grade and accuracy_claim are not
    part of this model. Extra fields are rejected instead of being trusted.
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=200)
    model: RemoteModelIdentity
    runtime: RemoteRuntimeIdentity
    observations: AnalyzerObservations
    alignment: RemoteAlignment
    findings: list[AnalyzerFinding] = Field(max_length=1000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list, max_length=100)


class RemoteRequestedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=120)
    media_probe: "RemoteMediaProbe" = Field(default_factory=lambda: RemoteMediaProbe())


class RemoteMediaProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: float | None = Field(default=None, ge=0)
    format_name: str | None = Field(default=None, max_length=200)
    video_codec: str | None = Field(default=None, max_length=100)
    width: int | None = Field(default=None, ge=1, le=100_000)
    height: int | None = Field(default=None, ge=1, le=100_000)
    frame_rate: str | None = Field(default=None, max_length=64)


class RemoteBaselineMeasurements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_depth_m: float | None = Field(default=None, ge=0, le=100_000)
    min_spacing_m: float | None = Field(default=None, ge=0, le=100_000)
    expected_quantity: int | None = Field(default=None, ge=0, le=10_000_000)
    expected_specification: str | None = Field(default=None, max_length=500)


class RemoteBaselineExpected(BaseModel):
    """Explicit data-egress allowlist for the current remote contract."""

    model_config = ConfigDict(extra="forbid")

    scene_type: str | None = Field(default=None, max_length=100)
    measurements: RemoteBaselineMeasurements = Field(default_factory=RemoteBaselineMeasurements)


class RemoteBaselineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    site_id: str = Field(min_length=1, max_length=100)
    procedure_code: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    source_type: str = Field(min_length=1, max_length=32)
    expected: RemoteBaselineExpected
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteOutputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_review_required: Literal[True]
    evidence_grade_controlled_by_business_service: Literal[True]
    accuracy_claims_forbidden: Literal[True]


class RemoteAnalyzerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"]
    job_id: str = Field(min_length=1, max_length=100)
    task_type: Literal["construction_evidence_analysis"]
    model: RemoteRequestedModel
    evidence: RemoteEvidenceRequest
    baseline: RemoteBaselineRequest
    output_policy: RemoteOutputPolicy


ClientFactory = Callable[[], httpx.Client]


def remote_adapter_version(
    url: str,
    model_name: str,
    model_version: str,
    model_sha256: str,
    expected_runtime_mode: str = "model",
) -> str:
    """Return a full configuration fingerprint that fits the persisted version field."""

    return sha256_bytes(
        canonical_json_bytes(
            {
                "bridge_version": REMOTE_BRIDGE_VERSION,
                "contract_version": REMOTE_CONTRACT_VERSION,
                "endpoint": url,
                "model_name": model_name,
                "model_version": model_version,
                "model_artifact_sha256": model_sha256,
                "expected_runtime_mode": expected_runtime_mode,
            }
        )
    )


def _validation_summary(exc: ValidationError) -> str:
    messages: list[str] = []
    for item in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "response"
        messages.append(f"{location}: {item['msg']}")
    return "; ".join(messages)[:2000]


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant is forbidden: {value}")


class RemoteHTTPAnalyzer:
    name = "remote_http"

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None,
        model_name: str,
        model_version: str,
        expected_model_sha256: str,
        job_id: str,
        timeout_seconds: float,
        max_upload_bytes: int,
        max_response_bytes: int,
        expected_runtime_mode: Literal["model", "stub"] = "model",
        client_factory: ClientFactory | None = None,
    ) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Remote analyzer URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Remote analyzer URL must not contain credentials, query parameters, or a fragment")
        if timeout_seconds <= 0:
            raise ValueError("Remote analyzer timeout must be positive")
        if max_upload_bytes <= 0 or max_response_bytes <= 0:
            raise ValueError("Remote analyzer byte limits must be positive")
        if expected_runtime_mode not in {"model", "stub"}:
            raise ValueError("Remote analyzer runtime mode must be 'model' or 'stub'")

        self.url = url
        self.api_key = api_key
        self.model_name = model_name
        self.model_version = model_version
        self.expected_model_sha256 = expected_model_sha256
        self.expected_runtime_mode = expected_runtime_mode
        self.job_id = job_id
        self.timeout_seconds = timeout_seconds
        self.max_upload_bytes = max_upload_bytes
        self.max_response_bytes = max_response_bytes
        self.version = remote_adapter_version(
            url,
            model_name,
            model_version,
            expected_model_sha256,
            expected_runtime_mode,
        )
        port = f":{parsed.port}" if parsed.port else ""
        self.endpoint_identity = f"{parsed.scheme}://{parsed.hostname}{port}"
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=self.timeout_seconds, follow_redirects=False, trust_env=False)
        )

    def analyze(self, evidence: EvidenceAsset, baseline: DesignBaseline) -> dict[str, Any]:
        evidence_source = current_validated_evidence_source()
        if evidence_source is None:
            raise RemoteAnalyzerError(
                "REMOTE_INPUT_MISSING",
                "Remote analyzer requires an open, integrity-validated evidence source",
            )
        try:
            evidence_descriptor = evidence_source.fileno()
            descriptor_stat = os.fstat(evidence_descriptor)
        except (OSError, ValueError) as exc:
            raise RemoteAnalyzerError(
                "REMOTE_INPUT_MISSING",
                "Remote analyzer validated evidence source is unavailable",
            ) from exc
        source_identity = (
            evidence_source.stat_result.st_dev,
            evidence_source.stat_result.st_ino,
            evidence_source.stat_result.st_size,
            evidence_source.stat_result.st_mtime_ns,
        )
        descriptor_identity = (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
            descriptor_stat.st_size,
            descriptor_stat.st_mtime_ns,
        )
        if (
            source_identity != descriptor_identity
            or evidence_source.path.name != evidence.stored_name
            or evidence_source.stored_name != evidence.stored_name
            or evidence_source.sha256 != evidence.sha256
            or evidence_source.content_type != evidence.content_type
            or descriptor_stat.st_size != evidence.size_bytes
        ):
            raise RemoteAnalyzerError(
                "REMOTE_INPUT_HASH_MISMATCH",
                "Validated evidence source no longer matches the queued evidence record",
            )
        if evidence.size_bytes > self.max_upload_bytes:
            raise RemoteAnalyzerError(
                "REMOTE_INPUT_TOO_LARGE",
                f"Evidence exceeds the configured remote analyzer upload limit ({self.max_upload_bytes} bytes)",
            )
        media_probe_source = (evidence.metadata_json or {}).get("media_probe", {})
        media_probe = {
            key: media_probe_source.get(key)
            for key in (
                "duration_seconds",
                "format_name",
                "video_codec",
                "width",
                "height",
                "frame_rate",
            )
            if isinstance(media_probe_source, dict) and key in media_probe_source
        }

        try:
            request_document = RemoteAnalyzerRequest(
                contract_version=REMOTE_CONTRACT_VERSION,
                job_id=self.job_id,
                task_type="construction_evidence_analysis",
                model=RemoteRequestedModel(
                    name=self.model_name,
                    version=self.model_version,
                    artifact_sha256=self.expected_model_sha256,
                ),
                evidence=RemoteEvidenceRequest(
                    id=evidence.id,
                    sha256=evidence.sha256,
                    size_bytes=evidence.size_bytes,
                    content_type=evidence_source.content_type,
                    media_probe=media_probe,
                ),
                baseline=RemoteBaselineRequest(
                    id=baseline.id,
                    site_id=baseline.site_id,
                    procedure_code=baseline.procedure_code,
                    version=baseline.version,
                    source_type=baseline.source_type,
                    expected=baseline.expected,
                    sha256=baseline.sha256,
                ),
                output_policy=RemoteOutputPolicy(
                    human_review_required=True,
                    evidence_grade_controlled_by_business_service=True,
                    accuracy_claims_forbidden=True,
                ),
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise RemoteAnalyzerError(
                "REMOTE_REQUEST_INVALID",
                f"Remote request metadata is outside the allowlisted contract: {_validation_summary(exc)}",
            ) from exc
        request_bytes = canonical_json_bytes(request_document)
        if len(request_bytes) > MAX_REMOTE_REQUEST_BYTES:
            raise RemoteAnalyzerError(
                "REMOTE_REQUEST_TOO_LARGE",
                f"Remote request metadata exceeds {MAX_REMOTE_REQUEST_BYTES} bytes",
            )
        idempotency_key = sha256_bytes(
            canonical_json_bytes(
                {
                    "job_id": self.job_id,
                    "evidence_sha256": evidence.sha256,
                    "baseline_sha256": baseline.sha256,
                    "model_sha256": self.expected_model_sha256,
                }
            )
        )
        headers = {
            "Accept": "application/json",
            "X-Fengmou-Contract-Version": REMOTE_CONTRACT_VERSION,
            "X-Fengmou-Request-ID": self.job_id,
            "X-Evidence-SHA256": evidence.sha256,
            "X-Baseline-SHA256": baseline.sha256,
            "Idempotency-Key": idempotency_key,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            with self._client_factory() as client, open(
                evidence_descriptor,
                "rb",
                buffering=0,
                closefd=False,
            ) as handle:
                handle.seek(0)
                suffix = evidence_source.path.suffix.lower()
                files = {
                    "evidence": (f"evidence-{evidence.id}{suffix}", handle, evidence_source.content_type),
                    "request": ("request.json", request_bytes, "application/json"),
                }
                with client.stream("POST", self.url, headers=headers, files=files) as response:
                    if response.status_code != 200:
                        raise RemoteAnalyzerError(
                            "REMOTE_HTTP_ERROR",
                            f"Remote analyzer returned HTTP {response.status_code}",
                            retryable=response.status_code == 429 or response.status_code >= 500,
                            upstream_status=response.status_code,
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type != "application/json":
                        raise RemoteAnalyzerError(
                            "REMOTE_RESPONSE_CONTENT_TYPE",
                            "Remote analyzer success response must use application/json",
                        )
                    response_bytes = self._read_bounded_response(response)
        except RemoteAnalyzerError:
            raise
        except httpx.TimeoutException as exc:
            raise RemoteAnalyzerError(
                "REMOTE_TIMEOUT",
                f"Remote analyzer timed out after {self.timeout_seconds:g} seconds",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise RemoteAnalyzerError(
                "REMOTE_TRANSPORT_ERROR",
                f"Remote analyzer transport failed: {type(exc).__name__}",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise RemoteAnalyzerError(
                "REMOTE_INPUT_READ_ERROR",
                f"Remote analyzer could not read the evidence: {exc}",
            ) from exc

        try:
            payload = json.loads(response_bytes, parse_constant=_reject_non_json_constant)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise RemoteAnalyzerError(
                "REMOTE_RESPONSE_INVALID",
                "Remote analyzer response is not valid JSON",
            ) from exc

        invalid_scalar_path = invalid_json_scalar_path(payload)
        if invalid_scalar_path:
            raise RemoteAnalyzerError(
                "REMOTE_RESPONSE_INVALID",
                f"Remote analyzer response contains a non-finite number or invalid Unicode scalar at {invalid_scalar_path}",
            )
        protected_path = protected_claim_path(payload, allow_root=False)
        if protected_path:
            raise RemoteAnalyzerError(
                "REMOTE_RESPONSE_PROTECTED_CLAIM",
                f"Remote analyzer response attempted to inject a protected business claim at {protected_path}",
            )
        try:
            remote = RemoteAnalyzerResponse.model_validate(payload)
        except ValidationError as exc:
            raise RemoteAnalyzerError(
                "REMOTE_RESPONSE_INVALID",
                f"Remote analyzer response contract invalid: {_validation_summary(exc)}",
            ) from exc

        if remote.request_id != self.job_id:
            raise RemoteAnalyzerError(
                "REMOTE_REQUEST_ID_MISMATCH",
                "Remote analyzer request_id does not match the queued job",
            )
        if (
            remote.model.name != self.model_name
            or remote.model.version != self.model_version
            or remote.model.artifact_sha256 != self.expected_model_sha256
        ):
            raise RemoteAnalyzerError(
                "REMOTE_MODEL_IDENTITY_DRIFT",
                "Remote analyzer model identity differs from the pinned job configuration",
            )
        if remote.runtime.mode != self.expected_runtime_mode:
            raise RemoteAnalyzerError(
                "REMOTE_RUNTIME_MODE_MISMATCH",
                "Remote analyzer runtime mode differs from the pinned job configuration",
            )

        findings = [item.model_dump(mode="json") for item in remote.findings]
        findings.append(
            {
                "code": "REMOTE_RESULT_REQUIRES_VALIDATION",
                "severity": "info",
                "message": (
                    "The remote service produced a model result, but no frozen EvaluationRun is linked; "
                    "human review cannot convert it into competition metric evidence."
                ),
            }
        )
        is_stub = remote.runtime.mode == "stub"
        return {
            "schema_version": "1.0",
            "analysis_mode": self.name,
            "evidence_grade": False,
            "analyzer": {"name": self.name, "version": self.version},
            "provenance": {
                "kind": "remote_contract_stub" if is_stub else "remote_model_unvalidated",
                "synthetic": is_stub,
                "warning": (
                    "Remote contract STUB output is not real model inference or evaluation evidence."
                    if is_stub
                    else "Remote inference output is not a frozen EvaluationRun or a verified accuracy claim."
                ),
                "endpoint": self.endpoint_identity,
                "request_id": remote.request_id,
                "request_sha256": sha256_bytes(request_bytes),
                "response_sha256": sha256_bytes(response_bytes),
                "idempotency_key": idempotency_key,
                "model": remote.model.model_dump(mode="json"),
                "runtime": remote.runtime.model_dump(mode="json"),
                "limitations": remote.limitations,
            },
            "input": {
                "evidence_sha256": evidence.sha256,
                "baseline_sha256": baseline.sha256,
                "size_bytes": evidence.size_bytes,
                "content_type": evidence.content_type,
            },
            "observations": remote.observations.model_dump(mode="json"),
            "alignment": {
                **remote.alignment.model_dump(mode="json"),
                "baseline_version": baseline.version,
            },
            "findings": findings,
            "confidence": remote.confidence,
            "accuracy_claim": None,
            "recommended_action": "manual_review",
        }

    def _read_bounded_response(self, response: httpx.Response) -> bytes:
        body = bytearray()
        for chunk in response.iter_bytes():
            if len(body) + len(chunk) > self.max_response_bytes:
                raise RemoteAnalyzerError(
                    "REMOTE_RESPONSE_TOO_LARGE",
                    f"Remote analyzer response exceeds {self.max_response_bytes} bytes",
                )
            body.extend(chunk)
        return bytes(body)


__all__ = [
    "REMOTE_BRIDGE_VERSION",
    "REMOTE_CONTRACT_VERSION",
    "RemoteAnalyzerError",
    "RemoteAnalyzerRequest",
    "RemoteAnalyzerResponse",
    "RemoteHTTPAnalyzer",
    "RemoteRuntimeIdentity",
    "remote_adapter_version",
]
