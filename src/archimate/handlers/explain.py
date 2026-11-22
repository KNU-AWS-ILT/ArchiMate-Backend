"""노드 설명 Lambda — `archimate.handlers.explain.handler`.

    GET /api/v1/designs/{designId}/nodes/{nodeId}/explanation
explanations 캐시 확인 → 없으면 소형 모델(`LLM_EXPLAIN_MODEL`)로 생성해 저장(TTL 30일).
`monthlyCost`는 설계 생성 때 designs에 저장한 값에서 가져오므로 prices 테이블 권한이 필요 없다.
로직은 api/service.py에 있다.
"""

from functools import lru_cache
from typing import Any

from mangum import Mangum

from archimate.handlers._app import build_app, use_event_loop


@lru_cache
def _asgi() -> Mangum:
    return Mangum(build_app(), lifespan="off")


def handler(event: dict[str, Any], context: Any) -> Any:
    use_event_loop()
    return _asgi()(event, context)
