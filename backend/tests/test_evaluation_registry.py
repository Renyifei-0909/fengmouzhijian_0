from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import sqlite3
from typing import Any

import pytest

import app.evaluation.registry as registry_module
from app.evaluation.errors import ContractError, EvaluationError, ExecutionError, IntegrityError
from app.evaluation.registry import (
    commit_holdout_exposure,
    finalize_holdout_attempt,
    get_holdout_attempt,
    list_holdout_attempts,
    mark_holdout_incident,
    reserve_holdout_attempt,
)
from app.evaluation.registry_schemas import HoldoutReservationRequest, QAHoldoutApproval


DATASET_SHA = "1" * 64
MODEL_SHA = "2" * 64


def _request(
    suffix: str,
    *,
    model_sha256: str = MODEL_SHA,
    generation: int = 0,
    approval_kind: str = "initial_release",
    predecessor_attempt_id: str | None = None,
    split: str = "final_holdout",
) -> dict[str, Any]:
    return {
        "schema_version": "evaluation.holdout-reservation.v0",
        "attempt_id": f"attempt-{suffix}",
        "run_id": f"run-{suffix}",
        "dataset_manifest_sha256": DATASET_SHA,
        "split": split,
        "policy_generation": generation,
        "model_artifact_sha256": model_sha256,
        "formal_capability": {
            "schema_version": "evaluation.formal-capability.v0",
            "capability_id": f"capability-{suffix}",
            "capability_digest": hashlib.sha256(f"capability:{suffix}".encode()).hexdigest(),
            "dataset_manifest_sha256": DATASET_SHA,
            "split": split,
            "policy_generation": generation,
            "actor": "qa-capability-issuer",
            "scope": "formal_holdout_reservation",
        },
        "qa_approval": {
            "schema_version": "evaluation.qa-holdout-approval.v0",
            "approval_id": f"approval-{suffix}",
            "approval_digest": hashlib.sha256(f"approval:{suffix}".encode()).hexdigest(),
            "approval_kind": approval_kind,
            "dataset_manifest_sha256": DATASET_SHA,
            "split": split,
            "policy_generation": generation,
            "reason": "QA authorizes this one-shot reservation for test audit",
            "actor": "qa-reviewer-independent",
            "predecessor_attempt_id": predecessor_attempt_id,
        },
    }


def _concurrent_worker(registry: str, suffix: str, start: Any, queue: Any) -> None:
    start.wait()
    try:
        receipt = reserve_holdout_attempt(registry, _request(suffix, model_sha256=suffix[0] * 64))
        queue.put(("ok", receipt["attempt_id"]))
    except EvaluationError as exc:
        queue.put(("error", exc.code))


def _crash_after_reservation(registry: str) -> None:
    reserve_holdout_attempt(registry, _request("crash"))
    os._exit(0)


def _crash_after_exposure_commit(registry: str) -> None:
    reserve_holdout_attempt(registry, _request("exposure-crash"))
    commit_holdout_exposure(registry, attempt_id="attempt-exposure-crash", actor="broker")
    os._exit(0)


