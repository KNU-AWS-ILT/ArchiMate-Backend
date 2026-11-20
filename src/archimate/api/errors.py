"""에러 응답 — 모든 에러는 ErrorResponse `{ code, message, retryable, requestId }` 모양으로 보낸다 (Issue #7 B안).

프론트는 message를 그대로 쓰지 않고 code별 한국어 문구로 바꿔 보여 준다.
"""

import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from archimate.llm import (
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from archimate.schemas import ErrorCode, ErrorResponse

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"


class ApiError(Exception):
    """라우트·서비스에서 던지면 ErrorResponse로 바뀐다."""

    def __init__(
        self, http_status: int, code: ErrorCode, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message
        self.retryable = retryable


def not_found(message: str = "요청한 항목을 찾을 수 없어요.") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND, message)


def llm_error_to_api(e: LLMError, *, explanation: bool = False) -> ApiError:
    """LLM 예외 → HTTP 에러 (노드 설명처럼 바로 응답하는 API용). 매핑표: llm/errors.py."""
    if isinstance(e, LLMConfigError):
        return ApiError(500, ErrorCode.INTERNAL_ERROR, "서버 설정 문제로 처리하지 못했어요.")
    if isinstance(e, LLMRateLimitError):
        return ApiError(
            429,
            ErrorCode.RATE_LIMITED,
            "요청이 많아요. 잠시 후 다시 시도해 주세요.",
            retryable=True,
        )
    if isinstance(e, LLMTimeoutError):
        return ApiError(
            504,
            ErrorCode.LLM_TIMEOUT,
            "AI 응답이 늦어지고 있어요. 다시 시도해 주세요.",
            retryable=True,
        )
    if explanation:
        return ApiError(
            502, ErrorCode.EXPLANATION_FAILED, "설명을 불러오지 못했어요.", retryable=True
        )
    return ApiError(
        502,
        ErrorCode.DESIGN_GENERATION_FAILED,
        "구성도를 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
        retryable=True,
    )


def to_error_response(e: ApiError, request_id: str | None) -> ErrorResponse:
    return ErrorResponse(
        code=e.code, message=e.message, retryable=e.retryable, request_id=request_id
    )


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _json(http_status: int, body: ErrorResponse, request_id: str | None) -> JSONResponse:
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(
        status_code=http_status,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        # TODO(AWS): Lambda에서는 API Gateway 요청 id를 쓰는 편이 로그 추적에 낫다
        request.state.request_id = request.headers.get(REQUEST_ID_HEADER) or (
            f"req_{uuid.uuid4().hex[:12]}"
        )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, e: ApiError) -> JSONResponse:
        rid = _request_id(request)
        return _json(e.http_status, to_error_response(e, rid), rid)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, e: RequestValidationError) -> JSONResponse:
        # FastAPI 기본값 422 대신 400 INVALID_REQUEST로 통일한다 (Issue #7)
        first = e.errors()[0] if e.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        detail = first.get("msg", "")
        message = (
            f"입력값을 확인해 주세요. ({field}: {detail})" if field else "입력값을 확인해 주세요."
        )
        rid = _request_id(request)
        err = ApiError(status.HTTP_400_BAD_REQUEST, ErrorCode.INVALID_REQUEST, message)
        return _json(err.http_status, to_error_response(err, rid), rid)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, e: StarletteHTTPException) -> JSONResponse:
        # 없는 경로(404)·허용되지 않은 메서드(405) 등 프레임워크가 내는 에러
        code = ErrorCode.NOT_FOUND if e.status_code == 404 else ErrorCode.INVALID_REQUEST
        if e.status_code >= 500:
            code = ErrorCode.INTERNAL_ERROR
        rid = _request_id(request)
        err = ApiError(e.status_code, code, str(e.detail))
        return _json(err.http_status, to_error_response(err, rid), rid)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, e: Exception) -> JSONResponse:
        rid = _request_id(request)
        logger.exception("api.unhandled request_id=%s", rid)
        err = ApiError(500, ErrorCode.INTERNAL_ERROR, "일시적인 오류가 발생했어요.", retryable=True)
        return _json(err.http_status, to_error_response(err, rid), rid)


# OpenAPI(Swagger) 문서에 에러 응답 모양을 표시하기 위한 공통 정의
def error_responses(*codes: int) -> dict[int | str, dict]:
    descriptions = {
        400: "입력 검증 실패 (`INVALID_REQUEST`)",
        404: "job·설계·노드 없음 (`NOT_FOUND`)",
        429: "요청 과다 (`RATE_LIMITED`)",
        500: "서버 오류 (`INTERNAL_ERROR`)",
        502: "AI 생성 실패 (`EXPLANATION_FAILED` 등)",
        504: "AI 응답 시간 초과 (`LLM_TIMEOUT`)",
    }
    return {c: {"model": ErrorResponse, "description": descriptions[c]} for c in codes}
