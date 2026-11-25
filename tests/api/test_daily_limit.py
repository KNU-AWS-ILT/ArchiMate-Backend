"""사용자별 하루 생성 횟수 제한 (#52) — 서비스·API·설정. 저장소(DynamoDB)는 tests/storage에서 본다."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from archimate.api import app as app_module
from archimate.api import service
from archimate.api.app import create_app
from archimate.api.errors import ApiError
from archimate.api.settings import ApiSettings
from archimate.llm import build_requirements
from archimate.llm.providers.mock import load_fixture
from archimate.schemas import ErrorCode
from archimate.storage import InMemoryRepository

BASE = "/api/v1"


@pytest.fixture
def req(demo_answers):
    return build_requirements(demo_answers)


# ── 날짜 기준 (한국 시간) ──


@pytest.mark.parametrize(
    ("utc", "day"),
    [
        (datetime(2026, 11, 24, 14, 59, tzinfo=UTC), "2026-11-24"),  # 한국 23:59
        (datetime(2026, 11, 24, 15, 0, tzinfo=UTC), "2026-11-25"),  # 한국 00:00
    ],
)
def test_generation_day_uses_korean_midnight(utc, day):
    assert service.generation_day(utc) == day


# ── 서비스 ──


async def test_limit_allows_up_to_n_then_rejects(req):
    repo = InMemoryRepository()
    for _ in range(5):
        await service.create_job(repo, "u1", req, daily_limit=5)
    with pytest.raises(ApiError) as e:
        await service.create_job(repo, "u1", req, daily_limit=5)
    assert (e.value.http_status, e.value.code, e.value.retryable) == (
        429,
        ErrorCode.RATE_LIMITED,
        False,
    )
    assert "5회" in e.value.message


async def test_rejected_request_creates_no_job(req, monkeypatch):
    repo = InMemoryRepository()
    await service.create_job(repo, "u1", req, daily_limit=1)
    created: list[str] = []
    original = repo.create_job

    async def spy(job):
        created.append(job.job_id)
        await original(job)

    monkeypatch.setattr(repo, "create_job", spy)
    with pytest.raises(ApiError):
        await service.create_job(repo, "u1", req, daily_limit=1)
    assert created == []


async def test_limit_is_per_user_and_resets_next_day(req):
    repo = InMemoryRepository()
    today = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)
    await service.create_job(repo, "u1", req, daily_limit=1, now=today)
    await service.create_job(repo, "u2", req, daily_limit=1, now=today)  # 다른 사용자는 따로 센다
    with pytest.raises(ApiError):
        await service.create_job(repo, "u1", req, daily_limit=1, now=today)
    await service.create_job(repo, "u1", req, daily_limit=1, now=today + timedelta(days=1))


async def test_zero_limit_means_unlimited(req):
    repo = InMemoryRepository()
    for _ in range(20):
        await service.create_job(repo, "u1", req, daily_limit=0)


# ── 설정: 고정 응답(mock)은 제한하지 않는다 ──


class _Provider:
    def __init__(self, provider: str) -> None:
        self.provider = provider


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("mock", 0), ("bedrock", 5), ("gemini", 5)],
)
def test_daily_limit_applies_only_to_real_llm(monkeypatch, provider, expected):
    monkeypatch.setattr(app_module, "get_llm_settings", lambda: _Provider(provider))
    assert app_module._daily_limit(ApiSettings(daily_generation_limit=5)) == expected


def test_daily_limit_env_name(monkeypatch):
    monkeypatch.setenv("API_DAILY_GENERATION_LIMIT", "3")
    assert ApiSettings().daily_generation_limit == 3
    monkeypatch.delenv("API_DAILY_GENERATION_LIMIT")
    assert ApiSettings(_env_file=None).daily_generation_limit == 5


# ── API ──


@pytest.fixture
def limited_client(monkeypatch):
    """실제 LLM처럼 제한이 걸린 앱 (생성 작업 자체는 돌리지 않는다)."""
    monkeypatch.setattr(app_module, "get_llm_settings", lambda: _Provider("bedrock"))

    class NoopRunner:
        def submit(self, job_id: str) -> None: ...

        async def aclose(self) -> None: ...

    app = create_app(
        settings=ApiSettings(daily_generation_limit=2),
        repo=InMemoryRepository(),
        runner=NoopRunner(),
    )
    with TestClient(app) as c:
        yield c


def test_api_returns_429_with_error_shape_after_limit(limited_client):
    body = {"answers": json.loads(load_fixture("demo_answers.json"))}
    headers = {"X-User-Id": "u1"}
    assert [limited_client.post(f"{BASE}/designs", json=body, headers=headers).status_code
            for _ in range(2)] == [202, 202]  # fmt: skip

    res = limited_client.post(f"{BASE}/designs", json=body, headers=headers)
    assert res.status_code == 429
    data = res.json()
    assert (data["code"], data["retryable"]) == ("RATE_LIMITED", False)
    assert data["requestId"] == res.headers["X-Request-Id"]

    # 다른 사용자는 영향 없음
    other = limited_client.post(f"{BASE}/designs", json=body, headers={"X-User-Id": "u2"})
    assert other.status_code == 202


def test_invalid_request_is_not_counted(limited_client):
    headers = {"X-User-Id": "u1"}
    for _ in range(5):
        assert limited_client.post(f"{BASE}/designs", json={}, headers=headers).status_code == 400
    body = {"answers": json.loads(load_fixture("demo_answers.json"))}
    assert limited_client.post(f"{BASE}/designs", json=body, headers=headers).status_code == 202


def test_mock_server_is_not_limited(client, demo_answers):
    # 기본 테스트 앱은 LLM_PROVIDER=mock이라 제한이 없다 (Railway 개발 서버와 같은 조건)
    for _ in range(7):
        assert client.post(f"{BASE}/designs", json={"answers": demo_answers}).status_code == 202


def test_openapi_documents_429_on_create(client):
    responses = client.get("/openapi.json").json()["paths"][f"{BASE}/designs"]["post"]["responses"]
    assert "429" in responses
