"""FastAPI 앱 — 로컬·Railway 실행용. Swagger UI는 /docs, OpenAPI JSON은 /openapi.json.

uvicorn archimate.api.app:app --reload
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from archimate.api import service
from archimate.api.errors import REQUEST_ID_HEADER, install_error_handlers
from archimate.api.routes import router
from archimate.api.runner import JobRunner, LocalJobRunner
from archimate.api.settings import ApiSettings, get_api_settings
from archimate.llm.settings import get_settings as get_llm_settings
from archimate.storage import DesignRepository, InMemoryRepository

DESCRIPTION = """\
ArchiMate 백엔드 API. 인터뷰 답변으로 AWS 구성 3안(최소비용·균형·확장형)과 월 비용, 운영 절차를 만든다.

- 필드명은 camelCase, 열거형 값은 snake_case, 에러 `code`는 UPPER_SNAKE_CASE
- 모든 에러는 `{ code, message, retryable, requestId }` 모양
- 로컬 개발에서는 `X-User-Id` 헤더로 사용자를 구분한다 (Lambda에서는 Cognito 로그인 sub를 쓰고 헤더는 무시)
"""

logger = logging.getLogger(__name__)

TAGS = [
    {"name": "designs", "description": "3안 생성·조회, 운영 절차, 노드 설명"},
    {"name": "system", "description": "상태 확인"},
]


def _mock_delay(settings: ApiSettings) -> float:
    """MOCK_DELAY_S는 고정 응답일 때만 쓴다 (실제 LLM은 원래 느리다)."""
    if not settings.mock_delay_s:
        return 0.0
    if get_llm_settings().provider != "mock":
        logger.warning("MOCK_DELAY_S ignored: LLM_PROVIDER is not mock")
        return 0.0
    return settings.mock_delay_s


def create_app(
    *,
    settings: ApiSettings | None = None,
    repo: DesignRepository | None = None,
    runner: JobRunner | None = None,
) -> FastAPI:
    """테스트·실행 환경별로 저장소와 실행기를 바꿔 끼울 수 있게 팩토리로 만든다."""
    settings = settings or get_api_settings()
    repo = repo or InMemoryRepository()
    if runner is None:
        delay = _mock_delay(settings)
        runner = LocalJobRunner(
            lambda job_id: service.run_design_job(repo, job_id, mock_delay_s=delay)
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await runner.aclose()

    docs = settings.docs_enabled
    app = FastAPI(
        title="ArchiMate API",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=lifespan,
    )
    app.state.repo = repo
    app.state.runner = runner

    # 요청 id·500 처리 미들웨어를 먼저 추가하고 CORS를 나중에 추가해야 CORS가 바깥에서 감싼다
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-User-Id", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.include_router(router)
    _drop_default_422(app)
    return app


def _drop_default_422(app: FastAPI) -> None:
    """입력 검증 실패는 400 INVALID_REQUEST로 바꿔 보내므로, FastAPI가 문서에 자동으로 넣는 422를 지운다."""
    default_openapi = app.openapi

    def openapi() -> dict:
        if app.openapi_schema is None:
            schema = default_openapi()
            for path_item in schema.get("paths", {}).values():
                for operation in path_item.values():
                    operation.get("responses", {}).pop("422", None)
            schemas = schema.get("components", {}).get("schemas", {})
            schemas.pop("HTTPValidationError", None)
            schemas.pop("ValidationError", None)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


app = create_app()
