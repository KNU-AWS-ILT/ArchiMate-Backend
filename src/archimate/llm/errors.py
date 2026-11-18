"""LLM 모듈 예외. API 계층(백엔드 담당)은 이 예외만 잡아 HTTP 에러로 매핑하면 된다.

권장 매핑 (팀 합의 필요):
    LLMConfigError        → 500 LLM_CONFIG_ERROR
    LLMRateLimitError     → 429 LLM_RATE_LIMITED
    LLMTimeoutError       → 504 LLM_TIMEOUT
    LLMOutputError        → 502 LLM_INVALID_OUTPUT
    LLMProviderError      → 502 LLM_PROVIDER_ERROR
    AllTiersFailedError   → 502 DESIGN_GENERATION_FAILED
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
