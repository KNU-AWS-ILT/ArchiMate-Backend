"""Gemini 제공자 — 개발 기간 무료 티어용.

주의
- 무료 티어 입력은 Google 제품 개선에 쓰일 수 있다 → 실제 사용자 데이터 넣지 말 것.
- 한도는 'API 키'가 아니라 'Google Cloud 프로젝트' 단위. 팀원별로 각자 프로젝트를 만들면 한도가 따로 잡힌다.
"""

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from archimate.llm.errors import (
    LLMConfigError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from archimate.llm.providers.base import ChatMessage
from archimate.llm.providers.schema import inline_refs


class GeminiProvider:
    name = "gemini"

    def __init__(
        self, *, api_key: str | None, model: str, max_output_tokens: int, timeout_s: float
    ) -> None:
        if not api_key:
            raise LLMConfigError("GEMINI_API_KEY가 설정되지 않았습니다.")
        self.model = model
        self._max_output_tokens = max_output_tokens
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )

    async def generate_json(
        self, *, system: str, messages: list[ChatMessage], output_model: type[BaseModel]
    ) -> str:
        contents = [
            types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=inline_refs(output_model.model_json_schema()),
            max_output_tokens=self._max_output_tokens,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except genai_errors.APIError as e:
            if e.code == 429:
                raise LLMRateLimitError(f"Gemini 무료 한도 초과: {e}") from e
            if e.code in (401, 403):
                raise LLMConfigError(f"Gemini 인증 오류: {e}") from e
            # 500·503(과부하)·504는 일시 오류. 개발 중 무료 티어에서 503이 실제로 났다 (#23)
            raise LLMProviderError(str(e), transient=e.code >= 500, overloaded=e.code == 503) from e
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"Gemini 응답 시간 초과: {e!r}") from e
        except httpx.TransportError as e:
            # 네트워크 끊김 등 (httpx는 google-genai가 쓰는 HTTP 클라이언트)
            raise LLMProviderError(f"Gemini 연결 오류: {e!r}", transient=True) from e

        candidate = response.candidates[0] if response.candidates else None
        finish = candidate.finish_reason if candidate else None
        if finish == types.FinishReason.MAX_TOKENS:
            raise LLMOutputError("출력이 max_output_tokens에서 잘렸습니다.")
        if finish not in (None, types.FinishReason.STOP):
            raise LLMOutputError(f"Gemini 응답 비정상 종료: {finish}")
        if not response.text:
            raise LLMOutputError("Gemini 응답이 비어 있습니다.")
        return response.text
