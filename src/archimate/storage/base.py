"""저장소 인터페이스 — 실행 환경(메모리·DynamoDB)과 무관하게 API 로직이 쓰는 모양.

개발 중(로컬·Railway)은 InMemoryRepository, 막바지 AWS는 DynamoDB 구현으로 바꾼다
(코딩 컨벤션 4장: AWS 의존 코드는 교체 가능한 경계 뒤에 둔다).
"""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field

from archimate.schemas import (
    DesignResult,
    ErrorResponse,
    JobStatus,
    NodeExplanation,
    Requirements,
    TierFailure,
)


class JobRecord(BaseModel):
    """designs 테이블 1행 = 생성 작업 1건 (3안 포함). 저장용 내부 모델이라 API 응답에 그대로 쓰지 않는다."""

    job_id: str
    user_id: str
    requirements: Requirements
    status: JobStatus = JobStatus.QUEUED
    designs: list[DesignResult] = Field(default_factory=list)
    failures: list[TierFailure] = Field(default_factory=list)
    error: ErrorResponse | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DesignRepository(Protocol):
    async def create_job(self, job: JobRecord) -> None: ...

    async def get_job(self, job_id: str) -> JobRecord | None: ...

    async def set_status(self, job_id: str, status: JobStatus) -> None: ...

    async def add_design(self, job_id: str, design: DesignResult) -> None:
        """안 하나가 끝날 때마다 호출. 프론트는 끝난 안부터 조회한다."""
        ...

    async def finish_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        failures: list[TierFailure],
        error: ErrorResponse | None = None,
    ) -> None: ...

    async def get_explanation(self, design_id: str, node_id: str) -> NodeExplanation | None: ...

    async def put_explanation(
        self, design_id: str, node_id: str, explanation: NodeExplanation
    ) -> None: ...
