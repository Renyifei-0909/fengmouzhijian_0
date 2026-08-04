from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.router import _iter_validated_evidence
from app.models import EvidenceAsset, VerificationJob
from app.services.storage import FileStorage, ValidatedStoredFile


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


@pytest.fixture()
def uploaded_evidence(
    client: TestClient,
    project_and_baseline: tuple[dict[str, Any], dict[str, Any]],
) -> tuple[str, bytes]:
    project, baseline = project_and_baseline
    response = client.post(
        "/api/v1/verifications",
        data={"project_id": project["id"], "baseline_id": baseline["id"], "analyzer": "stub"},
        files={"file": ("现场截图.png", PNG_BYTES, "image/png")},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    detail = client.get(f"/api/v1/verifications/{job_id}")
    assert detail.status_code == 200, detail.text
    return detail.json()["evidence"]["id"], PNG_BYTES


def _record(client: TestClient, evidence_id: str) -> tuple[Path, str]:
    with client.app.state.database.session_factory() as db:
        evidence = db.get(EvidenceAsset, evidence_id)
        assert evidence is not None
        return Path(evidence.storage_path), evidence.stored_name


def _assert_private_no_store(response) -> None:
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_complete_evidence_read_uses_allowlisted_media_and_integrity_headers(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
) -> None:
    evidence_id, content = uploaded_evidence
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"].strip('"') != ""
    assert "content-range" not in response.headers
    assert "content-disposition" not in response.headers
    _assert_private_no_store(response)

    cross_origin = client.get(
        f"/api/v1/evidence-assets/{evidence_id}/content",
        headers={"Origin": "http://testserver"},
    )
    exposed = {item.strip().lower() for item in cross_origin.headers["access-control-expose-headers"].split(",")}
    assert {"accept-ranges", "content-length", "content-range", "etag"} <= exposed


@pytest.mark.parametrize(
    ("range_header", "expected_start", "expected_end"),
    [
        ("bytes=0-0", 0, 0),
        ("bytes=1-3", 1, 3),
        ("bytes=8-", 8, len(PNG_BYTES) - 1),
        ("bytes=-5", len(PNG_BYTES) - 5, len(PNG_BYTES) - 1),
        ("bytes=-999", 0, len(PNG_BYTES) - 1),
        ("bytes=1-999", 1, len(PNG_BYTES) - 1),
    ],
)
def test_single_byte_ranges_are_exact(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    range_header: str,
    expected_start: int,
    expected_end: int,
) -> None:
    evidence_id, content = uploaded_evidence
    response = client.get(
        f"/api/v1/evidence-assets/{evidence_id}/content",
        headers={"Range": range_header},
    )
    assert response.status_code == 206
    assert response.content == content[expected_start : expected_end + 1]
    assert response.headers["content-range"] == f"bytes {expected_start}-{expected_end}/{len(content)}"
    assert response.headers["content-length"] == str(expected_end - expected_start + 1)
    _assert_private_no_store(response)


@pytest.mark.parametrize(
    "range_header",
    [
        f"bytes={len(PNG_BYTES)}-",
        f"bytes={len(PNG_BYTES) + 10}-",
        "bytes=-0",
        "bytes=",
        "bytes=4-2",
        "bytes=a-b",
        "bytes=+1-2",
        "items=0-1",
        "bytes=0-1,4-5",
        " bytes=0-1",
        "bytes =0-1",
        f"bytes={'9' * 200}-",
    ],
)
def test_malformed_multiple_and_unsatisfiable_ranges_are_bounded_416(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    range_header: str,
) -> None:
    evidence_id, content = uploaded_evidence
    response = client.get(
        f"/api/v1/evidence-assets/{evidence_id}/content",
        headers={"Range": range_header},
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(content)}"
    assert response.json()["detail"] in {
        "Only one well-formed bytes range is supported",
        "Requested byte range is not satisfiable",
    }
    assert content[:1] not in response.content
    _assert_private_no_store(response)


def test_authentication_precedes_existence_and_file_access(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id, _ = uploaded_evidence
    calls = 0
    original = FileStorage.validate_evidence_file

    def tracked(self: FileStorage, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, **kwargs)

    monkeypatch.setattr(FileStorage, "validate_evidence_file", tracked)
    for requested_id, headers, params in (
        (evidence_id, {"X-API-Key": ""}, None),
        (evidence_id, {"X-API-Key": "wrong"}, None),
        ("00000000-0000-0000-0000-000000000000", {"X-API-Key": ""}, None),
        (evidence_id, {"X-API-Key": ""}, {"api_key": "test-operator-key"}),
    ):
        response = client.get(
            f"/api/v1/evidence-assets/{requested_id}/content",
            headers=headers,
            params=params,
        )
        assert response.status_code == 401
        assert "test-operator-key" not in response.text
    assert calls == 0


@pytest.mark.parametrize("api_key", ["test-operator-key", "test-reviewer-key", "test-auditor-key"])
def test_all_configured_read_roles_can_read_evidence(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    api_key: str,
) -> None:
    evidence_id, content = uploaded_evidence
    response = client.get(
        f"/api/v1/evidence-assets/{evidence_id}/content",
        headers={"X-API-Key": api_key},
    )
    assert response.status_code == 200
    assert response.content == content


def test_unknown_and_missing_evidence_have_distinct_contract_statuses(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
) -> None:
    evidence_id, content = uploaded_evidence
    unknown = client.get("/api/v1/evidence-assets/00000000-0000-0000-0000-000000000000/content")
    assert unknown.status_code == 404
    _assert_private_no_store(unknown)

    path, _ = _record(client, evidence_id)
    path.unlink()
    missing = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert missing.status_code == 410
    assert missing.json() == {"detail": "Original evidence file is unavailable"}
    assert content not in missing.content
    assert str(path) not in missing.text
    _assert_private_no_store(missing)

    path.parent.rmdir()
    missing_directory = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert missing_directory.status_code == 410
    assert missing_directory.json() == {"detail": "Original evidence file is unavailable"}


@pytest.mark.parametrize(
    "mutation",
    ["same_size", "append", "truncate", "content_type", "size_metadata", "sha_metadata", "magic"],
)
def test_integrity_mutations_fail_closed_without_leaking_evidence(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    mutation: str,
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    if mutation == "same_size":
        changed = bytearray(content)
        changed[-1] ^= 0xFF
        path.write_bytes(changed)
    elif mutation == "append":
        path.write_bytes(content + b"appended")
    elif mutation == "truncate":
        path.write_bytes(content[:-1])
    elif mutation == "magic":
        changed = b"BADMAGIC" + content[8:]
        path.write_bytes(changed)
        with client.app.state.database.session_factory() as db:
            evidence = db.get(EvidenceAsset, evidence_id)
            assert evidence is not None
            evidence.sha256 = hashlib.sha256(changed).hexdigest()
            db.commit()
    elif mutation in {"content_type", "size_metadata", "sha_metadata"}:
        with client.app.state.database.session_factory() as db:
            evidence = db.get(EvidenceAsset, evidence_id)
            assert evidence is not None
            if mutation == "content_type":
                evidence.content_type = "text/html"
            elif mutation == "size_metadata":
                evidence.size_bytes = 0
            else:
                evidence.sha256 = "NOT-A-SHA256"
            db.commit()

    response = client.get(
        f"/api/v1/evidence-assets/{evidence_id}/content",
        headers={"Range": "bytes=0-0"},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "Original evidence failed integrity validation"}
    assert content not in response.content
    assert content[:1] not in response.content
    assert str(path) not in response.text
    _assert_private_no_store(response)


@pytest.mark.parametrize("escape_kind", ["absolute", "relative", "prefix", "cross_record", "root_member"])
def test_database_path_escape_and_cross_record_substitution_are_rejected(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    tmp_path: Path,
    escape_kind: str,
) -> None:
    evidence_id, content = uploaded_evidence
    path, stored_name = _record(client, evidence_id)
    outside = tmp_path / "outside.png"
    outside.write_bytes(content)
    replacement = outside
    if escape_kind == "relative":
        replacement = Path("../../outside.png")
    elif escape_kind == "prefix":
        replacement = client.app.state.storage.root / "evidence-evil" / stored_name
        replacement.parent.mkdir()
        replacement.write_bytes(content)
    elif escape_kind == "cross_record":
        replacement = path.with_name("other.png")
        replacement.write_bytes(content)
    elif escape_kind == "root_member":
        replacement = client.app.state.storage.report_dir / stored_name
        replacement.write_bytes(content)

    with client.app.state.database.session_factory() as db:
        evidence = db.get(EvidenceAsset, evidence_id)
        assert evidence is not None
        evidence.storage_path = str(replacement)
        db.commit()
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert response.status_code == 409
    assert response.json() == {"detail": "Original evidence failed integrity validation"}
    assert content not in response.content
    assert str(replacement) not in response.text


@pytest.mark.parametrize("stored_name", ["../outside.png", "/tmp/outside.png", "sub/file.png", "sub\\file.png"])
def test_untrusted_stored_name_cannot_select_a_path(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    stored_name: str,
) -> None:
    evidence_id, content = uploaded_evidence
    with client.app.state.database.session_factory() as db:
        evidence = db.get(EvidenceAsset, evidence_id)
        assert evidence is not None
        evidence.stored_name = stored_name
        db.commit()
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert response.status_code == 409
    assert content not in response.content


@pytest.mark.parametrize("link_inside", [False, True])
def test_symbolic_links_are_rejected_without_target_disclosure(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    tmp_path: Path,
    link_inside: bool,
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    target = (path.parent / "other.png") if link_inside else (tmp_path / "outside.png")
    target.write_bytes(content)
    path.unlink()
    path.symlink_to(target)
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert response.status_code == 409
    assert content not in response.content
    assert str(target) not in response.text


def test_directory_and_hard_link_are_rejected(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    tmp_path: Path,
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    path.unlink()
    path.mkdir()
    assert client.get(f"/api/v1/evidence-assets/{evidence_id}/content").status_code == 409
    path.rmdir()

    os.mkfifo(path)
    assert client.get(f"/api/v1/evidence-assets/{evidence_id}/content").status_code == 409
    path.unlink()

    target = tmp_path / "hard-linked.png"
    target.write_bytes(content)
    os.link(target, path)
    assert client.get(f"/api/v1/evidence-assets/{evidence_id}/content").status_code == 409


def test_path_swap_after_validation_cannot_change_streamed_bytes(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    replacement = path.with_name("replacement.png")
    replacement.write_bytes(b"\x89PNG\r\n\x1a\n" + b"replacement-secret")
    original = FileStorage.validate_evidence_file

    def swap_after_open(self: FileStorage, **kwargs):
        validated = original(self, **kwargs)
        path.unlink()
        replacement.replace(path)
        return validated

    monkeypatch.setattr(FileStorage, "validate_evidence_file", swap_after_open)
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")
    assert response.status_code == 200
    assert response.content == content
    assert b"replacement-secret" not in response.content


def test_validated_descriptor_is_closed_after_stream(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id, _ = uploaded_evidence
    captured_descriptor: list[int] = []
    original = FileStorage.validate_evidence_file

    def capture(self: FileStorage, **kwargs):
        validated = original(self, **kwargs)
        captured_descriptor.append(validated.fileno())
        return validated

    monkeypatch.setattr(FileStorage, "validate_evidence_file", capture)
    assert client.get(f"/api/v1/evidence-assets/{evidence_id}/content").status_code == 200
    assert len(captured_descriptor) == 1
    with pytest.raises(OSError):
        os.fstat(captured_descriptor[0])


def test_review_reuses_secure_storage_validation(
    client: TestClient,
    project_and_baseline: tuple[dict[str, Any], dict[str, Any]],
    uploaded_evidence: tuple[str, bytes],
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    outside = path.parent.parent.parent / "review-outside.png"
    outside.write_bytes(content)
    path.unlink()
    path.symlink_to(outside)
    with client.app.state.database.session_factory() as db:
        job = db.query(VerificationJob).filter(VerificationJob.evidence_id == evidence_id).one()
        job_id = job.id
    response = client.post(
        f"/api/v1/verifications/{job_id}/review",
        json={"decision": "approve", "reviewer": "审核员", "note": "must fail closed"},
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert response.status_code == 409
    assert outside.read_bytes() == content


def test_evidence_directory_symlink_is_rejected_before_member_open(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    evidence_directory = path.parent
    real_directory = evidence_directory.with_name("evidence-real")
    evidence_directory.rename(real_directory)
    evidence_directory.symlink_to(real_directory, target_is_directory=True)

    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")

    assert response.status_code == 409
    assert response.json() == {"detail": "Original evidence failed integrity validation"}
    assert content[:8] not in response.content
    assert str(real_directory) not in response.text
    _assert_private_no_store(response)


def test_symlink_swap_between_lstat_and_open_fails_closed(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    outside = tmp_path / "outside-secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nsecret-that-must-not-leak")
    original_open = os.open
    swapped = False

    def swap_then_open(candidate, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and Path(candidate) == path:
            swapped = True
            path.unlink()
            path.symlink_to(outside)
        return original_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr("app.services.storage.os.open", swap_then_open)
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")

    assert swapped is True
    assert response.status_code == 409
    assert response.json() == {"detail": "Original evidence failed integrity validation"}
    assert content[:8] not in response.content
    assert b"secret-that-must-not-leak" not in response.content
    assert str(outside) not in response.text
    _assert_private_no_store(response)


@pytest.mark.parametrize("mutation", ["disappear", "replace"])
def test_path_mutation_after_digest_fails_before_response(
    client: TestClient,
    uploaded_evidence: tuple[str, bytes],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    evidence_id, content = uploaded_evidence
    path, _ = _record(client, evidence_id)
    replacement = path.with_name("post-digest-replacement.png")
    replacement.write_bytes(b"\x89PNG\r\n\x1a\npost-digest-secret")
    original_lstat = Path.lstat
    candidate_lstat_calls = 0

    def mutate_before_final_lstat(candidate: Path):
        nonlocal candidate_lstat_calls
        if candidate == path:
            candidate_lstat_calls += 1
            if candidate_lstat_calls == 2:
                path.unlink()
                if mutation == "replace":
                    replacement.replace(path)
        return original_lstat(candidate)

    monkeypatch.setattr(Path, "lstat", mutate_before_final_lstat)
    response = client.get(f"/api/v1/evidence-assets/{evidence_id}/content")

    assert candidate_lstat_calls == 2
    assert response.status_code == (410 if mutation == "disappear" else 409)
    assert content[:8] not in response.content
    assert b"post-digest-secret" not in response.content
    assert str(path) not in response.text
    _assert_private_no_store(response)


def test_validated_iterator_detects_short_read_and_closes_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "short-read.png"
    content = b"\x89PNG\r\n\x1a\nshort"
    path.write_bytes(content)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    validated = ValidatedStoredFile(
        path=path,
        stat_result=path.stat(),
        content_type="image/png",
        descriptor=descriptor,
    )
    iterator = _iter_validated_evidence(
        validated,
        start=0,
        end=len(content) + 4,
        chunk_size=len(content) + 8,
    )

    assert next(iterator) == content
    with pytest.raises(RuntimeError, match="ended unexpectedly"):
        next(iterator)
    assert validated.descriptor is None
    with pytest.raises(ValueError, match="descriptor is closed"):
        validated.fileno()
    with pytest.raises(OSError):
        os.fstat(descriptor)
