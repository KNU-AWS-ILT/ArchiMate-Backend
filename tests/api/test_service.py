"""service.py 단위 테스트 — 생성 실패·일부 실패·노드 설명 실패를 가짜 LLM 함수로 재현."""

import asyncio
from datetime import timedelta

import pytest

from archimate.api import service
from archimate.api.errors import ApiError
from archimate.llm import (
    AllTiersFailedError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    TierCallbackError,
    TierInternalError,
    build_requirements,
)
from archimate.llm.providers.mock import load_fixture
from archimate.schemas import (
    Design,
    DesignDraft,
    DesignSet,
    ErrorCode,
    JobStatus,
    Tier,
    TierFailure,
)
from archimate.storage import InMemoryRepository


def fixture_design(tier: Tier) -> Design:
    draft = DesignDraft.model_validate_json(load_fixture(f"design_{tier.value}.json"))
    return Design(tier=tier, **draft.model_dump(by_alias=False))


@pytest.fixture
def req(demo_answers):
    return build_requirements(demo_answers)


async def test_partial_failure_is_completed(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        design = fixture_design(Tier.MINIMAL_COST)
        await on_tier_done(design)
        failures = [
            TierFailure(tier=Tier.BALANCED, reason="LLM_INVALID_OUTPUT"),
            TierFailure(tier=Tier.SCALABLE, reason="LLM_TIMEOUT"),
        ]
        return DesignSet(designs=[design], failures=failures)

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.COMPLETED
    assert [d.tier for d in job.designs] == [Tier.MINIMAL_COST]
    assert {f.tier for f in job.failures} == {Tier.BALANCED, Tier.SCALABLE}
    assert job.error is None


async def test_all_failed_sets_error(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise AllTiersFailedError("boom")

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.FAILED
    assert job.designs == []
    assert {f.tier for f in job.failures} == set(Tier)
    assert job.error is not None
    assert job.error.code == ErrorCode.DESIGN_GENERATION_FAILED
    assert job.error.retryable is True
    assert job.error.request_id == job_id


async def test_all_failed_with_same_cause_uses_that_cause(req):
    # #21: 셋 다 한도 초과면 일반 실패가 아니라 RATE_LIMITED로 알려 준다
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise AllTiersFailedError(
            failures=[TierFailure(tier=t, reason="LLM_RATE_LIMITED") for t in Tier],
            causes=[LLMRateLimitError("한도") for _ in Tier],
        )

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.FAILED
    assert [f.reason for f in job.failures] == ["LLM_RATE_LIMITED"] * 3
    assert job.error is not None and job.error.code == ErrorCode.RATE_LIMITED


async def test_all_failed_with_mixed_causes_keeps_tier_reasons(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)
    failures = [
        TierFailure(tier=Tier.MINIMAL_COST, reason="LLM_RATE_LIMITED"),
        TierFailure(tier=Tier.BALANCED, reason="LLM_TIMEOUT"),
        TierFailure(tier=Tier.SCALABLE, reason="LLM_INVALID_OUTPUT"),
    ]

    async def fake_generate(_req, *, on_tier_done):
        raise AllTiersFailedError(
            failures=failures,
            causes=[LLMRateLimitError("a"), LLMTimeoutError("b"), LLMOutputError("c")],
        )

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.failures == failures
    assert job.error is not None and job.error.code == ErrorCode.DESIGN_GENERATION_FAILED


async def test_single_tier_mode_failure_reports_one_tier(req):
    # #14: 1안 모드에서 실패하면 시도한 1개 안만 failures에 들어간다
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise AllTiersFailedError(
            failures=[TierFailure(tier=Tier.MINIMAL_COST, reason="LLM_TIMEOUT")],
            causes=[LLMTimeoutError("느림")],
        )

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert [(f.tier, f.reason) for f in job.failures] == [(Tier.MINIMAL_COST, "LLM_TIMEOUT")]
    assert job.error is not None and job.error.code == ErrorCode.LLM_TIMEOUT


async def test_timeout_maps_to_llm_timeout(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise LLMTimeoutError("slow")

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.error is not None and job.error.code == ErrorCode.LLM_TIMEOUT


async def test_unexpected_error_is_recorded(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise RuntimeError("bug")

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.FAILED
    assert job.error is not None and job.error.code == ErrorCode.INTERNAL_ERROR


async def test_explanation_llm_failure_is_502(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        design = fixture_design(Tier.MINIMAL_COST)
        await on_tier_done(design)
        return DesignSet(designs=[design])

    async def failing_explain(*_args, **_kwargs):
        raise LLMOutputError("bad json")

    await service.run_design_job(repo, job_id, generate=fake_generate)
    with pytest.raises(ApiError) as exc:
        await service.get_explanation(
            repo, f"{job_id}_minimal_cost", "app_db", "u1", explain=failing_explain
        )
    assert exc.value.http_status == 502
    assert exc.value.code == ErrorCode.EXPLANATION_FAILED
    assert exc.value.retryable is True


@pytest.mark.parametrize("tier", list(Tier))
def test_design_id_roundtrip(tier):
    design_id = service.make_design_id("job_abc", tier)
    assert service.parse_design_id(design_id) == ("job_abc", tier)


@pytest.mark.parametrize("bad", ["", "job_abc", "_minimal_cost", "job_abc_unknown"])
def test_parse_design_id_rejects(bad):
    assert service.parse_design_id(bad) is None


async def test_cost_failure_keeps_design(req, monkeypatch):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    def broken_cost(*_args, **_kwargs):
        raise RuntimeError("price table down")

    monkeypatch.setattr(service, "estimate_monthly_cost", broken_cost)

    async def fake_generate(_req, *, on_tier_done):
        design = fixture_design(Tier.MINIMAL_COST)
        await on_tier_done(design)
        return DesignSet(designs=[design])

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.COMPLETED
    assert job.designs[0].monthly_cost is None


async def test_mock_delay_shows_queued_and_fills_in_tier_order(req):
    # 지연 0.4초 기준 확인 시점: 0.15초(queued), 0.6초(running, 안 없음). 각각 앞뒤로 0.2초 안팎 여유
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        # 실제처럼 병렬로, 일부러 역순으로 끝낸다
        designs = [fixture_design(t) for t in reversed(list(Tier))]
        await asyncio.gather(*(on_tier_done(d) for d in designs))
        return DesignSet(designs=designs)

    task = asyncio.create_task(
        service.run_design_job(repo, job_id, generate=fake_generate, mock_delay_s=0.4)
    )
    await asyncio.sleep(0.15)
    assert (await service.get_job(repo, job_id, "u1")).status == JobStatus.QUEUED
    await asyncio.sleep(0.45)  # queued(0.4초) 끝, 첫 안 저장(0.8초) 전
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.RUNNING and job.designs == []
    await task
    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.COMPLETED
    assert [d.tier for d in job.designs] == list(Tier)


def test_mock_delay_only_applies_to_mock_provider(monkeypatch):
    from archimate.api.app import _mock_delay
    from archimate.api.settings import ApiSettings
    from archimate.llm.settings import LLMSettings

    settings = ApiSettings(mock_delay_s=2)
    monkeypatch.setattr("archimate.api.app.get_llm_settings", lambda: LLMSettings(provider="mock"))
    assert _mock_delay(settings) == 2
    monkeypatch.setattr(
        "archimate.api.app.get_llm_settings", lambda: LLMSettings(provider="gemini")
    )
    assert _mock_delay(settings) == 0


def test_mock_delay_env_name(monkeypatch):
    from archimate.api.settings import ApiSettings

    monkeypatch.setenv("MOCK_DELAY_S", "1.5")
    assert ApiSettings().mock_delay_s == 1.5


async def test_all_failed_with_same_internal_cause_is_internal_error(req):
    # #14: 비용 계산·저장 같은 서버 쪽 원인으로 세 안이 모두 실패하면 AI 오류가 아니라 INTERNAL_ERROR
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise AllTiersFailedError(
            failures=[TierFailure(tier=t, reason="TIER_CALLBACK_FAILED") for t in Tier],
            causes=[TierCallbackError("저장 실패") for _ in Tier],
        )

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert [f.reason for f in job.failures] == ["TIER_CALLBACK_FAILED"] * 3
    assert job.error is not None
    assert (job.error.code, job.error.retryable) == (ErrorCode.INTERNAL_ERROR, True)


async def test_mixed_internal_and_llm_causes_stay_generation_failed(req):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)

    async def fake_generate(_req, *, on_tier_done):
        raise AllTiersFailedError(
            failures=[
                TierFailure(tier=Tier.MINIMAL_COST, reason="TIER_INTERNAL_ERROR"),
                TierFailure(tier=Tier.BALANCED, reason="LLM_INVALID_OUTPUT"),
                TierFailure(tier=Tier.SCALABLE, reason="LLM_INVALID_OUTPUT"),
            ],
            causes=[TierInternalError("버그"), LLMOutputError("a"), LLMOutputError("b")],
        )

    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert job.error is not None and job.error.code == ErrorCode.DESIGN_GENERATION_FAILED


# ── 1안 모드(LLM_TIERS): 시도하지 않은 안은 failures에 넣지 않는다 (#14) ──


@pytest.fixture
def single_tier(monkeypatch):
    monkeypatch.setattr(service, "attempted_tiers", lambda: [Tier.MINIMAL_COST])


async def test_single_tier_mode_stale_job_reports_only_attempted_tier(req, single_tier):
    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)
    await repo.claim_job(job_id)
    created = (await repo.get_job(job_id)).created_at

    job = await service.get_job(repo, job_id, "u1", now=created + timedelta(seconds=201))
    assert job.status == JobStatus.FAILED
    assert [f.tier for f in job.failures] == [Tier.MINIMAL_COST]


async def test_single_tier_mode_cancelled_job_reports_only_attempted_tier(req, single_tier):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(60)

    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)
    task = asyncio.create_task(service.run_design_job(repo, job_id, generate=never_finishes))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = await service.get_job(repo, job_id, "u1")
    assert [f.tier for f in job.failures] == [Tier.MINIMAL_COST]


async def test_single_tier_mode_generic_llm_error_reports_only_attempted_tier(req, single_tier):
    async def fake_generate(_req, *, on_tier_done):
        raise LLMTimeoutError("느림")

    repo = InMemoryRepository()
    job_id = await service.create_job(repo, "u1", req)
    await service.run_design_job(repo, job_id, generate=fake_generate)
    job = await service.get_job(repo, job_id, "u1")
    assert [(f.tier, f.reason) for f in job.failures] == [(Tier.MINIMAL_COST, "LLM_TIMEOUT")]


@pytest.fixture
def fresh_settings():
    """LLM 설정은 캐시되므로, 환경 변수를 바꾸는 테스트 앞뒤로 비워서 다른 테스트에 남지 않게 한다."""
    from archimate.llm.settings import get_settings

    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


def test_attempted_tiers_follow_the_llm_tiers_setting(monkeypatch, fresh_settings):
    monkeypatch.setenv("LLM_TIERS", "minimal_cost, balanced")
    fresh_settings.cache_clear()
    assert service.attempted_tiers() == [Tier.MINIMAL_COST, Tier.BALANCED]

    monkeypatch.setenv("LLM_TIERS", "")  # 비우면 3안 전체
    fresh_settings.cache_clear()
    assert service.attempted_tiers() == list(Tier)
