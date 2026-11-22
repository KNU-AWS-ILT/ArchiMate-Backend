"""Lambda 핸들러 테스트 — API Gateway 이벤트 → FastAPI → DynamoDB(moto), 자기 호출 이벤트까지 한 흐름으로 확인."""

import json

import pytest

from archimate.handlers import _app, explain, generate
from archimate.llm.providers.mock import load_fixture


class FakeLambdaClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def lambda_client(repo, monkeypatch):
    client = FakeLambdaClient()
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "archimate-generate")
    real = generate.LambdaJobRunner
    monkeypatch.setattr(generate, "LambdaJobRunner", lambda name: real(name, client=client))
    for cached in (_app.get_repo, generate._asgi, explain._asgi):
        cached.cache_clear()
    yield client
    for cached in (_app.get_repo, generate._asgi, explain._asgi):
        cached.cache_clear()


def gateway_event(method: str, path: str, *, sub: str | None = "user-1", body=None) -> dict:
    claims = {"sub": sub} if sub else {}
    return {
        "resource": path,
        "path": path,
        "httpMethod": method,
        "headers": {"Content-Type": "application/json", "X-User-Id": "attacker"},
        "multiValueHeaders": {"Content-Type": ["application/json"], "X-User-Id": ["attacker"]},
        "queryStringParameters": None,
        "multiValueQueryStringParameters": None,
        "pathParameters": None,
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
        "requestContext": {
            "requestId": "gw-req-1",
            "stage": "prod",
            "httpMethod": method,
            "path": path,
            "resourcePath": path,
            "identity": {"sourceIp": "1.2.3.4", "userAgent": "test"},
            "authorizer": {"claims": claims},
        },
    }


def call(handler, event) -> tuple[int, dict, dict]:
    res = handler.handler(event, None)
    return res["statusCode"], json.loads(res["body"]), res["headers"]


def test_generate_flow_through_gateway_events(lambda_client):
    answers = json.loads(load_fixture("demo_answers.json"))
    status, body, _ = call(
        generate, gateway_event("POST", "/api/v1/designs", body={"answers": answers})
    )
    assert status == 202
    job_id = body["jobId"]

    # 자기 자신을 비동기 호출한다
    (invoke,) = lambda_client.calls
    assert invoke["FunctionName"] == "archimate-generate"
    assert invoke["InvocationType"] == "Event"
    run_event = json.loads(invoke["Payload"])
    assert run_event == {"action": "run_job", "jobId": job_id}

    status, body, _ = call(generate, gateway_event("GET", f"/api/v1/designs/jobs/{job_id}"))
    assert (status, body["status"]) == (200, "queued")

    # 비동기 호출 이벤트가 오면 3안 생성을 실행한다
    assert generate.handler(run_event, None) == {"ok": True}

    status, body, headers = call(generate, gateway_event("GET", f"/api/v1/designs/jobs/{job_id}"))
    assert (status, body["status"], len(body["designs"])) == (200, "completed", 3)
    assert {k.lower(): v for k, v in headers.items()}["x-request-id"] == "gw-req-1"

    # 다른 사용자는 볼 수 없다. X-User-Id 헤더는 무시되고 Cognito sub만 쓴다
    status, body, _ = call(
        generate, gateway_event("GET", f"/api/v1/designs/jobs/{job_id}", sub="user-2")
    )
    assert (status, body["code"]) == (404, "NOT_FOUND")

    # explain Lambda는 같은 테이블에서 설명을 만들어 캐시한다
    design_id = f"{job_id}_minimal_cost"
    path = f"/api/v1/designs/{design_id}/nodes/app_db/explanation"
    status, body, _ = call(explain, gateway_event("GET", path))
    assert (status, body["nodeId"]) == (200, "app_db")


def test_missing_cognito_claims_is_unauthorized(lambda_client):
    status, body, _ = call(generate, gateway_event("GET", "/api/v1/designs/jobs/x", sub=None))
    assert (status, body["code"]) == (401, "UNAUTHORIZED")