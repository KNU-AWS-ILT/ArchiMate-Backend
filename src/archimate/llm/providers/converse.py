"""Bedrock Converse 제공자 — Claude가 아닌 Bedrock 모델(Qwen, Amazon Nova, gpt-oss 등)용.

배포 계정(무료 플랜, 시드니)에서는 Anthropic 모델을 쓸 수 없어서 추가했다 (#15).
Converse API는 모델마다 다른 요청 형식을 하나로 맞춰 주므로 모델 ID만 바꾸면 된다.

JSON 강제: 출력 스키마를 도구(tool) 하나로 등록하고 그 도구를 꼭 부르게 한다(toolChoice).
모델이 돌려준 도구 입력(dict)이 곧 설계 JSON이다. 고도화 문서 1장 "Bedrock 도구 호출로 JSON 강제"와 같은 방식.
스키마·의미 검증과 재시도는 다른 제공자와 똑같이 파이프라인이 한다.

자격 증명은 표준 체인(Lambda IAM 역할, AWS_PROFILE 등). 필요한 권한은 bedrock:InvokeModel (해당 모델).
리전은 AWS_REGION (Lambda가 자동으로 넣는다).
"""

import asyncio
import json
import logging
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)
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

logger = logging.getLogger(__name__)

TOOL_NAME = "submit_result"
_TOOL_DESCRIPTION = "결과를 제출한다. 답은 반드시 이 도구의 입력으로만 보낸다."

# 잠시 뒤 다시 하면 될 수 있는 오류 (파이프라인이 지수 백오프로 재시도한다)
_TRANSIENT_CODES = {
    "InternalServerException",
    "ServiceUnavailableException",
    "ModelNotReadyException",
    "ModelErrorException",
}
# 다른 모델로 바꾸면 될 수 있는 과부하 (재시도 후에도 계속되면 LLM_FALLBACK_MODEL로 넘어간다)
_OVERLOADED_CODES = {"ServiceUnavailableException"}
_AUTH_CODES = {"AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"}

# 모델별 최대 출력 토큰. LLM_MAX_OUTPUT_TOKENS(기본 16000)가 이보다 크면 Bedrock이
# ValidationException으로 거절해서, 요청할 때 이 값으로 낮춘다 (#15 시험: Nova Pro 한도 10000)
_MODEL_MAX_OUTPUT_TOKENS = {"amazon.nova-": 10_000}


def max_output_tokens_for(model: str, requested: int) -> int:
    """모델 ID 앞부분(접두사)이 맞는 한도가 있으면 그 값과 요청값 중 작은 값."""
    for prefix, limit in _MODEL_MAX_OUTPUT_TOKENS.items():
        if model.startswith(prefix):
            return min(requested, limit)
    return requested


def _error_code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _is_tool_choice_unsupported(e: ClientError) -> bool:
    """모델이 toolChoice(도구 강제)를 지원하지 않아 거절한 경우."""
    message = e.response.get("Error", {}).get("Message", "").lower()
    return _error_code(e) == "ValidationException" and (
        "toolchoice" in message or "tool choice" in message or "tool_choice" in message
    )