def test_reservation_is_persisted_fail_closed_and_key_is_domain_separated(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    receipt = reserve_holdout_attempt(registry, _request("first"))

    assert receipt["state"] == "reserved"
    assert receipt["reservation_persisted"] is True
    assert receipt["model_identity_part_of_consumption_key"] is False
    assert receipt["authorization_authenticity"] == "self_asserted_unsigned"
    assert receipt["formal_execution_completed"] is False
    assert receipt["compliance_claim_eligible"] is False
    assert receipt["registry_instance_id"].startswith("registry-")
    key = receipt["consumption_key"]
    expected = hashlib.sha256(
        b"evaluation.holdout-consumption-key.v0\n"
        + b'{"dataset_manifest_sha256":"'
        + DATASET_SHA.encode()
        + b'","policy_generation":0,"split":"final_holdout"}'
    ).hexdigest()
    assert key["key_sha256"] == expected
    record = get_holdout_attempt(registry, attempt_id="attempt-first")
    assert record["state"] == "reserved"
    assert record["registry_instance_id"] == receipt["registry_instance_id"]
    assert record["result_commitment_profile"] is None
    assert record["authorization_authenticity"] == "self_asserted_unsigned"
    assert record["formal_execution_completed"] is False
    assert record["compliance_claim_eligible"] is False
    assert list_holdout_attempts(registry)[0]["authorization_authenticity"] == "self_asserted_unsigned"
    with sqlite3.connect(registry) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"


def test_independent_registry_files_receive_distinct_persistent_instance_ids(tmp_path: Path) -> None:
    first = tmp_path / "first.sqlite3"
    second_root = tmp_path / "second-root"
    second_root.mkdir(mode=0o700)
    second = second_root / "second.sqlite3"

    first_receipt = reserve_holdout_attempt(first, _request("instance-first"))
    second_request = _request("instance-second")
    second_request["dataset_manifest_sha256"] = "8" * 64
    second_request["formal_capability"]["dataset_manifest_sha256"] = "8" * 64
    second_request["qa_approval"]["dataset_manifest_sha256"] = "8" * 64
    second_receipt = reserve_holdout_attempt(second, second_request)

    assert first_receipt["registry_instance_id"] != second_receipt["registry_instance_id"]
    assert (
        get_holdout_attempt(first, attempt_id="attempt-instance-first")["registry_instance_id"]
        == first_receipt["registry_instance_id"]
    )


def test_path_identity_swap_is_rejected_before_registry_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "holdout.sqlite3"
    attacker = tmp_path / "attacker.sqlite3"
    real_connect = registry_module.sqlite3.connect
    with real_connect(attacker) as connection:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker(value) VALUES ('unchanged')")

    swapped = False

    def swap_before_connect(database: str, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal swapped
        if not swapped:
            swapped = True
            Path(database).unlink()
            Path(database).symlink_to(attacker)
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(registry_module.sqlite3, "connect", swap_before_connect)
    with pytest.raises(ContractError) as captured:
        reserve_holdout_attempt(registry, _request("path-race"))

    assert captured.value.code == "EVAL_HOLDOUT_REGISTRY_PATH_INVALID"
    with real_connect(attacker) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "unchanged"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "holdout_attempts" not in tables


def test_failed_parent_fsync_is_structured_and_retried_before_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "holdout.sqlite3"
    real_fsync_parent = registry_module._fsync_parent
    calls = 0

    def fail_once(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected directory fsync failure")
        real_fsync_parent(path)

    monkeypatch.setattr(registry_module, "_fsync_parent", fail_once)
    with pytest.raises(ExecutionError) as captured:
        reserve_holdout_attempt(registry, _request("fsync-first"))

    assert captured.value.code == "EVAL_HOLDOUT_REGISTRY_PERSISTENCE_FAILED"
    assert registry.exists()
    assert registry.stat().st_size == 0

    receipt = reserve_holdout_attempt(registry, _request("fsync-second"))
    assert calls >= 2
    assert receipt["reservation_persisted"] is True
    assert get_holdout_attempt(registry, attempt_id="attempt-fsync-second")["state"] == "reserved"


def test_read_only_typo_path_is_not_created(tmp_path: Path) -> None:
    typo = tmp_path / "typo-holdout.sqlite3"

    with pytest.raises(ContractError) as captured:
        list_holdout_attempts(typo)

    assert captured.value.code == "EVAL_HOLDOUT_REGISTRY_NOT_FOUND"
    assert not os.path.lexists(typo)


def test_same_consumption_key_cannot_be_bypassed_by_changing_model(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("model-a", model_sha256="a" * 64))

    with pytest.raises(IntegrityError) as captured:
        reserve_holdout_attempt(registry, _request("model-b", model_sha256="b" * 64))

    assert captured.value.code == "EVAL_HOLDOUT_ALREADY_CLAIMED"
    assert captured.value.details["model_identity_ignored_for_key"] is True


@pytest.mark.parametrize("round_index", range(3))
def test_concurrent_processes_can_reserve_a_key_only_once(tmp_path: Path, round_index: int) -> None:
    registry = tmp_path / "holdout.sqlite3"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    queue = context.Queue()
    suffixes = [f"{index:x}-worker" for index in range(1, 7)]
    processes = [
        context.Process(target=_concurrent_worker, args=(str(registry), suffix, start, queue))
        for suffix in suffixes
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    results = [queue.get(timeout=5) for _ in processes]
    assert sum(status == "ok" for status, _ in results) == 1
    assert all(value == "EVAL_HOLDOUT_ALREADY_CLAIMED" for status, value in results if status == "error")
    assert len(list_holdout_attempts(registry)) == 1


def test_process_crash_does_not_restore_consumability(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_after_reservation, args=(str(registry),))
    process.start()
    process.join(timeout=20)
    assert process.exitcode == 0

    assert get_holdout_attempt(registry, attempt_id="attempt-crash")["state"] == "reserved"
    with pytest.raises(IntegrityError) as captured:
        reserve_holdout_attempt(registry, _request("after-crash", model_sha256="f" * 64))
    assert captured.value.code == "EVAL_HOLDOUT_ALREADY_CLAIMED"


def test_crash_after_exposure_commit_remains_burned(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_after_exposure_commit, args=(str(registry),))
    process.start()
    process.join(timeout=20)
    assert process.exitcode == 0

    record = get_holdout_attempt(registry, attempt_id="attempt-exposure-crash")
    assert record["state"] == "exposure_committed"
    assert record["exposure_committed_at"] is not None
    with pytest.raises(IntegrityError) as captured:
        reserve_holdout_attempt(registry, _request("exposure-reclaim", model_sha256="e" * 64))
    assert captured.value.code == "EVAL_HOLDOUT_ALREADY_CLAIMED"


def test_incident_approval_creates_next_generation_without_erasing_history(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("original", split="gate_holdout"))

    retry = _request(
        "retry",
        generation=1,
        approval_kind="incident_retry",
        predecessor_attempt_id="attempt-original",
        model_sha256="3" * 64,
        split="gate_holdout",
    )
    mark_holdout_incident(
        registry,
        attempt_id="attempt-original",
        incident_approval=retry["qa_approval"],
    )
    receipt = reserve_holdout_attempt(registry, retry)

    assert receipt["consumption_key"]["policy_generation"] == 1
    records = list_holdout_attempts(registry)
    assert len(records) == 2
    assert records[0]["attempt_id"] == "attempt-original"
    assert records[0]["state"] == "incident_review"
    assert records[0]["result_sha256"] is None
    assert records[0]["exposure_committed_at"] is None
    assert records[1]["attempt_id"] == "attempt-retry"
    assert records[1]["state"] == "reserved"
    assert records[1]["predecessor_attempt_id"] == "attempt-original"


def test_exposed_gate_can_be_incident_locked_but_not_retried(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("exposed-gate", split="gate_holdout"))
    commit_holdout_exposure(registry, attempt_id="attempt-exposed-gate", actor="broker")
    retry = _request(
        "exposed-retry",
        generation=1,
        approval_kind="incident_retry",
        predecessor_attempt_id="attempt-exposed-gate",
        split="gate_holdout",
    )
    with pytest.raises(IntegrityError) as captured:
        mark_holdout_incident(
            registry,
            attempt_id="attempt-exposed-gate",
            incident_approval=retry["qa_approval"],
        )
    assert captured.value.code == "EVAL_HOLDOUT_EXPOSED_RERUN_FORBIDDEN"

    lock = _request(
        "exposed-lock",
        generation=0,
        approval_kind="incident_lock",
        predecessor_attempt_id="attempt-exposed-gate",
        split="gate_holdout",
    )["qa_approval"]
    receipt = mark_holdout_incident(
        registry,
        attempt_id="attempt-exposed-gate",
        incident_approval=lock,
    )
    assert receipt["retry_authorized"] is False
    assert get_holdout_attempt(registry, attempt_id="attempt-exposed-gate")["state"] == "incident_review"


def test_new_generation_without_incident_approval_is_rejected(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("first", split="gate_holdout"))

    with pytest.raises(IntegrityError) as captured:
        reserve_holdout_attempt(registry, _request("unapproved", generation=1, split="gate_holdout"))

    assert captured.value.code == "EVAL_HOLDOUT_INCIDENT_APPROVAL_REQUIRED"
    assert len(list_holdout_attempts(registry)) == 1


def test_exposure_must_be_committed_before_result_finalization(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("two-phase"))

    with pytest.raises(IntegrityError):
        finalize_holdout_attempt(
            registry,
            attempt_id="attempt-two-phase",
            result_sha256="9" * 64,
            actor="worker",
        )

    commit = commit_holdout_exposure(registry, attempt_id="attempt-two-phase", actor="holdout-broker")
    assert commit["exposure_commit_persisted"] is True
    assert commit["local_exposure_state_persisted"] is True
    assert commit["authorization_authenticity"] == "self_asserted_unsigned"
    assert commit["trusted_broker_release_authorized"] is False
    finalized = finalize_holdout_attempt(
        registry,
        attempt_id="attempt-two-phase",
        result_sha256="9" * 64,
        actor="controlled-worker",
    )
    assert finalized["attempt"]["state"] == "consumed"
    assert finalized["attempt"]["result_sha256"] == "9" * 64
    assert (
        finalized["attempt"]["result_commitment_profile"]
        == "evaluation.controlled-run-core-member-set.v0"
    )


def test_consumed_attempt_cannot_be_reclassified_as_incident(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("consumed-gate", split="gate_holdout"))
    commit_holdout_exposure(registry, attempt_id="attempt-consumed-gate", actor="broker")
    finalize_holdout_attempt(
        registry,
        attempt_id="attempt-consumed-gate",
        result_sha256="9" * 64,
        actor="worker",
    )
    retry = _request(
        "never",
        generation=1,
        approval_kind="incident_retry",
        predecessor_attempt_id="attempt-consumed-gate",
        split="gate_holdout",
    )

    with pytest.raises(IntegrityError) as captured:
        mark_holdout_incident(
            registry,
            attempt_id="attempt-consumed-gate",
            incident_approval=retry["qa_approval"],
        )
    assert captured.value.code == "EVAL_HOLDOUT_INCIDENT_STATE_INVALID"


def test_final_holdout_can_be_incident_locked_but_never_rerun(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("final"))
    commit_holdout_exposure(registry, attempt_id="attempt-final", actor="broker")
    lock = _request(
        "final-lock",
        generation=0,
        approval_kind="incident_lock",
        predecessor_attempt_id="attempt-final",
    )["qa_approval"]
    result = mark_holdout_incident(
        registry,
        attempt_id="attempt-final",
        incident_approval=lock,
    )
    assert result["retry_authorized"] is False

    retry = _request(
        "final-retry",
        generation=1,
        approval_kind="incident_retry",
        predecessor_attempt_id="attempt-final",
    )
    with pytest.raises(IntegrityError) as captured:
        reserve_holdout_attempt(registry, retry)
    assert captured.value.code == "EVAL_FINAL_RERUN_FORBIDDEN"


@pytest.mark.parametrize("missing", ["formal_capability", "qa_approval"])
def test_capability_and_qa_approval_are_mandatory(tmp_path: Path, missing: str) -> None:
    request = _request(missing)
    request.pop(missing)

    with pytest.raises(ContractError) as captured:
        reserve_holdout_attempt(tmp_path / "holdout.sqlite3", request)

    expected = {
        "formal_capability": "EVAL_HOLDOUT_FORMAL_CAPABILITY_REQUIRED",
        "qa_approval": "EVAL_HOLDOUT_QA_APPROVAL_REQUIRED",
    }
    assert captured.value.code == expected[missing]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda request: request.update(split="validation"), "EVAL_HOLDOUT_REQUEST_INVALID"),
        (
            lambda request: request["formal_capability"].update(dataset_manifest_sha256="0" * 64),
            "EVAL_HOLDOUT_REQUEST_INVALID",
        ),
        (
            lambda request: request.update(model_artifact_sha256="not-a-hash"),
            "EVAL_HOLDOUT_REQUEST_INVALID",
        ),
    ],
)
def test_invalid_or_mismatched_requests_are_rejected(
    tmp_path: Path,
    mutation: Any,
    expected_code: str,
) -> None:
    request = _request("invalid")
    mutation(request)

    with pytest.raises(ContractError) as captured:
        reserve_holdout_attempt(tmp_path / "holdout.sqlite3", request)

    assert captured.value.code == expected_code


def test_registry_model_inputs_and_incident_model_are_supported(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    request = HoldoutReservationRequest.model_validate(_request("model-input", split="gate_holdout"))
    receipt = reserve_holdout_attempt(registry, request)
    lock_payload = _request(
        "model-lock",
        generation=0,
        approval_kind="incident_lock",
        predecessor_attempt_id="attempt-model-input",
        split="gate_holdout",
    )["qa_approval"]
    lock = QAHoldoutApproval.model_validate(lock_payload)

    result = mark_holdout_incident(
        registry,
        attempt_id="attempt-model-input",
        incident_approval=lock,
    )
    assert receipt["state"] == "reserved"
    assert result["state"] == "incident_review"


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        (lambda path: reserve_holdout_attempt(path, []), "EVAL_HOLDOUT_REQUEST_INVALID"),
        (
            lambda path: mark_holdout_incident(path, attempt_id="attempt-x", incident_approval=[]),
            "EVAL_HOLDOUT_INCIDENT_APPROVAL_INVALID",
        ),
    ],
)
def test_registry_rejects_non_object_contract_inputs(
    tmp_path: Path,
    operation: Any,
    expected_code: str,
) -> None:
    with pytest.raises(ContractError) as captured:
        operation(tmp_path / "holdout.sqlite3")
    assert captured.value.code == expected_code


