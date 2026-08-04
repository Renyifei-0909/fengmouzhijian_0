from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.evaluation.controlled_bundle import (
    canonical_controlled_manifest_bytes,
    canonical_trust_store_bytes,
    controlled_bundle_id,
    controlled_core_result_sha256,
    controlled_manifest_signing_message,
    controlled_member_set_sha256,
    verify_controlled_local_evidence_bundle,
)
from app.evaluation.controlled_bundle_schemas import (
    CONTROLLED_ASSURANCE_LIMITATIONS,
    CONTROLLED_BUNDLE_MEMBER_PATHS,
    CONTROLLED_CORE_MEMBER_PATHS,
    ControlledEvidenceMember,
)
from app.evaluation.cli import main as evaluation_cli_main
from app.evaluation.errors import ContractError, IntegrityError
from app.evaluation.registry import (
    commit_holdout_exposure,
    finalize_holdout_attempt,
    get_holdout_attempt,
    reserve_holdout_attempt,
)


DATASET_SHA = "1" * 64
TRAINING_SHA = "2" * 64
MODEL_STATEMENT_SHA = "3" * 64
MODEL_ARTIFACT_SHA = "4" * 64
EVALUATOR_SHA = "5" * 64


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        + b"\n"
    )


def _reservation_request() -> dict[str, Any]:
    return {
        "schema_version": "evaluation.holdout-reservation.v0",
        "attempt_id": "controlled-attempt-001",
        "run_id": "controlled-run-001",
        "dataset_manifest_sha256": DATASET_SHA,
        "split": "gate_holdout",
        "policy_generation": 0,
        "model_artifact_sha256": MODEL_ARTIFACT_SHA,
        "formal_capability": {
            "schema_version": "evaluation.formal-capability.v0",
            "capability_id": "self-asserted-capability-001",
            "capability_digest": "6" * 64,
            "dataset_manifest_sha256": DATASET_SHA,
            "split": "gate_holdout",
            "policy_generation": 0,
            "actor": "self-asserted-capability-issuer",
            "scope": "formal_holdout_reservation",
        },
        "qa_approval": {
            "schema_version": "evaluation.qa-holdout-approval.v0",
            "approval_id": "self-asserted-approval-001",
            "approval_digest": "7" * 64,
            "approval_kind": "initial_release",
            "dataset_manifest_sha256": DATASET_SHA,
            "split": "gate_holdout",
            "policy_generation": 0,
            "reason": "Unsigned local controlled-bundle contract fixture",
            "actor": "self-asserted-qa",
            "predecessor_attempt_id": None,
        },
    }


def _descriptor(path: str, payload: bytes) -> ControlledEvidenceMember:
    return ControlledEvidenceMember(path=path, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))