def _strip_code_fence(text: str) -> str:
    """도구 없이 본문으로 답할 때 붙는 ```json ... ``` 표시를 벗긴다."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _log_usage(model: str, response: dict[str, Any]) -> None:
    usage = response.get("usage") or {}
    logger.info(
        "llm.usage model=%s input=%s cache_read=%s cache_write=%s output=%s",
        model,
        usage.get("inputTokens"),
        usage.get("cacheReadInputTokens"),
        usage.get("cacheWriteInputTokens"),
        usage.get("outputTokens"),
    )


class ConverseProvider:
    name = "bedrock_converse"

    def __init__(
        self,
        *,
        model: str,
        aws_region: str,
        max_output_tokens: int,
        timeout_s: float,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._max_output_tokens = max_output_tokens_for(model, max_output_tokens)
        # 도구 강제를 거절하는 모델이면 한 번 알게 된 뒤로는 강제 없이 부른다 (도구는 1개라 그래도 대개 부른다)
        self._force_tool = True
        # 재시도는 파이프라인이 전체 기한 안에서 한다 (SDK 재시도와 겹치지 않게 1회로 둔다)
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=aws_region,
            config=Config(
                read_timeout=timeout_s,
                connect_timeout=min(10.0, timeout_s),
                retries={"total_max_attempts": 1},
            ),
        )

    def _request(
        self, system: str, messages: list[ChatMessage], output_model: type[BaseModel]
    ) -> dict[str, Any]:
        tool_config: dict[str, Any] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": TOOL_NAME,
                        "description": _TOOL_DESCRIPTION,
                        "inputSchema": {"json": inline_refs(output_model.model_json_schema())},
                    }
                }
            ]
        }
        if self._force_tool:
            tool_config["toolChoice"] = {"tool": {"name": TOOL_NAME}}
        return {
            "modelId": self.model,
            "system": [{"text": system}],
            "messages": [{"role": m.role, "content": [{"text": m.content}]} for m in messages],
            "inferenceConfig": {"maxTokens": self._max_output_tokens},
            "toolConfig": tool_config,
        }

    async def _converse(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.converse, **request)

    async def generate_json(
        self, *, system: str, messages: list[ChatMessage], output_model: type[BaseModel]
    ) -> str:
        try:
            try:
                response = await self._converse(self._request(system, messages, output_model))
            except ClientError as e:
                if not (self._force_tool and _is_tool_choice_unsupported(e)):
                    raise
                logger.warning("llm.converse_tool_choice_unsupported model=%s", self.model)
                self._force_tool = False
                response = await self._converse(self._request(system, messages, output_model))
        except ClientError as e:
            raise self._map_client_error(e) from e
        except (ReadTimeoutError, ConnectTimeoutError) as e:
            raise LLMTimeoutError(f"{self.name} 응답 시간 초과: {e}") from e
        except NoCredentialsError as e:
            raise LLMConfigError(f"{self.name} AWS 자격 증명 없음: {e}") from e
        except EndpointConnectionError as e:
            raise LLMProviderError(f"{self.name} 연결 오류: {e}", transient=True) from e
        except BotoCoreError as e:
            raise LLMProviderError(f"{self.name} 오류: {e}") from e

        _log_usage(self.model, response)
        return self._extract_json(response)

    def _map_client_error(self, e: ClientError) -> LLMProviderError | LLMConfigError:
        code = _error_code(e)
        message = f"{self.name} {code}: {e}"
        if code == "ThrottlingException":
            return LLMRateLimitError(message)
        if code == "ModelTimeoutException":
            return LLMTimeoutError(message)
        if code in _AUTH_CODES:
            return LLMConfigError(
                f"{self.name} 인증/권한 오류 (bedrock:InvokeModel, 모델 액세스): {e}"
            )
        return LLMProviderError(
            message, transient=code in _TRANSIENT_CODES, overloaded=code in _OVERLOADED_CODES
        )

    def _extract_json(self, response: dict[str, Any]) -> str:
        stop = response.get("stopReason")
        if stop == "max_tokens":
            raise LLMOutputError(
                "출력이 maxTokens에서 잘렸습니다. LLM_MAX_OUTPUT_TOKENS를 늘리세요."
            )
        if stop in ("guardrail_intervened", "content_filtered"):
            raise LLMOutputError(f"모델 응답이 차단됐습니다({stop}).")

        content = ((response.get("output") or {}).get("message") or {}).get("content") or []
        for block in content:
            tool_use = block.get("toolUse")
            if tool_use and tool_use.get("name") == TOOL_NAME:
                tool_input = tool_use.get("input")
                if isinstance(tool_input, str):  # 일부 모델은 문자열로 준다
                    return tool_input
                return json.dumps(tool_input, ensure_ascii=False)
        # 도구를 부르지 않고 본문에 JSON을 쓴 경우: 파이프라인 검증에 맡긴다 (틀리면 피드백 후 재시도)
        text = _strip_code_fence("".join(block.get("text", "") for block in content))
        if text:
            return text
        raise LLMOutputError(f"응답에 도구 호출과 텍스트가 모두 없습니다(stopReason={stop}).")
