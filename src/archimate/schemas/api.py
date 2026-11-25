"""API 요청·응답 모양 (Issue #7 제안 초안).

    POST /api/v1/designs                       CreateDesignRequest → 202 DesignJobAccepted
    GET  /api/v1/designs/jobs/{jobId}          → DesignJob (2~3초마다 조회, 끝난 안부터 포함)
    GET  /api/v1/designs/{designId}/guide      → OperationsGuide (생성 시 함께 저장된 값)
    GET  /api/v1/designs/{designId}/nodes/{nodeId}/explanation → NodeExplanation
    에러                                        → ErrorResponse

고도화 문서 4장: 생성은 '접수 → jobId 즉시 응답 → 비동기 생성 → 조회' 흐름 (API Gateway 29초 제한 회피).
인터뷰 질문 목록은 API 없이 contracts/examples/interview_questions.json을 프론트가 상수로 쓴다.

⚠️ 월 비용(MonthlyCost)과 상태 값의 세부는 백엔드(API·비용) 담당이 확정한다.
"""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field

from archimate.schemas.architecture import Design, TierFailure
from archimate.schemas.base import CamelModel
from archimate.schemas.requirements import Requirements


class CreateDesignRequest(CamelModel):
    answers: Requirements


class DesignJobAccepted(CamelModel):
    job_id: str


class JobStatus(StrEnum):
    QUEUED = "queued"  # 접수됨, 생성 전
    RUNNING = "running"  # 생성 중 (끝난 안은 designs에 들어 있음)
    COMPLETED = "completed"  # 3안 모두 끝남 (일부 실패는 failures에)
    FAILED = "failed"  # 3안 모두 실패


class CostItem(CamelModel):
    """노드 1개의 월 비용. 계산 대상이 아니거나 사용량이 없으면 amount·amountUsd가 null."""

    node_id: str
    amount: int | None = Field(description="월 비용(원). 원 단위 반올림")
    amount_usd: float | None = Field(description="월 비용(USD). 소수 둘째 자리 반올림")
    note: str | None = Field(default=None, description="계산 근거 또는 계산하지 못한 이유")


class MonthlyCost(CamelModel):
    """1안의 월 비용 (Issue #10 C안). 프론트는 값을 그대로 표시만 한다.

    - total = items[].amount 합 (null 제외) — 백엔드가 보장
    - 금액은 '항상 무료' 한도를 뺀 실제 청구 예상액. 가입 크레딧은 반영하지 않는다
    """

    currency: Literal["KRW"] = "KRW"
    total: int = Field(description="월 합계(원)")
    total_usd: float = Field(description="월 합계(USD)")
    exchange_rate: float = Field(description="적용 환율 (1 USD당 원)")
    exchange_rate_date: date = Field(description="환율 기준일")
    price_date: date = Field(description="AWS 단가 기준일")
    free_tier: bool = Field(description="무료 한도 안이라 월 0원인지")
    free_tier_note: str | None = Field(default=None, description="무료 한도 안내 문구")
    over_budget: bool = Field(description="total이 인터뷰 월 예산(monthlyBudget)을 넘는지")
    items: list[CostItem] = Field(description="사용자 노드를 뺀 모든 노드")


class DesignResult(Design):
    """3안 중 1안 = LLM 설계(Design) + 저장 id + 월 비용."""

    id: str = Field(description="안 id. C·D 조회 경로에 쓴다")
    monthly_cost: MonthlyCost | None = Field(default=None, description="API·비용 담당이 채움")


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"  # 400 입력 검증 실패
    UNAUTHORIZED = "UNAUTHORIZED"  # 401 로그인 필요
    NOT_FOUND = "NOT_FOUND"  # 404 job·설계·노드 없음
    RATE_LIMITED = "RATE_LIMITED"  # 429 요청 과다 (API 스로틀 또는 LLM 한도)
    DESIGN_GENERATION_FAILED = "DESIGN_GENERATION_FAILED"  # 재시도까지 모두 실패
    LLM_TIMEOUT = "LLM_TIMEOUT"  # LLM 응답 시간 초과
    EXPLANATION_FAILED = "EXPLANATION_FAILED"  # 노드 설명 생성 실패
    INTERNAL_ERROR = "INTERNAL_ERROR"  # 500


class ErrorResponse(CamelModel):
    """Issue #7 B안. 프론트는 code별 한국어 문구로 매핑한다."""

    code: ErrorCode
    message: str
    retryable: bool = Field(description="같은 요청을 다시 시도해도 되는지 ('다시 시도' 버튼 표시)")
    request_id: str | None = Field(default=None, description="문의·로그 추적용")


class DesignJob(CamelModel):
    job_id: str
    status: JobStatus
    designs: list[DesignResult] = Field(default_factory=list, description="끝난 안부터 채워짐")
    failures: list[TierFailure] = Field(default_factory=list)
    error: ErrorResponse | None = Field(default=None, description="status=failed일 때")