def _write_member(root: Path, path: str, payload: bytes) -> None:
    destination = root.joinpath(*path.split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def _fixture(tmp_path: Path) -> dict[str, Any]:
    bundle = tmp_path / "controlled-bundle"
    for directory in ("inputs", "public", "registry", "results"):
        (bundle / directory).mkdir(parents=True, exist_ok=True)

    predictions = _json_bytes(
        {
            "schema_version": "evaluation.prediction.v0",
            "case_id": "case-controlled-001",
            "output": {"kind": "violation_single_label", "label": "helmet_compliant", "confidence": 0.75},
        }
    )
    prediction_sha = hashlib.sha256(predictions).hexdigest()
    run_plan = _json_bytes(
        {
            "schema_version": "evaluation.controlled-local-run-plan.v0",
            "run_id": "controlled-run-001",
            "attempt_id": "controlled-attempt-001",
            "mode": "controlled_local",
            "split": "gate_holdout",
            "dataset_manifest_sha256": DATASET_SHA,
            "training_data_manifest_sha256": TRAINING_SHA,
            "model_statement_sha256": MODEL_STATEMENT_SHA,
            "model_artifact_sha256": MODEL_ARTIFACT_SHA,
            "evaluator_source_sha256": EVALUATOR_SHA,
            "formal_requested": False,
            "compliance_claim_eligible": False,
        }
    )
    summary = _json_bytes(
        {
            "schema_version": "evaluation.controlled-local-run-summary.v0",
            "run_id": "controlled-run-001",
            "attempt_id": "controlled-attempt-001",
            "mode": "controlled_local",
            "split": "gate_holdout",
            "dataset_manifest_sha256": DATASET_SHA,
            "training_data_manifest_sha256": TRAINING_SHA,
            "model_statement_sha256": MODEL_STATEMENT_SHA,
            "model_artifact_sha256": MODEL_ARTIFACT_SHA,
            "evaluator_source_sha256": EVALUATOR_SHA,
            "predictions_sha256": prediction_sha,
            "predictions_size_bytes": len(predictions),
            "threshold_status": "failed",
            "execution_status": "completed",
            "formal_execution_completed": False,
            "compliance_claim_eligible": False,
        }
    )
    score = _json_bytes(
        {
            "schema_version": "evaluation.controlled-local-public-score.v0",
            "run_id": "controlled-run-001",
            "attempt_id": "controlled-attempt-001",
            "split": "gate_holdout",
            "dataset_manifest_sha256": DATASET_SHA,
            "model_artifact_sha256": MODEL_ARTIFACT_SHA,
            "predictions_sha256": prediction_sha,
            "predictions_size_bytes": len(predictions),
            "threshold_status": "failed",
            "formal_requested": False,
            "gate_status": "not_eligible",
            "score_recomputed": False,
            "private_label_records_included": False,
            "compliance_claim_eligible": False,
        }
    )
    core_payloads = {
        "inputs/run-plan.json": run_plan,
        "public/predictions.jsonl": predictions,
        "results/run-summary.json": summary,
        "results/score.json": score,
    }
    for path, payload in core_payloads.items():
        _write_member(bundle, path, payload)
    core_descriptors = [_descriptor(path, core_payloads[path]) for path in CONTROLLED_CORE_MEMBER_PATHS]
    core_sha = controlled_core_result_sha256(core_descriptors)

    registry_root = tmp_path / "private-registry"
    registry_root.mkdir(mode=0o700)
    registry = registry_root / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _reservation_request())
    commit_holdout_exposure(registry, attempt_id="controlled-attempt-001", actor="local-test-broker")
    finalize_holdout_attempt(
        registry,
        attempt_id="controlled-attempt-001",
        result_sha256=core_sha,
        actor="local-test-worker",
    )
    attempt = get_holdout_attempt(registry, attempt_id="controlled-attempt-001")
    attempt_payload = _json_bytes(attempt)
    _write_member(bundle, "registry/attempt.json", attempt_payload)

    payloads = {**core_payloads, "registry/attempt.json": attempt_payload}
    members = [_descriptor(path, payloads[path]) for path in CONTROLLED_BUNDLE_MEMBER_PATHS]
    member_set_sha = controlled_member_set_sha256(members)

    private_key = Ed25519PrivateKey.generate()
    raw_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    fingerprint = hashlib.sha256(raw_public_key).hexdigest()
    trust_store = {
        "schema_version": "evaluation.ed25519-trust-store.v0",
        "trust_store_id": "controlled-test-trust-store",
        "generation": 1,
        "keys": [
            {
                "key_id": "controlled-test-key",
                "algorithm": "ed25519",
                "public_key_encoding": "raw_base64",
                "public_key_base64": base64.b64encode(raw_public_key).decode("ascii"),
                "public_key_fingerprint_sha256": fingerprint,
                "roles": ["controlled_run_bundle_signer"],
                "status": "active",
            }
        ],
    }
    trust_bytes = canonical_trust_store_bytes(trust_store)
    trust_path = tmp_path / "trust-store.json"
    trust_path.write_bytes(trust_bytes)

    manifest = {
        "schema_version": "evaluation.controlled-local-evidence-manifest.v0",
        "bundle_kind": "controlled_local_run_evidence",
        "fixed_tree_version": "v0",
        "bundle_id": controlled_bundle_id(
            registry_instance_id=attempt["registry_instance_id"],
            attempt_id=attempt["attempt_id"],
            run_id=attempt["run_id"],
            core_result_sha256=core_sha,
        ),
        "run_id": attempt["run_id"],
        "attempt_id": attempt["attempt_id"],
        "mode": "controlled_local",
        "execution_boundary": "single_host_local_registry",
        "split": "gate_holdout",
        "dataset_manifest_sha256": DATASET_SHA,
        "training_data_manifest_sha256": TRAINING_SHA,
        "model_statement_sha256": MODEL_STATEMENT_SHA,
        "model_artifact_sha256": MODEL_ARTIFACT_SHA,
        "evaluator_source_sha256": EVALUATOR_SHA,
        "core_result_commitment": {
            "profile": "evaluation.controlled-run-core-member-set.v0",
            "sha256": core_sha,
        },
        "registry_binding": {
            "registry_schema_version": "evaluation.holdout-registry.v0",
            "registry_instance_id": attempt["registry_instance_id"],
            "snapshot_path": "registry/attempt.json",
            "snapshot_sha256": hashlib.sha256(attempt_payload).hexdigest(),
            "state": "consumed",
            "consumption_key": attempt["consumption_key"],
            "result_sha256": core_sha,
            "result_commitment_profile": "evaluation.controlled-run-core-member-set.v0",
            "formal_capability_digest": attempt["formal_capability_digest"],
            "qa_approval_digest": attempt["qa_approval_digest"],
            "authorization_authenticity": "self_asserted_unsigned",
            "formal_execution_completed": False,
            "compliance_claim_eligible": False,
        },
        "signing": {
            "algorithm": "ed25519",
            "signature_path": "bundle-manifest.ed25519",
            "signature_encoding": "raw_64_bytes",
            "message_profile": "evaluation.controlled-local-manifest-signature.v0",
            "manifest_canonicalization": "evaluation.canonical-json.v0",
            "key_id": "controlled-test-key",
            "public_key_fingerprint_sha256": fingerprint,
            "required_key_role": "controlled_run_bundle_signer",
            "trust_source_required": "external",
            "time_assurance": "not_provided",
        },
        "verification_scope": "integrity_origin_and_local_registry_binding_only",
        "formal_execution_completed": False,
        "gate_status": "not_eligible",
        "compliance_claim_eligible": False,
        "isolation_status": "unverified",
        "private_label_records_included": False,
        "raw_logs_included": False,
        "score_recomputed": False,
        "assurance_limitations": list(CONTROLLED_ASSURANCE_LIMITATIONS),
        "member_set_sha256": member_set_sha,
        "members": [member.model_dump(mode="json") for member in members],
    }
    manifest_bytes = canonical_controlled_manifest_bytes(manifest)
    (bundle / "bundle-manifest.json").write_bytes(manifest_bytes)
    signature = private_key.sign(controlled_manifest_signing_message(manifest_bytes))
    (bundle / "bundle-manifest.ed25519").write_bytes(signature)
    return {
        "bundle": bundle,
        "trust_path": trust_path,
        "trust_sha": hashlib.sha256(trust_bytes).hexdigest(),
        "manifest_sha": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest": manifest,
        "private_key": private_key,
    }


