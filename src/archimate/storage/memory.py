"""메모리 저장소 — 로컬 개발·테스트용. 프로세스가 재시작되면 데이터가 사라진다."""

from archimate.schemas import (
    DesignResult,
    ErrorResponse,
    JobStatus,
    NodeExplanation,
    TierFailure,
)
from archimate.storage.base import JobRecord


class InMemoryRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._explanations: dict[tuple[str, str], NodeExplanation] = {}

    def _require(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    async def create_job(self, job: JobRecord) -> None:
        if job.job_id in self._jobs:
            raise ValueError(f"이미 있는 jobId: {job.job_id}")
        self._jobs[job.job_id] = job.model_copy(deep=True)

    async def get_job(self, job_id: str) -> JobRecord | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def set_status(self, job_id: str, status: JobStatus) -> None:
        self._require(job_id).status = status

    async def add_design(self, job_id: str, design: DesignResult) -> None:
        self._require(job_id).designs.append(design.model_copy(deep=True))

    async def finish_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        failures: list[TierFailure],
        error: ErrorResponse | None = None,
    ) -> None:
        job = self._require(job_id)
        job.status = status
        job.failures = list(failures)
        job.error = error

    async def get_explanation(self, design_id: str, node_id: str) -> NodeExplanation | None:
        return self._explanations.get((design_id, node_id))

    async def put_explanation(
        self, design_id: str, node_id: str, explanation: NodeExplanation
    ) -> None:
        self._explanations[(design_id, node_id)] = explanation
