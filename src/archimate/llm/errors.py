"""LLM 모듈 예외. API 계층은 이 예외만 잡아 ErrorResponse(schemas.api)로 바꾸면 된다.

권장 매핑 (Issue #7 합의 필요). code는 schemas.api.ErrorCode 값.
    예외                    code                       retryable  노드 설명(동기) HTTP
    LLMConfigError        → INTERNAL_ERROR             false      500
    LLMRateLimitError     → RATE_LIMITED               true       429
    LLMTimeoutError       → LLM_TIMEOUT                true       504
    LLMOutputError        → DESIGN_GENERATION_FAILED   true       502 (노드 설명은 EXPLANATION_FAILED)
    LLMProviderError      → DESIGN_GENERATION_FAILED   true       502 (노드 설명은 EXPLANATION_FAILED)
    AllTiersFailedError   → common_cause가 있으면 그 예외의 매핑, 없으면 DESIGN_GENERATION_FAILED
    TierInternalError     → INTERNAL_ERROR             true       500 (안 하나의 실패로만 쓰이고, 세 안이 모두 이 원인이면 API가 이 코드로 응답)

3안 생성은 비동기 작업이라 HTTP 에러가 아니라 DesignJob.status=failed + error로 전달한다.
  - DesignJob.failures = AllTiersFailedError.failures  (실제로 시도한 안만, 1안 모드면 1개)
  - DesignJob.error    = 위 표로 바꾼 AllTiersFailedError.common_cause or AllTiersFailedError
아래 각 클래스의 code는 로그·TierFailure.reason용 내부 코드다.
"""

from collections.abc import Sequence

from archimate.schemas import TierFailure


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


class TierInternalError(LLMError):
    """LLM 호출 밖의 예상 못 한 오류로 안 하나가 실패 (코드 버그, 감싸지 않은 SDK 예외 등)."""

    code = "TIER_INTERNAL_ERROR"


class TierCallbackError(TierInternalError):
    """on_tier_done(비용 계산·저장)에서 오류가 나 안 하나가 실패."""

    code = "TIER_CALLBACK_FAILED"


class AllTiersFailedError(LLMError):
    """시도한 안이 모두 생성에 실패.

    failures: 안별 실패 (DesignJob.failures에 그대로 쓴다)
    causes:   failures와 같은 순서의 원래 예외
    """

    code = "DESIGN_GENERATION_FAILED"

    def __init__(
        self,
        message: str | None = None,
        *,
        failures: Sequence[TierFailure] = (),
        causes: Sequence[LLMError] = (),
    ) -> None:
        self.failures = list(failures)
        self.causes = list(causes)
        if message is None:
            message = "모든 안 생성 실패: " + ", ".join(
                f"{f.tier.value}={f.reason}" for f in self.failures
            )
        super().__init__(message)

    @property
    def common_cause(self) -> LLMError | None:
        """모든 안이 같은 종류의 오류로 실패했으면 그 예외 (예: 셋 다 한도 초과 → LLMRateLimitError).

        API 계층은 이 값으로 error.code를 정한다. 원인이 섞여 있으면 None.
        """
        if self.causes and len({type(c) for c in self.causes}) == 1:
            return self.causes[0]
        return None
