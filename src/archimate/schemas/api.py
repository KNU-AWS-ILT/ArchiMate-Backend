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

from enum import StrEnum

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
    node_id: str
    amount: float
    note: str | None = None


class MonthlyCost(CamelModel):
    total: float
    currency: str = Field(description="USD 또는 KRW (API·비용 담당 확정)")
    free_tier: bool
    free_tier_note: str | None = None
    items: list[CostItem]


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