def test_incident_endpoint_rejects_initial_release_approval(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("initial-incident", split="gate_holdout"))
    approval = _request("not-incident", split="gate_holdout")["qa_approval"]

    with pytest.raises(ContractError) as captured:
        mark_holdout_incident(
            registry,
            attempt_id="attempt-initial-incident",
            incident_approval=approval,
        )
    assert captured.value.code == "EVAL_HOLDOUT_INCIDENT_APPROVAL_INVALID"


def test_registry_requires_existing_owner_only_parent(tmp_path: Path) -> None:
    missing_parent = tmp_path / "missing-parent" / "holdout.sqlite3"
    with pytest.raises(ContractError) as captured:
        reserve_holdout_attempt(missing_parent, _request("missing-parent"))
    assert captured.value.code == "EVAL_HOLDOUT_REGISTRY_PARENT_INVALID"

    permissive_parent = tmp_path / "permissive"
    permissive_parent.mkdir(mode=0o755)
    permissive_parent.chmod(0o755)
    with pytest.raises(ContractError) as captured:
        reserve_holdout_attempt(permissive_parent / "holdout.sqlite3", _request("permissive"))
    assert captured.value.code == "EVAL_HOLDOUT_REGISTRY_PARENT_UNTRUSTED"


@pytest.mark.parametrize(
    ("attempt_id", "actor", "result_sha256", "expected_code"),
    [
        ("attempt-valid", "", None, "EVAL_HOLDOUT_ACTOR_INVALID"),
        ("bad id!", "worker", None, "EVAL_HOLDOUT_TRANSITION_INVALID"),
        ("attempt-valid", "worker", "not-a-hash", "EVAL_HOLDOUT_TRANSITION_INVALID"),
    ],
)
def test_registry_transition_identifiers_are_strict(
    tmp_path: Path,
    attempt_id: str,
    actor: str,
    result_sha256: str | None,
    expected_code: str,
) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("valid"))
    with pytest.raises(ContractError) as captured:
        if result_sha256 is None:
            commit_holdout_exposure(registry, attempt_id=attempt_id, actor=actor)
        else:
            finalize_holdout_attempt(
                registry,
                attempt_id=attempt_id,
                result_sha256=result_sha256,
                actor=actor,
            )
    assert captured.value.code == expected_code


def test_registry_missing_attempts_are_structured_and_do_not_create_state(tmp_path: Path) -> None:
    registry = tmp_path / "holdout.sqlite3"
    reserve_holdout_attempt(registry, _request("existing"))

    for operation in (
        lambda: get_holdout_attempt(registry, attempt_id="attempt-missing"),
        lambda: commit_holdout_exposure(registry, attempt_id="attempt-missing", actor="worker"),
        lambda: finalize_holdout_attempt(
            registry,
            attempt_id="attempt-missing",
            result_sha256="9" * 64,
            actor="worker",
        ),
    ):
        with pytest.raises(ContractError) as captured:
            operation()
        assert captured.value.code == "EVAL_HOLDOUT_ATTEMPT_NOT_FOUND"

    assert len(list_holdout_attempts(registry)) == 1
