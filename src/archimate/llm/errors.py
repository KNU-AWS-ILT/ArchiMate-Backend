"""LLM 모듈 예외. API 계층은 이 예외만 잡아 ErrorResponse(schemas.api)로 바꾸면 된다.

권장 매핑 (Issue #7 합의 필요). code는 schemas.api.ErrorCode 값.
    예외                    code                       retryable  노드 설명(동기) HTTP
    LLMConfigError        → INTERNAL_ERROR             false      500
    LLMRateLimitError     → RATE_LIMITED               true       429
    LLMTimeoutError       → LLM_TIMEOUT                true       504
    LLMOutputError        → DESIGN_GENERATION_FAILED   true       502 (노드 설명은 EXPLANATION_FAILED)
    LLMProviderError      → DESIGN_GENERATION_FAILED   true       502 (노드 설명은 EXPLANATION_FAILED)
    AllTiersFailedError   → DESIGN_GENERATION_FAILED   true       -

3안 생성은 비동기 작업이라 HTTP 에러가 아니라 DesignJob.status=failed + error로 전달한다.
아래 각 클래스의 code는 로그·TierFailure.reason용 내부 코드다.
"""


class LLMError(Exception):
    """LLM 모듈의 모든 예외의 부모."""

    code = "LLM_ERROR"


class LLMConfigError(LLMError):
    """API 키 누락, 잘못된 제공자 이름 등."""

    code = "LLM_CONFIG_ERROR"


class LLMProviderError(LLMError):
    """제공자 API 호출 실패(네트워크, 5xx, 권한 등)."""

    code = "LLM_PROVIDER_ERROR"


class LLMRateLimitError(LLMProviderError):
    """요청 한도 초과(무료 티어에서 자주 발생)."""

    code = "LLM_RATE_LIMITED"


class LLMTimeoutError(LLMProviderError):
    code = "LLM_TIMEOUT"


class LLMOutputError(LLMError):
    """응답이 잘렸거나, 거절됐거나, 재시도 후에도 스키마/규칙 검증을 통과하지 못함."""

    code = "LLM_INVALID_OUTPUT"


class AllTiersFailedError(LLMError):
    """3안이 모두 생성에 실패."""

    code = "DESIGN_GENERATION_FAILED"
