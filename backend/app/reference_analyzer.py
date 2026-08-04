"""Runnable, contract-faithful reference service for the remote analyzer bridge.

This module is deliberately a STUB.  It validates transport and identity
boundaries, but its default predictor returns no physical observations and no
accuracy claim.  Run it separately with::

    uvicorn app.reference_analyzer:app --host 127.0.0.1 --port 8010
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartException

from .services.analyzers.contracts import (
    AnalyzerFinding,
    AnalyzerObservations,
    invalid_json_scalar_path,
    protected_claim_path,
)
from .services.analyzers.remote_http import (
    MAX_REMOTE_REQUEST_BYTES,
    REMOTE_CONTRACT_VERSION,
    RemoteAlignment,
    RemoteAnalyzerRequest,
    RemoteAnalyzerResponse,
    RemoteModelIdentity,
    RemoteRuntimeIdentity,
)
from .services.storage import CANONICAL_CONTENT_TYPES, _signature_matches, canonical_json_bytes, sha256_bytes


REFERENCE_STUB_ARTIFACT_RELATIVE_PATH = "examples/remote-analyzer-reference/reference-stub-artifact.json"
# SHA-256 of the exact committed bytes at REFERENCE_STUB_ARTIFACT_RELATIVE_PATH.
# This is a protocol-fixture declaration, not model weights or a performance claim.
REFERENCE_STUB_ARTIFACT_SHA256 = "15695d7820543d1217651812af54e91e71a30d355f8a2b851734a4f2483e454e"
REFERENCE_STUB_LIMITATION = (
    "STUB reference service only: no computer-vision model is loaded, no physical inference is performed, "
    "and this response is not accuracy or competition evidence."
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_RE = SHA256_RE
ALLOWED_MEDIA_TYPES = frozenset(CANONICAL_CONTENT_TYPES.values())
UPLOAD_CHUNK_SIZE = 1024 * 1024


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message}, headers=headers)


@dataclass(frozen=True, slots=True)
class ReferenceAnalyzerSettings:
    bearer_token: str | None = None
    model_name: str = "fengmou-reference-stub"
    model_version: str = "stub-v0.1"
    model_artifact_sha256: str = REFERENCE_STUB_ARTIFACT_SHA256
    max_upload_bytes: int = 100 * 1024 * 1024
    max_request_bytes: int = MAX_REMOTE_REQUEST_BYTES
    max_response_bytes: int = 2 * 1024 * 1024
    idempotency_capacity: int = 256

    def __post_init__(self) -> None:
        if self.bearer_token is not None and len(self.bearer_token) < 16:
            raise ValueError("Reference analyzer bearer token must contain at least 16 characters")
        if not 1 <= len(self.model_name) <= 100 or not 1 <= len(self.model_version) <= 100:
            raise ValueError("Reference analyzer model name/version must contain 1 to 100 characters")
        if not SHA256_RE.fullmatch(self.model_artifact_sha256):
            raise ValueError("Reference analyzer model artifact SHA-256 must be lowercase hexadecimal")
        if self.max_upload_bytes <= 0:
            raise ValueError("Reference analyzer upload limit must be positive")
        if not 0 < self.max_request_bytes <= 1024 * 1024:
            raise ValueError("Reference analyzer request limit must be in (0, 1 MiB]")
        if self.max_response_bytes <= 0:
            raise ValueError("Reference analyzer response limit must be positive")
        if not 1 <= self.idempotency_capacity <= 10_000:
            raise ValueError("Reference analyzer idempotency capacity must be between 1 and 10000")

    @classmethod
    def from_env(cls) -> "ReferenceAnalyzerSettings":
        return cls(
            bearer_token=os.getenv("FENGMOU_REFERENCE_ANALYZER_BEARER_TOKEN"),
            model_name=os.getenv("FENGMOU_REFERENCE_ANALYZER_MODEL_NAME", "fengmou-reference-stub"),
            model_version=os.getenv("FENGMOU_REFERENCE_ANALYZER_MODEL_VERSION", "stub-v0.1"),
            model_artifact_sha256=os.getenv(
                "FENGMOU_REFERENCE_ANALYZER_MODEL_SHA256",
                REFERENCE_STUB_ARTIFACT_SHA256,
            ),
            max_upload_bytes=int(
                os.getenv("FENGMOU_REFERENCE_ANALYZER_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))
            ),
            max_request_bytes=int(
                os.getenv("FENGMOU_REFERENCE_ANALYZER_MAX_REQUEST_BYTES", str(MAX_REMOTE_REQUEST_BYTES))
            ),
            max_response_bytes=int(
                os.getenv("FENGMOU_REFERENCE_ANALYZER_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024))
            ),
            idempotency_capacity=int(os.getenv("FENGMOU_REFERENCE_ANALYZER_IDEMPOTENCY_CAPACITY", "256")),
        )


class ReferencePrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: AnalyzerObservations
    alignment: RemoteAlignment
    findings: list[AnalyzerFinding] = Field(max_length=1000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list, max_length=100)


class ReferencePredictor(Protocol):
    def __call__(
        self,
        request_document: RemoteAnalyzerRequest,
        evidence_stream: BinaryIO,
    ) -> Mapping[str, Any]: ...


class StubReferencePredictor:
    """Safe default that proves only that the transport contract is runnable."""

    def __call__(
        self,
        request_document: RemoteAnalyzerRequest,
        evidence_stream: BinaryIO,
    ) -> Mapping[str, Any]:
        del request_document, evidence_stream
        return {
            "observations": {"measurements": {}, "objects": [], "events": []},
            "alignment": {"status": "not_evaluated", "differences": []},
            "findings": [
                {
                    "code": "REFERENCE_STUB_NO_MODEL",
                    "severity": "info",
                    "message": "Reference transport succeeded; no model inference was performed.",
                }
            ],
            "confidence": None,
            "limitations": [REFERENCE_STUB_LIMITATION],
        }


class IdempotencyConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _CachedResponse:
    identity_sha256: str
    response_bytes: bytes


class BoundedIdempotencyCache:
    """Process-local bounded LRU cache with atomic compute-once semantics."""

    def __init__(self, capacity: int) -> None:
        if not 1 <= capacity <= 10_000:
            raise ValueError("Idempotency cache capacity must be between 1 and 10000")
        self.capacity = capacity
        self._entries: OrderedDict[str, _CachedResponse] = OrderedDict()
        self._lock = threading.RLock()

    def get_or_compute(
        self,
        key: str,
        identity_sha256: str,
        producer: Callable[[], bytes],
    ) -> tuple[bytes, bool]:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                if not hmac.compare_digest(cached.identity_sha256, identity_sha256):
                    raise IdempotencyConflict("Idempotency key is already bound to a different request identity")
                self._entries.move_to_end(key)
                return bytes(cached.response_bytes), True
            response_bytes = bytes(producer())
            self._entries[key] = _CachedResponse(
                identity_sha256=identity_sha256,
                response_bytes=response_bytes,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)
            return bytes(response_bytes), False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON member is forbidden: {key}")
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant is forbidden: {value}")


async def _read_bounded(upload: StarletteUploadFile, limit: int, *, label: str) -> bytes:
    result = bytearray()
    while chunk := await upload.read(min(UPLOAD_CHUNK_SIZE, limit + 1 - len(result))):
        result.extend(chunk)
        if len(result) > limit:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"REFERENCE_{label.upper()}_TOO_LARGE",
                f"{label.capitalize()} exceeds the configured byte limit",
            )
    return bytes(result)


async def _hash_evidence(
    upload: StarletteUploadFile,
    *,
    limit: int,
) -> tuple[int, str, bytes]:
    total = 0
    digest = hashlib.sha256()
    header = bytearray()
    while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "REFERENCE_EVIDENCE_TOO_LARGE",
                "Evidence exceeds the configured upload limit",
            )
        digest.update(chunk)
        if len(header) < 64:
            header.extend(chunk[: 64 - len(header)])
    await upload.seek(0)
    return total, digest.hexdigest(), bytes(header)


def _authenticate(request: Request, settings: ReferenceAnalyzerSettings) -> None:
    if not settings.bearer_token:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "REFERENCE_AUTH_NOT_CONFIGURED",
            "Reference analyzer authentication is not configured",
        )
    authorization = request.headers.get("authorization", "")
    scheme, separator, credential = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not credential or not hmac.compare_digest(
        credential,
        settings.bearer_token,
    ):
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "REFERENCE_UNAUTHORIZED",
            "A valid Bearer credential is required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _require_header(request: Request, name: str) -> str:
    value = request.headers.get(name)
    if not value:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REFERENCE_HEADER_MISSING",
            f"Required header {name} is missing",
        )
    return value


def _validate_request_headers(
    request: Request,
    request_document: RemoteAnalyzerRequest,
    settings: ReferenceAnalyzerSettings,
) -> str:
    contract_version = _require_header(request, "X-Fengmou-Contract-Version")
    request_id = _require_header(request, "X-Fengmou-Request-ID")
    evidence_sha256 = _require_header(request, "X-Evidence-SHA256")
    baseline_sha256 = _require_header(request, "X-Baseline-SHA256")
    idempotency_key = _require_header(request, "Idempotency-Key")
    if contract_version != REMOTE_CONTRACT_VERSION or contract_version != request_document.contract_version:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REFERENCE_CONTRACT_MISMATCH",
            "Contract version header and request document must both be 1.0",
        )
    if request_id != request_document.job_id:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REFERENCE_REQUEST_ID_MISMATCH",
            "Request identity header does not match request.json",
        )
    if evidence_sha256 != request_document.evidence.sha256 or baseline_sha256 != request_document.baseline.sha256:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REFERENCE_DIGEST_HEADER_MISMATCH",
            "Evidence or baseline digest header does not match request.json",
        )
    if (
        request_document.model.name != settings.model_name
        or request_document.model.version != settings.model_version
        or request_document.model.artifact_sha256 != settings.model_artifact_sha256
    ):
        raise _error(
            status.HTTP_409_CONFLICT,
            "REFERENCE_MODEL_IDENTITY_MISMATCH",
            "Requested model identity does not match the configured reference identity",
        )
    expected_idempotency_key = sha256_bytes(
        canonical_json_bytes(
            {
                "job_id": request_document.job_id,
                "evidence_sha256": request_document.evidence.sha256,
                "baseline_sha256": request_document.baseline.sha256,
                "model_sha256": request_document.model.artifact_sha256,
            }
        )
    )
    if not IDEMPOTENCY_RE.fullmatch(idempotency_key) or not hmac.compare_digest(
        idempotency_key,
        expected_idempotency_key,
    ):
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REFERENCE_IDEMPOTENCY_KEY_MISMATCH",
            "Idempotency-Key does not match the bound request identity",
        )
    return idempotency_key


def _build_response_bytes(
    *,
    predictor: ReferencePredictor,
    settings: ReferenceAnalyzerSettings,
    request_document: RemoteAnalyzerRequest,
    evidence_stream: BinaryIO,
) -> bytes:
    try:
        evidence_stream.seek(0)
        raw_prediction = predictor(request_document, evidence_stream)
    except Exception as exc:
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "REFERENCE_PREDICTOR_FAILED",
            f"Reference predictor failed safely ({type(exc).__name__})",
        ) from exc
    invalid_path = invalid_json_scalar_path(raw_prediction)
    protected_path = protected_claim_path(raw_prediction, allow_root=False)
    if invalid_path or protected_path:
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "REFERENCE_PREDICTOR_OUTPUT_FORBIDDEN",
            "Reference predictor output contains a forbidden claim or JSON scalar",
        )
    try:
        prediction = ReferencePrediction.model_validate(raw_prediction)
        response = RemoteAnalyzerResponse(
            contract_version=REMOTE_CONTRACT_VERSION,
            request_id=request_document.job_id,
            model=RemoteModelIdentity(
                name=settings.model_name,
                version=settings.model_version,
                artifact_sha256=settings.model_artifact_sha256,
            ),
            runtime=RemoteRuntimeIdentity(
                mode="stub",
                model_loaded=False,
                capabilities=[],
            ),
            observations=prediction.observations,
            alignment=prediction.alignment,
            findings=prediction.findings,
            confidence=prediction.confidence,
            limitations=prediction.limitations,
        )
    except ValidationError as exc:
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "REFERENCE_PREDICTOR_OUTPUT_INVALID",
            "Reference predictor output does not satisfy the remote response contract",
        ) from exc
    normalized = response.model_dump(mode="json")
    if invalid_json_scalar_path(normalized) or protected_claim_path(normalized, allow_root=False):
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "REFERENCE_RESPONSE_FORBIDDEN",
            "Normalized reference response contains a forbidden claim or JSON scalar",
        )
    response_bytes = canonical_json_bytes(normalized)
    if len(response_bytes) > settings.max_response_bytes:
        raise _error(
            status.HTTP_502_BAD_GATEWAY,
            "REFERENCE_RESPONSE_TOO_LARGE",
            "Reference response exceeds the configured byte limit",
        )
    return response_bytes


def create_reference_analyzer(
    settings: ReferenceAnalyzerSettings | None = None,
    predictor: ReferencePredictor | None = None,
    cache: BoundedIdempotencyCache | None = None,
) -> FastAPI:
    active_settings = settings if settings is not None else ReferenceAnalyzerSettings.from_env()
    active_predictor = predictor if predictor is not None else StubReferencePredictor()
    active_cache = cache if cache is not None else BoundedIdempotencyCache(active_settings.idempotency_capacity)
    service = FastAPI(
        title="烽眸智鉴远程算法参考服务（STUB）",
        version="0.1.0",
        description=(
            "仅用于远程算法合同联调的安全占位服务；不加载模型，不执行视觉识别，不声明精度。"
        ),
    )
    service.state.settings = active_settings
    service.state.predictor = active_predictor
    service.state.idempotency_cache = active_cache

    @service.get("/healthz", tags=["system"])
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "stub",
            "model_loaded": False,
            "capabilities": [],
            "model": {
                "name": active_settings.model_name,
                "version": active_settings.model_version,
                "artifact_sha256": active_settings.model_artifact_sha256,
            },
            "authentication_configured": bool(active_settings.bearer_token),
            "truth_boundary": REFERENCE_STUB_LIMITATION,
        }

    @service.post("/v1/analyze", tags=["reference-analyzer"])
    async def analyze(request: Request) -> Response:
        _authenticate(request, active_settings)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "multipart/form-data":
            raise _error(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "REFERENCE_MULTIPART_REQUIRED",
                "Request Content-Type must be multipart/form-data",
            )
        try:
            async with request.form(
                max_files=2,
                max_fields=0,
                max_part_size=active_settings.max_request_bytes,
            ) as form:
                parts = form.multi_items()
                if len(parts) != 2 or {name for name, _ in parts} != {"evidence", "request"}:
                    raise _error(
                        status.HTTP_400_BAD_REQUEST,
                        "REFERENCE_MULTIPART_INVALID",
                        "Multipart body must contain exactly one evidence file and one request file",
                    )
                evidence_upload = form.get("evidence")
                request_upload = form.get("request")
                if not isinstance(evidence_upload, StarletteUploadFile) or not isinstance(
                    request_upload,
                    StarletteUploadFile,
                ):
                    raise _error(
                        status.HTTP_400_BAD_REQUEST,
                        "REFERENCE_MULTIPART_INVALID",
                        "Both multipart members must be files",
                    )
                request_media_type = (request_upload.content_type or "").split(";", 1)[0].strip().lower()
                if request_media_type != "application/json":
                    raise _error(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        "REFERENCE_REQUEST_MEDIA_TYPE_INVALID",
                        "request member must use application/json",
                    )
                request_bytes = await _read_bounded(
                    request_upload,
                    active_settings.max_request_bytes,
                    label="request",
                )
                try:
                    raw_request = json.loads(
                        request_bytes,
                        object_pairs_hook=_reject_duplicate_pairs,
                        parse_constant=_reject_non_json_constant,
                    )
                    request_document = RemoteAnalyzerRequest.model_validate(raw_request)
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError, ValidationError) as exc:
                    raise _error(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        "REFERENCE_REQUEST_INVALID",
                        "request member is not valid RemoteAnalyzerRequest JSON",
                    ) from exc
                idempotency_key = _validate_request_headers(request, request_document, active_settings)

                evidence_media_type = (evidence_upload.content_type or "").split(";", 1)[0].strip().lower()
                filename = Path(evidence_upload.filename or "").name
                extension = Path(filename).suffix.lower()
                canonical_media_type = CANONICAL_CONTENT_TYPES.get(extension)
                if (
                    evidence_media_type not in ALLOWED_MEDIA_TYPES
                    or canonical_media_type is None
                    or evidence_media_type != canonical_media_type
                    or request_document.evidence.content_type != canonical_media_type
                ):
                    raise _error(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        "REFERENCE_EVIDENCE_MEDIA_TYPE_INVALID",
                        "Evidence extension and media types must match the service allowlist",
                    )
                evidence_size, evidence_sha256, evidence_header = await _hash_evidence(
                    evidence_upload,
                    limit=active_settings.max_upload_bytes,
                )
                if evidence_size == 0 or not _signature_matches(extension, evidence_header):
                    raise _error(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        "REFERENCE_EVIDENCE_SIGNATURE_INVALID",
                        "Evidence magic bytes do not match its media type",
                    )
                if evidence_size != request_document.evidence.size_bytes or not hmac.compare_digest(
                    evidence_sha256,
                    request_document.evidence.sha256,
                ):
                    raise _error(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        "REFERENCE_EVIDENCE_DIGEST_MISMATCH",
                        "Evidence size or SHA-256 does not match request.json",
                    )
                fingerprint = sha256_bytes(
                    canonical_json_bytes(
                        {
                            "request": request_document.model_dump(mode="json"),
                            "evidence_sha256": evidence_sha256,
                        }
                    )
                )

                def produce() -> bytes:
                    return _build_response_bytes(
                        predictor=active_predictor,
                        settings=active_settings,
                        request_document=request_document,
                        evidence_stream=evidence_upload.file,
                    )

                try:
                    response_bytes, replayed = await run_in_threadpool(
                        active_cache.get_or_compute,
                        idempotency_key,
                        fingerprint,
                        produce,
                    )
                except IdempotencyConflict as exc:
                    raise _error(
                        status.HTTP_409_CONFLICT,
                        "REFERENCE_IDEMPOTENCY_CONFLICT",
                        "Idempotency-Key is already bound to a different request identity",
                    ) from exc
                return Response(
                    content=response_bytes,
                    media_type="application/json",
                    headers={
                        "Cache-Control": "no-store",
                        "X-Fengmou-Contract-Version": REMOTE_CONTRACT_VERSION,
                        "X-Idempotent-Replay": "true" if replayed else "false",
                        "X-Reference-Analyzer-Mode": "stub",
                    },
                )
        except HTTPException:
            raise
        except MultiPartException as exc:
            raise _error(
                status.HTTP_400_BAD_REQUEST,
                "REFERENCE_MULTIPART_INVALID",
                "Multipart body could not be parsed within the configured limits",
            ) from exc

    return service


app = create_reference_analyzer()


__all__ = [
    "BoundedIdempotencyCache",
    "IdempotencyConflict",
    "REFERENCE_STUB_ARTIFACT_RELATIVE_PATH",
    "REFERENCE_STUB_ARTIFACT_SHA256",
    "REFERENCE_STUB_LIMITATION",
    "ReferenceAnalyzerSettings",
    "ReferencePrediction",
    "ReferencePredictor",
    "StubReferencePredictor",
    "app",
    "create_reference_analyzer",
]
