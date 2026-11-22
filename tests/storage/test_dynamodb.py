"""DynamoDBRepository 테스트 — moto가 메모리에서 DynamoDB를 흉내 내므로 AWS 호출·비용이 없다."""

import asyncio
import json
import os
from datetime import timedelta

os.environ["LLM_PROVIDER"] = "mock"

import pytest  # noqa: E402

from archimate.api import service  # noqa: E402
from archimate.llm import build_requirements  # noqa: E402
from archimate.llm.providers.mock import load_fixture  # noqa: E402
from archimate.schemas import ErrorCode, ErrorResponse, JobStatus, Tier, TierFailure  # noqa: E402
from archimate.storage import JobRecord  # noqa: E402


@pytest.fixture
def req():
    return build_requirements(json.loads(load_fixture("demo_answers.json")))


async def test_create_and_get_job(repo, req):
    await repo.create_job(JobRecord(job_id="job_1", user_id="u1", requirements=req))
    job = await repo.get_job("job_1")
    assert job is not None
    assert (job.user_id, job.status, job.designs, job.error) == ("u1", JobStatus.QUEUED, [], None)
    assert job.requirements == req
    assert await repo.get_job("nope") is None


async def test_duplicate_job_id_is_rejected(repo, req):
    rec = JobRecord(job_id="job_1", user_id="u1", requirements=req)
    await repo.create_job(rec)
    with pytest.raises(ValueError):
        await repo.create_job(rec)


async def test_update_missing_job_raises_key_error(repo):
    with pytest.raises(KeyError):
        await repo.set_status("nope", JobStatus.RUNNING)


async def test_finish_job_with_error_and_failures(repo, req):
    await repo.create_job(JobRecord(job_id="job_1", user_id="u1", requirements=req))
    err = ErrorResponse(code=ErrorCode.LLM_TIMEOUT, message="m", retryable=True, request_id="r")
    fails = [TierFailure(tier=Tier.BALANCED, reason="LLM_TIMEOUT")]
    await repo.finish_job("job_1", JobStatus.FAILED, failures=fails, error=err)
    job = await repo.get_job("job_1")
    assert job.status == JobStatus.FAILED
    assert job.failures == fails
    assert job.error == err
    await repo.finish_job("job_1", JobStatus.COMPLETED, failures=[])
    assert (await repo.get_job("job_1")).error is None


async def test_expired_job_is_hidden(repo, req):
    await repo.create_job(JobRecord(job_id="job_1", user_id="u1", requirements=req))
    repo._designs.update_item(
        Key={"jobId": "job_1"},
        UpdateExpression="SET expiresAt = :t",
        ExpressionAttributeValues={":t": 1},
    )
    assert await repo.get_job("job_1") is None


async def test_full_generation_flow_and_explanation_cache(repo, req):
    """service 로직이 DynamoDB 저장소에서도 메모리 저장소와 똑같이 동작한다 (동시에 끝나는 3안 포함)."""
    job_id = await service.create_job(repo, "u1", req)
    await service.run_design_job(repo, job_id)

    job = await service.get_job(repo, job_id, "u1")
    assert job.status == JobStatus.COMPLETED
    assert {d.tier for d in job.designs} == set(Tier)

    design = next(d for d in job.designs if d.tier == Tier.MINIMAL_COST)
    node_id = "app_db"
    first = await service.get_explanation(repo, design.id, node_id, "u1")
    assert await repo.get_explanation(design.id, node_id) is not None
    second = await service.get_explanation(repo, design.id, node_id, "u1")
    assert first == second


async def test_concurrent_add_design_keeps_all(repo, req):
    src = await service.create_job(repo, "u1", req)
    await service.run_design_job(repo, src)
    results = (await repo.get_job(src)).designs
    assert len(results) == 3

    await repo.create_job(JobRecord(job_id="job_2", user_id="u1", requirements=req))
    await asyncio.gather(*(repo.add_design("job_2", r) for r in results))
    assert {d.id for d in (await repo.get_job("job_2")).designs} == {r.id for r in results}


# ── 리뷰 반영: 중복 실행 방지 · 정체 job · 취소 시 failures 모양 ──────────────────


async def test_claim_job_only_once(repo, req):
    await repo.create_job(JobRecord(job_id="job_1", user_id="u1", requirements=req))
    assert await repo.claim_job("job_1") is True
    assert (await repo.get_job("job_1")).status == JobStatus.RUNNING
    assert await repo.claim_job("job_1") is False
    with pytest.raises(KeyError):
        await repo.claim_job("nope")


async def test_duplicate_run_event_generates_once(repo, req):
    calls = 0

    async def fake_generate(_req, *, on_tier_done):
        nonlocal calls
        calls += 1
        raise RuntimeError("stop")

    job_id = await service.create_job(repo, "u1", req)
    await asyncio.gather(
        service.run_design_job(repo, job_id, generate=fake_generate),
        service.run_design_job(repo, job_id, generate=fake_generate),
    )
    assert calls == 1


async def test_stale_running_job_is_reported_failed(repo, req):
    job_id = await service.create_job(repo, "u1", req)
    await repo.claim_job(job_id)
    created = (await repo.get_job(job_id)).created_at

    fresh = await service.get_job(repo, job_id, "u1", now=created + timedelta(seconds=100))
    assert fresh.status == JobStatus.RUNNING

    stale = await service.get_job(repo, job_id, "u1", now=created + timedelta(seconds=201))
    assert stale.status == JobStatus.FAILED
    assert stale.error.retryable is True
    assert {f.tier for f in stale.failures} == set(Tier)


async def test_stale_job_with_finished_design_is_completed_with_failures(repo, req):
    job_id = await service.create_job(repo, "u1", req)
    await service.run_design_job(repo, job_id)
    done = await repo.get_job(job_id)
    keep, rest = done.designs[0], done.designs[1:]
    # 3안 중 1안만 저장된 채 멈춘 상태를 만든다
    await repo.create_job(JobRecord(job_id="job_2", user_id="u1", requirements=req))
    await repo.claim_job("job_2")
    await repo.add_design("job_2", keep)
    created = (await repo.get_job("job_2")).created_at

    job = await service.get_job(repo, "job_2", "u1", now=created + timedelta(seconds=300))
    assert job.status == JobStatus.COMPLETED
    assert job.error is None
    assert {f.tier for f in job.failures} == {d.tier for d in rest}


async def test_cancelled_job_saves_tier_failures(repo, req):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(60)

    job_id = await service.create_job(repo, "u1", req)
    task = asyncio.create_task(service.run_design_job(repo, job_id, generate=never_finishes))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = await service.get_job(repo, job_id, "u1")  # API 응답 모양으로 만들어도 깨지지 않는다
    assert job.status == JobStatus.FAILED
    assert job.error.retryable is True
    assert {f.tier for f in job.failures} == set(Tier)