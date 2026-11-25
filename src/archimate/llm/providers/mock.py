"""고정 응답 제공자 — 비용 0. 프론트 연동, CI, 오프라인 개발용.

- 기본: fixtures/ 의 데모 시나리오(동아리 모집 서비스, 월 5만 원) 응답을 돌려준다.
  설계 요청은 user 메시지의 <tier>...</tier> 태그를 보고 해당 안을 고른다.
  운영 절차는 세 안에 공통인 노드만 가리키는 고정 응답 하나를 쓰고,
  노드 설명은 최소비용안 DynamoDB 노드 설명을 돌려준다.
- 테스트: MockProvider(responses=[...]) 로 응답 순서를 직접 지정할 수 있다.
"""

import re
from importlib import resources

from pydantic import BaseModel

from archimate.llm.providers.base import ChatMessage
from archimate.schemas import DesignDraft, ExplanationDraft, OperationsGuide

_TIER_TAG = re.compile(r"<tier>(\w+)</tier>")


def load_fixture(name: str) -> str:
    return resources.files("archimate.llm.providers.fixtures").joinpath(name).read_text("utf-8")


class MockProvider:
    name = "mock"
    model = "mock"

    def __init__(self, responses: list[str] | None = None) -> None:
        self._queue = list(responses) if responses is not None else None
        # 고정 응답 모드: 요청 내용과 상관없이 데모 응답을 준다. 파이프라인은 이때 요청에 따라 정답이
        # 달라지는 검증(충돌 안내, 예산 문구, 필수 서비스, 예산 알람 금액)을 건너뛴다 (#34 리뷰)
        self.fixed_responses = responses is None
        self.calls: list[list[ChatMessage]] = []  # 테스트에서 호출 내용 확인용

    async def generate_json(
        self, *, system: str, messages: list[ChatMessage], output_model: type[BaseModel]
    ) -> str:
        self.calls.append(list(messages))
        if self._queue is not None:
            if not self._queue:
                raise AssertionError("MockProvider: 준비된 응답이 더 없습니다.")
            return self._queue.pop(0)

        if issubclass(output_model, DesignDraft):
            match = _TIER_TAG.search(messages[0].content)
            tier = match.group(1) if match else "minimal_cost"
            return load_fixture(f"design_{tier}.json")
        if issubclass(output_model, OperationsGuide):
            return load_fixture("operations_guide.json")
        if issubclass(output_model, ExplanationDraft):
            return load_fixture("explanation.json")
        raise NotImplementedError(f"MockProvider: {output_model.__name__} 고정 응답 없음")
