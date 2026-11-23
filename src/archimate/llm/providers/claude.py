"""Claude 제공자 — Amazon Bedrock(최종) 또는 Anthropic API(선택).

두 경로 모두 같은 Messages API를 쓰므로 클라이언트만 다르다.
구조화 출력(output_config.format)으로 JSON 스키마를 강제한다.
"""

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


def _retry_after(e: anthropic.APIStatusError) -> float | None:
    try:
        return float(e.response.headers["retry-after"])
    except (KeyError, ValueError, AttributeError):
        return None


class ClaudeProvider:
    def __init__(
        self,
        client: AsyncAnthropic | AsyncAnthropicBedrockMantle,
        *,
        name: str,
        model: str,
        effort: str,
        max_output_tokens: int,
    ) -> None:
        self._client = client
        self.name = name
        self.model = model
        self._effort = effort
        self._max_output_tokens = max_output_tokens

    @classmethod
    def for_bedrock(
        cls, *, model: str, aws_region: str, effort: str, max_output_tokens: int, timeout_s: float
    ) -> "ClaudeProvider":
        # AWS 자격 증명은 표준 체인(AWS_PROFILE, 환경 변수, Lambda IAM 역할)에서 읽는다
        # 재시도는 파이프라인이 전체 기한 안에서 한다 (SDK 기본 재시도 2회와 겹치지 않게 끈다)
        client = AsyncAnthropicBedrockMantle(
            aws_region=aws_region, timeout=timeout_s, max_retries=0
        )
        return cls(
            client, name="bedrock", model=model, effort=effort, max_output_tokens=max_output_tokens
        )

    @classmethod
    def for_anthropic(
        cls, *, model: str, effort: str, max_output_tokens: int, timeout_s: float
    ) -> "ClaudeProvider":
        # ANTHROPIC_API_KEY 등 표준 자격 증명. 재시도는 파이프라인이 한다
        client = AsyncAnthropic(timeout=timeout_s, max_retries=0)
        return cls(
            client,
            name="anthropic",
            model=model,
            effort=effort,
            max_output_tokens=max_output_tokens,
        )

    async def generate_json(
        self, *, system: str, messages: list[ChatMessage], output_model: type[BaseModel]
    ) -> str:
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self._max_output_tokens,
                system=system,
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
                str(e), transient=transient, retry_after_s=_retry_after(e)
            ) from e
        except anthropic.APIError as e:
            raise LLMProviderError(str(e)) from e

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
