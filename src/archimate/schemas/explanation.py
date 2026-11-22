"""노드 설명 — 프론트 명세 D(`GET /designs/{designId}/nodes/{nodeId}/explanation`).

노드를 클릭하면 소형 모델이 "왜 이 서비스인가 / 대안 / 주의점"을 생성하고 캐시한다(고도화 문서 5장).
설명에는 요금 숫자를 넣지 않는다. 금액은 비용 계산 결과로 채운다.
"""

from typing import Literal

from pydantic import Field

from archimate.schemas.base import CamelModel
from archimate.schemas.common import AwsService, ServiceCategory


class AlternativeDraft(CamelModel):
    service_type: AwsService
    comparison: str = Field(description="이 노드의 서비스와 비교한 한 줄 (언제 대안이 나은지)")


class ExplanationDraft(CamelModel):
    """소형 모델이 생성하는 부분."""

    reason: str = Field(description="이 설계에서 이 서비스를 고른 이유 2~3문장. 사용자 상황 기준")
    alternatives: list[AlternativeDraft] = Field(description="대안 1~2개")
    cautions: list[str] = Field(description="운영·비용 주의점 1~3개. 금액 숫자는 쓰지 않는다")


class Alternative(CamelModel):
    service_type: AwsService
    name: str
    comparison: str


class NodeCost(CamelModel):
    """노드 1개의 월 비용 (Issue #10). 3안 응답의 monthlyCost.items[]와 같은 값."""

    currency: Literal["KRW"] = "KRW"
    amount: int = Field(description="월 비용(원)")
    amount_usd: float = Field(description="월 비용(USD)")
    note: str | None = Field(default=None, description="계산 근거")


class NodeExplanation(CamelModel):
    """D 응답. name·category는 SERVICE_INFO, role은 설계 노드, 나머지는 ExplanationDraft."""

    node_id: str
    service_type: AwsService
    name: str
    category: ServiceCategory
    role: str
    reason: str
    alternatives: list[Alternative]
    cautions: list[str]
    monthly_cost: NodeCost | None = Field(default=None, description="API·비용 담당이 채움")
