"""생성 작업 실행기 — `POST /designs`가 jobId를 바로 돌려준 뒤 3안 생성을 뒤에서 돌린다.

    LocalJobRunner   로컬·Railway: 같은 프로세스의 asyncio 작업으로 실행
    LambdaJobRunner  Lambda: 응답 후 Lambda가 멈추므로 자기 자신을 비동기 호출(InvocationType=Event)해서 실행.
                     호출을 받는 쪽은 handlers/generate.py (`RUN_JOB_ACTION` 이벤트)

API 로직은 JobRunner 인터페이스만 알고, 실행 환경별 차이는 여기서 흡수한다.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

JobWork = Callable[[str], Awaitable[None]]


class JobRunner(Protocol):
    def submit(self, job_id: str) -> None: ...

    async def aclose(self) -> None: ...


class LocalJobRunner:
    def __init__(self, work: JobWork) -> None:
        self._work = work
        self._tasks: set[asyncio.Task[None]] = set()  # 참조를 잡아 두지 않으면 GC될 수 있다

    def submit(self, job_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._run(job_id), name=f"job:{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str) -> None:
        try:
            await self._work(job_id)
        except Exception:
            # work 안에서 실패를 저장하므로 여기까지 오면 예상 못 한 버그
            logger.exception("job.crashed job_id=%s", job_id)

    async def aclose(self) -> None:
        """서버 종료 시 진행 중인 작업 취소."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


RUN_JOB_ACTION = "run_job"


class LambdaJobRunner:
    """설계 생성 Lambda가 자기 자신을 비동기 호출한다 (API Gateway 29초 제한 회피).

    필요 권한: 이 Lambda에 대한 lambda:InvokeFunction. 호출 이벤트는 API Gateway 이벤트가 아니라
    `{"action": "run_job", "jobId": ...}`이고, 핸들러가 이 모양을 보고 생성 작업으로 보낸다.
    """

    def __init__(self, function_name: str, client: Any | None = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("lambda")
        self._function_name = function_name
        self._client = client

    def submit(self, job_id: str) -> None:
        # 실패하면 예외가 그대로 올라가 POST /designs가 500 INTERNAL_ERROR(retryable)로 응답한다
        self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="Event",
            Payload=json.dumps({"action": RUN_JOB_ACTION, "jobId": job_id}).encode(),
        )

    async def aclose(self) -> None:
        """비동기 호출이라 이 프로세스가 들고 있는 작업이 없다."""
