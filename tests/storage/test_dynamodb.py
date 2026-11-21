"""DynamoDBRepository 테스트 — moto가 메모리에서 DynamoDB를 흉내 내므로 AWS 호출·비용이 없다."""

import asyncio
import json
import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-northeast-2")

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from archimate.api import service  # noqa: E402
from archimate.llm import build_requirements  # noqa: E402
from archimate.llm.providers.mock import load_fixture  # noqa: E402
from archimate.schemas import ErrorCode, ErrorResponse, JobStatus, Tier, TierFailure  # noqa: E402
from archimate.storage import JobRecord  # noqa: E402
from archimate.storage.dynamodb import DynamoDBRepository  # noqa: E402


@pytest.fixture
def repo(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "test")
    with mock_aws():
        db = boto3.resource("dynamodb")
        db.create_table(
            TableName="designs",
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        db.create_table(
            TableName="explanations",
            KeySchema=[
                {"AttributeName": "designId", "KeyType": "HASH"},
                {"AttributeName": "nodeId", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "designId", "AttributeType": "S"},
                {"AttributeName": "nodeId", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setenv("DESIGNS_TABLE", "designs")
        monkeypatch.setenv("EXPLANATIONS_TABLE", "explanations")
        yield DynamoDBRepository.from_env()


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
