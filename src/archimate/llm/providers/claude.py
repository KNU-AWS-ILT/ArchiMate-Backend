"""Claude 제공자 — Amazon Bedrock(최종) 또는 Anthropic API(선택).

두 경로 모두 같은 Messages API를 쓰므로 클라이언트만 다르다.
구조화 출력(output_config.format)으로 JSON 스키마를 강제한다.
"""

import logging

import anthropic
from anthropic import AsyncAnthropic, AsyncAnthropicBedrockMantle
from pydantic import BaseModel

from archimate.llm.errors import (
    LLMConfigError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from archimate.llm.providers.base import ChatMessage

logger = logging.getLogger(__name__)


def _retry_after(e: anthropic.APIStatusError) -> float | None:
    try:
        return float(e.response.headers["retry-after"])
    except (KeyError, ValueError, AttributeError):
        return None


def _log_usage(model: str, response: object) -> None:
    """캐시가 실제로 맞는지 확인용 (cache_read가 계속 0이면 앞부분이 요청마다 바뀌는 것)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    logger.info(
        "llm.usage model=%s input=%s cache_read=%s cache_write=%s output=%s",
        model,
        getattr(usage, "input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "output_tokens", None),
    )


class ClaudeProvider:
    def __init__(
        self,
        client: AsyncAnthropic | AsyncAnthropicBedrockMantle,
        *,
        name: str,
        model: str,
        effort: str,
        max_output_tokens: int,
        prompt_cache: bool = False,
    ) -> None:
        self._client = client
        self.name = name
        self.model = model
        self._effort = effort
        self._max_output_tokens = max_output_tokens
        self._prompt_cache = prompt_cache

    @classmethod
    def for_bedrock(
        cls,
        *,
        model: str,
        aws_region: str,
        effort: str,
        max_output_tokens: int,
        timeout_s: float,
        prompt_cache: bool = False,
    ) -> "ClaudeProvider":
        # AWS 자격 증명은 표준 체인(AWS_PROFILE, 환경 변수, Lambda IAM 역할)에서 읽는다
        # 재시도는 파이프라인이 전체 기한 안에서 한다 (SDK 기본 재시도 2회와 겹치지 않게 끈다)
        client = AsyncAnthropicBedrockMantle(
            aws_region=aws_region, timeout=timeout_s, max_retries=0
        )
        return cls(
            client,
            name="bedrock",
            model=model,
            effort=effort,
            max_output_tokens=max_output_tokens,
            prompt_cache=prompt_cache,
        )

    @classmethod
    def for_anthropic(
        cls,
        *,
        model: str,
        effort: str,
        max_output_tokens: int,
        timeout_s: float,
        prompt_cache: bool = False,
    ) -> "ClaudeProvider":
        # ANTHROPIC_API_KEY 등 표준 자격 증명. 재시도는 파이프라인이 한다
        client = AsyncAnthropic(timeout=timeout_s, max_retries=0)
        return cls(
            client,
            name="anthropic",
            model=model,
            effort=effort,
            max_output_tokens=max_output_tokens,
            prompt_cache=prompt_cache,
        )

    def _system(self, system: str) -> str | list[dict]:
        """LLM_PROMPT_CACHE=true면 시스템 프롬프트를 캐시한다 (3안의 설계·검증 재시도가 같은 앞부분을 공유).
        최소 캐시 길이(Opus·Haiku 5.5: 512토큰)보다 짧으면 오류 없이 캐시만 안 된다."""
        if not self._prompt_cache:
            return system
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

    async def generate_json(
        self, *, system: str, messages: list[ChatMessage], output_model: type[BaseModel]
    ) -> str:
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self._max_output_tokens,
                system=self._system(system),
                messages=[{"role": m.role, "content": m.content} for m in messages],
                output_config={
                    "effort": self._effort,
                    "format": {
                        "type": "json_schema",
                        # 미지원 제약(minimum 등)은 설명으로 옮겨 주고, 검증은 파이프라인이 한다
                        "schema": anthropic.transform_schema(output_model),
                    },
                },
            )
        except anthropic.RateLimitError as e:
            raise LLMRateLimitError(str(e), retry_after_s=_retry_after(e)) from e
        except anthropic.APITimeoutError as e:
            raise LLMTimeoutError(str(e)) from e
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMConfigError(f"{self.name} 인증/권한 오류: {e}") from e
        except anthropic.APIConnectionError as e:
            raise LLMProviderError(str(e), transient=True) from e
        except anthropic.APIStatusError as e:
            # 5xx·529(과부하)는 일시 오류, 그 밖의 4xx는 요청 자체의 문제
            transient = e.status_code >= 500
            raise LLMProviderError(
                str(e),
                transient=transient,
                retry_after_s=_retry_after(e),
                overloaded=e.status_code in (503, 529),
            ) from e
        except anthropic.APIError as e:
            raise LLMProviderError(str(e)) from e

        _log_usage(self.model, response)
        if response.stop_reason == "refusal":
            raise LLMOutputError("모델이 요청을 거절했습니다(refusal).")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(
                "출력이 max_tokens에서 잘렸습니다. LLM_MAX_OUTPUT_TOKENS를 늘리세요."
            )

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise LLMOutputError("응답에 텍스트 블록이 없습니다.")
        return text
