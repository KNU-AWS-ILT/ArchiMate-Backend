"""LLM 제공자 인터페이스.

제공자는 '스키마에 맞는 JSON 문자열'을 돌려주는 일만 한다.
파싱·검증·재시도는 파이프라인이 공통으로 처리하므로, 제공자를 바꿔도 동작이 같다.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate_json(
        self,
        *,
        system: str,
        messages: list[ChatMessage],
        output_model: type[BaseModel],
    ) -> str:
        """output_model 스키마를 따르는 JSON 문자열을 반환한다.

        실패 시 archimate.llm.errors 의 예외를 던진다.
        """
        ...