def _verify(fixture: dict[str, Any]) -> dict[str, Any]:
    return verify_controlled_local_evidence_bundle(
        fixture["bundle"],
        fixture["trust_path"],
        expected_trust_store_sha256=fixture["trust_sha"],
        expected_manifest_sha256=fixture["manifest_sha"],
        expected_run_id="controlled-run-001",
        expected_attempt_id="controlled-attempt-001",
        expected_dataset_manifest_sha256=DATASET_SHA,
    )


def _rewrite_and_resign(fixture: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> None:
    manifest = json.loads(json.dumps(fixture["manifest"]))
    mutate(manifest)
    manifest_bytes = _json_bytes(manifest)
    fixture["manifest_sha"] = hashlib.sha256(manifest_bytes).hexdigest()
    (fixture["bundle"] / "bundle-manifest.json").write_bytes(manifest_bytes)
    (fixture["bundle"] / "bundle-manifest.ed25519").write_bytes(
        fixture["private_key"].sign(controlled_manifest_signing_message(manifest_bytes))
    )


def test_controlled_bundle_verifies_signature_members_and_registry_binding(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _verify(fixture)

    assert result["verdict"] == "valid_nonformal_signed_evidence"
    assert result["signature_status"] == "valid"
    assert result["signer_trust_status"] == "matched_externally_pinned_trust_store"
    assert result["registry_binding_status"] == "local_snapshot_consistent"
    assert result["authorization_authenticity"] == "self_asserted_unsigned"
    assert result["formal_execution_completed"] is False
    assert result["compliance_claim_eligible"] is False
    assert result["replay_status"] == "not_checked"
    assert result["prediction_count"] == 1
    assert result["threshold_status"] == "failed"


def test_controlled_bundle_rejects_member_tampering_after_signature(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    target = fixture["bundle"] / "public" / "predictions.jsonl"
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(IntegrityError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_MEMBER_IDENTITY_MISMATCH"


def test_controlled_bundle_rejects_manifest_signature_tampering(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    signature_path = fixture["bundle"] / "bundle-manifest.ed25519"
    signature = bytearray(signature_path.read_bytes())
    signature[0] ^= 1
    signature_path.write_bytes(signature)

    with pytest.raises(IntegrityError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_SIGNATURE_INVALID"


def test_controlled_bundle_requires_canonical_manifest_even_when_resigned(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest_bytes = json.dumps(fixture["manifest"], ensure_ascii=False, indent=2).encode() + b"\n"
    fixture["manifest_sha"] = hashlib.sha256(manifest_bytes).hexdigest()
    (fixture["bundle"] / "bundle-manifest.json").write_bytes(manifest_bytes)
    (fixture["bundle"] / "bundle-manifest.ed25519").write_bytes(
        fixture["private_key"].sign(controlled_manifest_signing_message(manifest_bytes))
    )

    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_MANIFEST_NOT_CANONICAL"


def test_controlled_bundle_rejects_unpinned_or_revoked_trust_store(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(IntegrityError) as captured:
        verify_controlled_local_evidence_bundle(
            fixture["bundle"],
            fixture["trust_path"],
            expected_trust_store_sha256="0" * 64,
        )
    assert captured.value.code == "EVAL_CONTROLLED_TRUST_STORE_IDENTITY_MISMATCH"

    trust_store = json.loads(fixture["trust_path"].read_text())
    trust_store["keys"][0]["status"] = "revoked"
    revoked_bytes = canonical_trust_store_bytes(trust_store)
    fixture["trust_path"].write_bytes(revoked_bytes)
    fixture["trust_sha"] = hashlib.sha256(revoked_bytes).hexdigest()
    with pytest.raises(IntegrityError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_SIGNER_REVOKED"


def test_controlled_bundle_rejects_formal_or_compliance_upgrade_even_if_resigned(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_and_resign(fixture, lambda manifest: manifest.update(formal_execution_completed=True))

    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_SCHEMA_INVALID"


def test_controlled_bundle_rejects_extra_tree_entries_and_bad_signature_size(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture["bundle"] / "unexpected.txt").write_text("forbidden", encoding="utf-8")
    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_BUNDLE_TREE_INVALID"
    (fixture["bundle"] / "unexpected.txt").unlink()
    (fixture["bundle"] / "bundle-manifest.ed25519").write_bytes(b"x" * 63)
    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_SIGNATURE_SIZE_INVALID"


def test_controlled_bundle_rejects_external_run_identity_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(IntegrityError) as captured:
        verify_controlled_local_evidence_bundle(
            fixture["bundle"],
            fixture["trust_path"],
            expected_trust_store_sha256=fixture["trust_sha"],
            expected_run_id="different-run",
        )
    assert captured.value.code == "EVAL_CONTROLLED_RUN_ID_MISMATCH"


def test_controlled_bundle_cli_emits_one_machine_readable_verification(tmp_path: Path, capsys) -> None:
    fixture = _fixture(tmp_path)
    exit_code = evaluation_cli_main(
        [
            "verify-controlled-bundle",
            "--bundle",
            str(fixture["bundle"]),
            "--trust-store",
            str(fixture["trust_path"]),
            "--expected-trust-store-sha256",
            fixture["trust_sha"],
            "--expected-manifest-sha256",
            fixture["manifest_sha"],
            "--expected-run-id",
            "controlled-run-001",
            "--expected-attempt-id",
            "controlled-attempt-001",
            "--expected-dataset-manifest-sha256",
            DATASET_SHA,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["signature_status"] == "valid"
    assert payload["formal_execution_completed"] is False
    assert payload["compliance_claim_eligible"] is False


def test_controlled_signature_known_vector_is_deterministic() -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    message = b"FENGMOU\x00controlled-test-vector-v0\x00"
    signature = private_key.sign(message)
    raw_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    assert raw_public_key.hex() == "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
    assert signature.hex() == (
        "247bfad2c9eb1977515b607c9d741bde8f19e1119c07f64fab6a39276b6570dd"
        "97e0acf2e06cc884075ad7f9db9423f22da17b0f3f6732e79844dd82943b2304"
    )
    private_key.public_key().verify(signature, message)


def test_controlled_contract_helpers_reject_ambiguous_inputs(tmp_path: Path) -> None:
    with pytest.raises(ContractError) as captured:
        canonical_controlled_manifest_bytes({"schema_version": "wrong"})
    assert captured.value.code == "EVAL_CONTROLLED_MANIFEST_INVALID"
    with pytest.raises(ContractError) as captured:
        canonical_trust_store_bytes({"schema_version": "wrong"})
    assert captured.value.code == "EVAL_CONTROLLED_TRUST_STORE_INVALID"
    with pytest.raises(ContractError):
        controlled_manifest_signing_message(b"")
    member = ControlledEvidenceMember(path="inputs/run-plan.json", sha256="0" * 64, size_bytes=1)
    with pytest.raises(ContractError):
        controlled_core_result_sha256([member])
    with pytest.raises(ContractError):
        controlled_member_set_sha256([member])
    with pytest.raises(ContractError) as captured:
        verify_controlled_local_evidence_bundle(
            tmp_path / "missing",
            tmp_path / "missing-trust.json",
            expected_trust_store_sha256="not-a-digest",
        )
    assert captured.value.code == "EVAL_CONTROLLED_BUNDLE_ROOT_INVALID"


def test_controlled_bundle_rejects_invalid_external_digest_format(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(ContractError) as captured:
        verify_controlled_local_evidence_bundle(
            fixture["bundle"],
            fixture["trust_path"],
            expected_trust_store_sha256="not-a-digest",
        )
    assert captured.value.code == "EVAL_CONTROLLED_EXPECTED_DIGEST_INVALID"


def test_controlled_bundle_rejects_external_manifest_pin_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(IntegrityError) as captured:
        verify_controlled_local_evidence_bundle(
            fixture["bundle"],
            fixture["trust_path"],
            expected_trust_store_sha256=fixture["trust_sha"],
            expected_manifest_sha256="0" * 64,
        )
    assert captured.value.code == "EVAL_CONTROLLED_MANIFEST_IDENTITY_MISMATCH"


def test_controlled_bundle_rejects_noncanonical_or_unknown_trust_key(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    trust_store = json.loads(fixture["trust_path"].read_text())
    pretty = json.dumps(trust_store, indent=2).encode() + b"\n"
    fixture["trust_path"].write_bytes(pretty)
    fixture["trust_sha"] = hashlib.sha256(pretty).hexdigest()
    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_TRUST_STORE_NOT_CANONICAL"

    trust_store["keys"][0]["key_id"] = "different-key-id"
    canonical = canonical_trust_store_bytes(trust_store)
    fixture["trust_path"].write_bytes(canonical)
    fixture["trust_sha"] = hashlib.sha256(canonical).hexdigest()
    with pytest.raises(IntegrityError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_SIGNER_UNKNOWN"


def test_controlled_bundle_rejects_trust_key_fingerprint_substitution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    trust_store = json.loads(fixture["trust_path"].read_text())
    replacement = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    trust_store["keys"][0]["public_key_base64"] = base64.b64encode(replacement).decode("ascii")
    trust_store["keys"][0]["public_key_fingerprint_sha256"] = hashlib.sha256(replacement).hexdigest()
    canonical = canonical_trust_store_bytes(trust_store)
    fixture["trust_path"].write_bytes(canonical)
    fixture["trust_sha"] = hashlib.sha256(canonical).hexdigest()

    with pytest.raises(IntegrityError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_SIGNER_FINGERPRINT_MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda manifest: manifest.update(member_set_sha256="0" * 64), "EVAL_CONTROLLED_MEMBER_SET_MISMATCH"),
        (
            lambda manifest: (
                manifest["core_result_commitment"].update(sha256="0" * 64),
                manifest["registry_binding"].update(result_sha256="0" * 64),
            ),
            "EVAL_CONTROLLED_CORE_COMMITMENT_MISMATCH",
        ),
        (lambda manifest: manifest.update(bundle_id="crb0:" + "0" * 64), "EVAL_CONTROLLED_BUNDLE_ID_MISMATCH"),
    ],
)
def test_controlled_bundle_rejects_resigned_commitment_confusion(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path)
    _rewrite_and_resign(fixture, mutation)

    with pytest.raises(IntegrityError) as captured:
        _verify(fixture)
    assert captured.value.code == expected_code


def test_controlled_bundle_rejects_expected_attempt_and_dataset_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(IntegrityError) as captured:
        verify_controlled_local_evidence_bundle(
            fixture["bundle"],
            fixture["trust_path"],
            expected_trust_store_sha256=fixture["trust_sha"],
            expected_attempt_id="different-attempt",
        )
    assert captured.value.code == "EVAL_CONTROLLED_ATTEMPT_ID_MISMATCH"
    with pytest.raises(IntegrityError) as captured:
        verify_controlled_local_evidence_bundle(
            fixture["bundle"],
            fixture["trust_path"],
            expected_trust_store_sha256=fixture["trust_sha"],
            expected_dataset_manifest_sha256="8" * 64,
        )
    assert captured.value.code == "EVAL_CONTROLLED_DATASET_IDENTITY_MISMATCH"


def test_controlled_bundle_rejects_non_directory_root_and_oversized_signature(tmp_path: Path) -> None:
    root_file = tmp_path / "not-a-directory"
    root_file.write_text("x", encoding="utf-8")
    with pytest.raises(ContractError) as captured:
        verify_controlled_local_evidence_bundle(
            root_file,
            tmp_path / "unused.json",
            expected_trust_store_sha256="0" * 64,
        )
    assert captured.value.code == "EVAL_CONTROLLED_BUNDLE_ROOT_INVALID"

    fixture = _fixture(tmp_path)
    (fixture["bundle"] / "bundle-manifest.ed25519").write_bytes(b"x" * 65)
    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_FILE_TOO_LARGE"


def test_controlled_bundle_rejects_replaced_or_incomplete_fixed_directories(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    results = fixture["bundle"] / "results"
    (results / "score.json").unlink()
    with pytest.raises(ContractError) as captured:
        _verify(fixture)
    assert captured.value.code == "EVAL_CONTROLLED_BUNDLE_TREE_INVALID"


def test_controlled_schema_rejects_unsafe_paths_and_malformed_trust_keys() -> None:
    with pytest.raises(ValueError):
        ControlledEvidenceMember(path="../escape.json", sha256="0" * 64, size_bytes=1)

    base_key = {
        "key_id": "bad-key",
        "algorithm": "ed25519",
        "public_key_encoding": "raw_base64",
        "public_key_base64": "!" * 44,
        "public_key_fingerprint_sha256": "0" * 64,
        "roles": ["controlled_run_bundle_signer"],
        "status": "active",
    }
    with pytest.raises(ContractError):
        canonical_trust_store_bytes(
            {
                "schema_version": "evaluation.ed25519-trust-store.v0",
                "trust_store_id": "bad-store",
                "generation": 1,
                "keys": [base_key],
            }
        )

    short_key = dict(base_key)
    short_raw = b"x" * 31
    short_key["public_key_base64"] = base64.b64encode(short_raw).decode("ascii")
    short_key["public_key_fingerprint_sha256"] = hashlib.sha256(short_raw).hexdigest()
    with pytest.raises(ContractError):
        canonical_trust_store_bytes(
            {
                "schema_version": "evaluation.ed25519-trust-store.v0",
                "trust_store_id": "short-store",
                "generation": 1,
                "keys": [short_key],
            }
        )

    wrong_fingerprint = dict(base_key)
    raw = b"x" * 32
    wrong_fingerprint["public_key_base64"] = base64.b64encode(raw).decode("ascii")
    wrong_fingerprint["public_key_fingerprint_sha256"] = "0" * 64
    with pytest.raises(ContractError):
        canonical_trust_store_bytes(
            {
                "schema_version": "evaluation.ed25519-trust-store.v0",
                "trust_store_id": "wrong-fingerprint-store",
                "generation": 1,
                "keys": [wrong_fingerprint],
            }
        )
