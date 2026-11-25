import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from archimate.api import service
from archimate.api.app import create_app
from archimate.api.deps import get_user_id
from archimate.api.settings import ApiSettings
from archimate.schemas import JobStatus, Requirements, Tier
from archimate.storage import InMemoryRepository, JobRecord


def _app_with_extra_routes():
    app = create_app(settings=ApiSettings(cors_origins=["http://localhost:3000"]))

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    @app.get("/forbidden")
    async def forbidden():
        raise HTTPException(403, "no")

    @app.get("/whoami")
    async def whoami(request_user: str = __import__("fastapi").Depends(get_user_id)):
        return {"userId": request_user}

    return app


def test_unexpected_error_keeps_cors_and_error_shape():
    with TestClient(_app_with_extra_routes(), raise_server_exceptions=False) as c:
        res = c.get("/boom", headers={"Origin": "http://localhost:3000"})
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert res.json()["requestId"] == res.headers["X-Request-Id"]


def test_401_403_use_unauthorized_and_405_keeps_allow_header(client):
    with TestClient(_app_with_extra_routes()) as c:
        assert c.get("/forbidden").json()["code"] == "UNAUTHORIZED"
    res = client.delete("/api/v1/health")
    assert res.status_code == 405
    assert "GET" in res.headers["allow"]


@pytest.mark.parametrize("bad", ["x" * 65, "a b", "a\tb", "<script>"])
def test_invalid_client_request_id_is_replaced(client, bad):
    res = client.get("/api/v1/health", headers={"X-Request-Id": bad})
    assert res.headers["X-Request-Id"] != bad
    assert res.headers["X-Request-Id"].startswith("req_")


def test_valid_client_request_id_is_kept(client):
    res = client.get("/api/v1/health", headers={"X-Request-Id": "abc-123"})
    assert res.headers["X-Request-Id"] == "abc-123"


def test_lambda_event_uses_gateway_request_id_and_cognito_sub():
    app = _app_with_extra_routes()
    event = {
        "requestContext": {
            "requestId": "gw-req-1",
            "authorizer": {"claims": {"sub": "cognito-sub"}},
        }
    }

    @app.middleware("http")
    async def inject_event(request, call_next):  # Mangum이 넣는 scope 값을 흉내 낸다
        request.scope["aws.event"] = event
        return await call_next(request)

    with TestClient(app) as c:
        res = c.get("/whoami", headers={"X-User-Id": "victim", "X-Request-Id": "client"})
    assert res.json() == {"userId": "cognito-sub"}
    assert res.headers["X-Request-Id"] == "gw-req-1"


def test_lambda_event_without_claims_is_unauthorized():
    app = _app_with_extra_routes()

    @app.middleware("http")
    async def inject_event(request, call_next):
        request.scope["aws.event"] = {"requestContext": {"requestId": "gw-req-2"}}
        return await call_next(request)

    with TestClient(app) as c:
        res = c.get("/whoami", headers={"X-User-Id": "victim"})
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHORIZED"


def test_get_routes_document_400(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path, method in [
        ("/api/v1/designs/jobs/{jobId}", "get"),
        ("/api/v1/designs/{designId}/guide", "get"),
        ("/api/v1/designs/{designId}/nodes/{nodeId}/explanation", "get"),
    ]:
        assert "400" in paths[path][method]["responses"]


async def test_cancelled_job_is_saved_as_retryable_failure(demo_answers):
    repo = InMemoryRepository()
    await repo.create_job(
        JobRecord(job_id="job_x", user_id="u", requirements=Requirements(**demo_answers))
    )

    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(60)

    task = asyncio.create_task(service.run_design_job(repo, "job_x", generate=never_finishes))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = await repo.get_job("job_x")
    assert job.status == JobStatus.FAILED
    assert job.error.retryable is True
    # 폴링 응답(DesignJob)으로 바꿀 수 있어야 한다 — failures가 Tier만 담기면 500
    polled = await service.get_job(repo, "job_x", "u")
    assert {f.tier for f in polled.failures} == set(Tier)
    assert {f.reason for f in polled.failures} == {"INTERNAL_ERROR"}
