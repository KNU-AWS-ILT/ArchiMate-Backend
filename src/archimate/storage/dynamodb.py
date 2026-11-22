"""DynamoDB 저장소 — Lambda(AWS) 배포용 DesignRepository 구현 (Issue #15에서 확정한 키 설계).

    designs        PK jobId (S)                      userId 속성으로 소유자 확인
    explanations   PK designId (S), SK nodeId (S)
    TTL            expiresAt (Number, epoch 초) — designs·explanations 모두 30일

복잡한 모델(요구사항·설계안 등)은 JSON 문자열로 저장한다. DynamoDB는 float을 그대로 못 받고(Decimal 필요),
스키마가 바뀌어도 테이블을 건드리지 않기 위해서다. 조회·갱신에 쓰는 값만 속성으로 둔다.

boto3는 동기 라이브러리라 asyncio.to_thread로 감싼다. 안이 동시에 끝나도 서로 덮어쓰지 않도록
add_design은 읽고 쓰지 않고 list_append로 한 번에 추가한다.
"""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from archimate.schemas import (
    DesignResult,
    ErrorResponse,
    JobStatus,
    NodeExplanation,
    Requirements,
    TierFailure,
)
from archimate.storage.base import JobRecord

DESIGNS_TABLE_ENV = "DESIGNS_TABLE"
EXPLANATIONS_TABLE_ENV = "EXPLANATIONS_TABLE"
TTL_DAYS = 30


def _expires_at(ttl_days: int) -> int:
    return int(time.time()) + ttl_days * 24 * 60 * 60


def _dump_failures(failures: list[TierFailure]) -> str:
    return "[" + ",".join(f.model_dump_json() for f in failures) + "]"


class DynamoDBRepository:
    def __init__(
        self,
        designs_table: Any,
        explanations_table: Any,
        *,
        ttl_days: int = TTL_DAYS,
    ) -> None:
        """테이블 객체(boto3 `Table`)를 받는다. 실행 환경에서는 `from_env()`를 쓴다."""
        self._designs = designs_table
        self._explanations = explanations_table
        self._ttl_days = ttl_days

    @classmethod
    def from_env(cls, *, ttl_days: int = TTL_DAYS) -> "DynamoDBRepository":
        import boto3

        resource = boto3.resource("dynamodb")
        return cls(
            resource.Table(os.environ[DESIGNS_TABLE_ENV]),
            resource.Table(os.environ[EXPLANATIONS_TABLE_ENV]),
            ttl_days=ttl_days,
        )

    # ── designs ─────────────────────────────────────────

    async def create_job(self, job: JobRecord) -> None:
        item = {
            "jobId": job.job_id,
            "userId": job.user_id,
            "status": job.status.value,
            "requirements": job.requirements.model_dump_json(),
            "designs": [d.model_dump_json() for d in job.designs],
            "failures": _dump_failures(job.failures),
            "createdAt": job.created_at.isoformat(),
            "expiresAt": _expires_at(self._ttl_days),
        }
        if job.error is not None:
            item["error"] = job.error.model_dump_json()
        try:
            await asyncio.to_thread(
                self._designs.put_item,
                Item=item,
                ConditionExpression="attribute_not_exists(jobId)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"이미 있는 jobId: {job.job_id}") from e
            raise

    async def get_job(self, job_id: str) -> JobRecord | None:
        res = await asyncio.to_thread(
            self._designs.get_item, Key={"jobId": job_id}, ConsistentRead=True
        )
        item = res.get("Item")
        # TTL 삭제는 최대 수십 시간 늦을 수 있어서, 만료된 항목은 없는 것으로 본다
        if item is None or int(item.get("expiresAt", 0)) < time.time():
            return None
        error = item.get("error")
        return JobRecord(
            job_id=item["jobId"],
            user_id=item["userId"],
            requirements=Requirements.model_validate_json(item["requirements"]),
            status=JobStatus(item["status"]),
            designs=[DesignResult.model_validate_json(d) for d in item.get("designs", [])],
            failures=[
                TierFailure.model_validate(f)
                for f in json.loads(item.get("failures", "[]"))
            ],
            error=ErrorResponse.model_validate_json(error) if error else None,
            created_at=datetime.fromisoformat(item["createdAt"]).astimezone(UTC),
        )

    async def _update(self, job_id: str, **kwargs: Any) -> None:
        """없는 job을 갱신하면 새로 만들지 않고 KeyError (InMemoryRepository와 같은 동작)."""
        kwargs.setdefault("ConditionExpression", "attribute_exists(jobId)")
        try:
            await asyncio.to_thread(
                self._designs.update_item,
                Key={"jobId": job_id},
                **kwargs,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(job_id) from e
            raise

    async def set_status(self, job_id: str, status: JobStatus) -> None:
        await self._update(
            job_id,
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status.value},
        )

    async def claim_job(self, job_id: str) -> bool:
        try:
            await self._update(
                job_id,
                UpdateExpression="SET #s = :running",
                ConditionExpression="attribute_exists(jobId) AND #s = :queued",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":running": JobStatus.RUNNING.value,
                    ":queued": JobStatus.QUEUED.value,
                },
            )
        except KeyError:
            # 없는 job과 이미 시작된 job 모두 조건 실패로 오므로 get으로 구분한다
            if await self.get_job(job_id) is None:
                raise
            return False
        return True

    async def add_design(self, job_id: str, design: DesignResult) -> None:
        await self._update(
            job_id,
            UpdateExpression="SET designs = list_append(if_not_exists(designs, :empty), :d)",
            ExpressionAttributeValues={":empty": [], ":d": [design.model_dump_json()]},
        )

    async def finish_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        failures: list[TierFailure],
        error: ErrorResponse | None = None,
    ) -> None:
        values: dict[str, Any] = {":s": status.value, ":f": _dump_failures(failures)}
        expression = "SET #s = :s, failures = :f"
        if error is not None:
            expression += ", #e = :e"
            values[":e"] = error.model_dump_json()
        else:
            expression += " REMOVE #e"
        await self._update(
            job_id,
            UpdateExpression=expression,
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues=values,
        )

    # ── explanations ────────────────────────────────────

    async def get_explanation(self, design_id: str, node_id: str) -> NodeExplanation | None:
        res = await asyncio.to_thread(
            self._explanations.get_item, Key={"designId": design_id, "nodeId": node_id}
        )
        item = res.get("Item")
        if item is None or int(item.get("expiresAt", 0)) < time.time():
            return None
        return NodeExplanation.model_validate_json(item["data"])

    async def put_explanation(
        self, design_id: str, node_id: str, explanation: NodeExplanation
    ) -> None:
        await asyncio.to_thread(
            self._explanations.put_item,
            Item={
                "designId": design_id,
                "nodeId": node_id,
                "data": explanation.model_dump_json(),
                "expiresAt": _expires_at(self._ttl_days),
            },
        )
