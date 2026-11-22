"""HTTP 계층 테스트 — 모킹 LLM으로 접수 → 조회 → 운영 절차 → 노드 설명 흐름 전체."""

import time

from fastapi.testclient import TestClient

from archimate.api.app import create_app
from archimate.api.settings import ApiSettings

BASE = "/api/v1"


def wait_for_job(client: TestClient, job_id: str, headers: dict | None = None) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"{BASE}/designs/jobs/{job_id}", headers=headers).json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("작업이 제한 시간 안에 끝나지 않았습니다")


def create_job(client: TestClient, answers: dict, headers: dict | None = None) -> str:
    res = client.post(f"{BASE}/designs", json={"answers": answers}, headers=headers)
    assert res.status_code == 202, res.text
    return res.json()["jobId"]


def test_health(client):
    assert client.get(f"{BASE}/health").json() == {"status": "ok"}


def test_full_flow(client, demo_answers):
    job_id = create_job(client, demo_answers)
    assert job_id.startswith("job_")

    job = wait_for_job(client, job_id)
    assert job["status"] == "completed"
    assert job["failures"] == []
    assert job["error"] is None
    assert {d["tier"] for d in job["designs"]} == {"minimal_cost", "balanced", "scalable"}
    for d in job["designs"]:
        assert d["id"] == f"{job_id}_{d['tier']}"
        cost = d["monthlyCost"]
        assert cost["currency"] == "KRW"
        assert cost["total"] == sum(i["amount"] for i in cost["items"] if i["amount"] is not None)
        assert "users" not in {i["nodeId"] for i in cost["items"]}

    design_id = f"{job_id}_minimal_cost"
    guide = client.get(f"{BASE}/designs/{design_id}/guide")
    assert guide.status_code == 200
    assert guide.json()["phases"]

    expl = client.get(f"{BASE}/designs/{design_id}/nodes/app_db/explanation")
    assert expl.status_code == 200, expl.text
    body = expl.json()
    assert body["nodeId"] == "app_db"
    assert body["serviceType"] == "dynamodb"
    assert body["alternatives"]
    # 노드 설명 금액 = 3안 응답의 같은 노드 금액
    design = next(d for d in job["designs"] if d["id"] == design_id)
    item = next(i for i in design["monthlyCost"]["items"] if i["nodeId"] == "app_db")
    assert body["monthlyCost"] == {
        "currency": "KRW",
        "amount": item["amount"],
        "amountUsd": item["amountUsd"],
        "note": item["note"],
    }
    # 계산 대상이 아닌 노드(CloudWatch)는 monthlyCost: null
    logs = client.get(f"{BASE}/designs/{design_id}/nodes/logs/explanation")
    assert logs.status_code == 200, logs.text
    assert logs.json()["monthlyCost"] is None


def test_explanation_is_cached(client, repo, demo_answers):
    job_id = create_job(client, demo_answers)
    wait_for_job(client, job_id)
    design_id = f"{job_id}_minimal_cost"

    first = client.get(f"{BASE}/designs/{design_id}/nodes/app_db/explanation").json()
    assert repo._explanations[(design_id, "app_db")] is not None
    second = client.get(f"{BASE}/designs/{design_id}/nodes/app_db/explanation").json()
    assert first == second


def test_invalid_request_returns_400(client, demo_answers):
    answers = {**demo_answers, "concurrentUsers": "a_lot"}
    res = client.post(f"{BASE}/designs", json={"answers": answers})
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_REQUEST"
    assert body["retryable"] is False
    assert "concurrentUsers" in body["message"]
    assert body["requestId"] == res.headers["X-Request-Id"]


def test_missing_body_returns_400(client):
    res = client.post(f"{BASE}/designs", json={})
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REQUEST"


def test_unknown_job_returns_404(client):
    res = client.get(f"{BASE}/designs/jobs/job_nope")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"


def test_unknown_route_uses_error_shape(client):
    res = client.get(f"{BASE}/nope")
    assert res.status_code == 404
    assert set(res.json()) == {"code", "message", "retryable", "requestId"}


def test_other_user_cannot_see_job(client, demo_answers):
    job_id = create_job(client, demo_answers, headers={"X-User-Id": "alice"})
    wait_for_job(client, job_id, headers={"X-User-Id": "alice"})

    res = client.get(f"{BASE}/designs/jobs/{job_id}", headers={"X-User-Id": "bob"})
    assert res.status_code == 404
    res = client.get(f"{BASE}/designs/{job_id}_minimal_cost/guide", headers={"X-User-Id": "bob"})
    assert res.status_code == 404


def test_bad_design_and_node_ids(client, demo_answers):
    job_id = create_job(client, demo_answers)
    wait_for_job(client, job_id)

    assert client.get(f"{BASE}/designs/not-a-design/guide").status_code == 404
    users = client.get(f"{BASE}/designs/{job_id}_minimal_cost/nodes/users/explanation")
    assert users.status_code == 404
    missing = client.get(f"{BASE}/designs/{job_id}_minimal_cost/nodes/nope/explanation")
    assert missing.status_code == 404


def test_openapi_documents_error_shape(client):
    spec = client.get("/openapi.json").json()
    post = spec["paths"]["/api/v1/designs"]["post"]
    assert post["operationId"] == "createDesign"
    assert set(post["responses"]) == {"202", "400", "500"}
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    assert client.get("/docs").status_code == 200


def test_docs_can_be_disabled():
    app = create_app(settings=ApiSettings(docs_enabled=False))
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404
