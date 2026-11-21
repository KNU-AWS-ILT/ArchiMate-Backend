"""생성 작업 실행기 — `POST /designs`가 jobId를 바로 돌려준 뒤 3안 생성을 뒤에서 돌린다.

    LocalJobRunner   로컬·Railway: 같은 프로세스의 asyncio 작업으로 실행
    (예정) Lambda    응답 후 Lambda가 멈추므로 자기 자신을 비동기 호출(InvocationType=Event)해서 실행
                     — 진입점 이름·권한은 Issue #15에서 확정

API 로직은 JobRunner 인터페이스만 알고, 실행 환경별 차이는 여기서 흡수한다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

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
