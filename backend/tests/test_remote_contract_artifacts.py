from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DOCS_ROOT = PROJECT_ROOT / "docs"
EXPORT_SCRIPT = BACKEND_ROOT / "scripts" / "export_remote_contract.py"


def _schema(name: str) -> dict:
    return json.loads((DOCS_ROOT / name).read_text(encoding="utf-8"))


def test_committed_remote_contract_artifacts_match_live_models() -> None:
    completed = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT), "--check"],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_remote_response_schema_excludes_business_controlled_claims() -> None:
    response = _schema("remote-analyzer-response-v1.schema.json")
    properties = response["properties"]
    assert "evidence_grade" not in properties
    assert "accuracy_claim" not in properties
    assert response["additionalProperties"] is False
    assert {"model", "observations", "alignment", "findings"}.issubset(response["required"])


def test_remote_request_schema_pins_model_and_server_controlled_output_policy() -> None:
    request = _schema("remote-analyzer-request-v1.schema.json")
    assert request["properties"]["contract_version"]["const"] == "1.0"
    assert request["properties"]["task_type"]["const"] == "construction_evidence_analysis"
    model_schema = request["$defs"]["RemoteRequestedModel"]
    assert "artifact_sha256" in model_schema["required"]
    policy_schema = request["$defs"]["RemoteOutputPolicy"]
    assert policy_schema["properties"]["accuracy_claims_forbidden"]["const"] is True
