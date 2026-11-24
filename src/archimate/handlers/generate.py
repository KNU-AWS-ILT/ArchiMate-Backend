"""설계 생성 Lambda — `archimate.handlers.generate.handler`.

API Gateway(Lambda 프록시) 이벤트는 Mangum이 FastAPI 앱으로 넘기고, 담당 경로는 다음과 같다.
    POST /api/v1/designs                          접수 → jobId 저장 → 자기 자신을 비동기 호출 → 202
    GET  /api/v1/designs/jobs/{jobId}             상태 조회
    GET  /api/v1/designs/{designId}/guide         운영 절차 조회

자기 자신의 비동기 호출 이벤트(`{"action": "run_job", "jobId": ...}`)는 API Gateway를 거치지 않고
3안 생성을 직접 실행한다. 로직은 api/service.py에 있고 여기서는 이벤트 종류만 가른다.
"""

import os
from functools import lru_cache
from typing import Any

from mangum import Mangum

from archimate.api import service
from archimate.api.runner import RUN_JOB_ACTION, LambdaJobRunner
from archimate.handlers._app import (
    build_app,
    get_price_source,
    get_repo,
    run_async,
    use_event_loop,
)


@lru_cache
def _asgi() -> Mangum:
    runner = LambdaJobRunner(os.environ["AWS_LAMBDA_FUNCTION_NAME"])
    # lifespan: Lambda 환경에서는 시작·종료 이벤트가 없다
    return Mangum(build_app(runner), lifespan="off")


def handler(event: dict[str, Any], context: Any) -> Any:
    if event.get("action") == RUN_JOB_ACTION:
        run_async(
            service.run_design_job(get_repo(), event["jobId"], price_source=get_price_source())
        )
        return {"ok": True}
    use_event_loop()
    return _asgi()(event, context)
