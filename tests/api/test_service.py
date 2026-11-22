"""service.py 단위 테스트 — 생성 실패·일부 실패·노드 설명 실패를 가짜 LLM 함수로 재현."""

import pytest

from archimate.api import service
from archimate.api.errors import ApiError
from archimate.llm import (
    AllTiersFailedError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
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
