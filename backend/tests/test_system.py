from fastapi.testclient import TestClient


def test_health_and_truth_boundary(client: TestClient) -> None:
    assert client.get("/api/v1/healthz").json() == {"status": "ok"}
    assert client.get("/api/v1/readyz").json() == {"status": "ready"}
    meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    body = meta.json()
    assert body["service_version"] == "0.2.0"
    assert body["adapters"]["stub"]["enabled"] is True
    assert body["adapters"]["demo_fixture"]["synthetic"] is True
    assert body["adapters"]["remote_http"]["enabled"] is False
    assert body["database_schema"]["mode"] == "create_all"
    assert body["database_schema"]["drift_free"] is True
    assert body["database_schema"]["managed_by_alembic"] is False
    assert any("accuracy" in line.lower() for line in body["truth_boundary"])


def test_duplicate_project_code_is_rejected(client: TestClient) -> None:
    payload = {"code": "DUP-001", "name": "项目 A", "location": "测试地点"}
    assert client.post("/api/v1/projects", json=payload).status_code == 201
    assert client.post("/api/v1/projects", json=payload).status_code == 409


def test_mutations_require_the_correct_role(client: TestClient) -> None:
    payload = {"code": "AUTH-001", "name": "鉴权测试项目", "location": "测试地点"}
    anonymous = client.post("/api/v1/projects", json=payload, headers={"X-API-Key": ""})
    assert anonymous.status_code == 401
    reviewer = client.post(
        "/api/v1/projects",
        json=payload,
        headers={"X-API-Key": "test-reviewer-key"},
    )
    assert reviewer.status_code == 403


def test_unknown_proof_is_not_silently_accepted(client: TestClient) -> None:
    response = client.get("/api/v1/proofs/not-a-real-proof/verify")
    assert response.status_code == 404
    lookup = client.get("/api/v1/proofs", params={"fingerprint": "f" * 64})
    assert lookup.status_code == 200
    assert lookup.json() == []
