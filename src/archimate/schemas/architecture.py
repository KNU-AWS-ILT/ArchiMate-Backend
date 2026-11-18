"""아키텍처 JSON 스키마 — LLM 출력 = 프론트 렌더링 입력 = 비용 계산 입력.

설계 원칙
- LLM은 가격을 추정하지 않는다. 각 노드에 '사용량(Usage)'만 출력하고,
  단가 × 사용량 계산은 비용 계산 모듈(백엔드)이 가격 스냅샷으로 한다.
- 모든 설명 문자열은 한국어.
- Gemini·Claude 구조화 출력 양쪽에서 쓰려고 dict·재귀·Union 타입은 쓰지 않는다.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from archimate.schemas.common import AwsService, Tier


class GroupKind(StrEnum):
    """다이어그램 레이어(프론트가 중첩 박스로 그림)."""

    REGION = "region"
    VPC = "vpc"
    AVAILABILITY_ZONE = "availability_zone"
    PUBLIC_SUBNET = "public_subnet"
    PRIVATE_SUBNET = "private_subnet"


class Group(BaseModel):
    id: str = Field(description="영문 소문자·숫자·밑줄, 예: vpc_main")
    kind: GroupKind
    label: str = Field(description="표시 이름, 예: 'VPC (10.0.0.0/16)'")
    parent_id: str | None = Field(default=None, description="상위 그룹 id. 최상위면 null")


class Usage(BaseModel):
    """비용 계산용 월 사용량 추정치. 서비스별 필수 항목은 REQUIRED_USAGE_FIELDS 참고.

    ⚠️ 필드 구성은 비용 계산 담당과 합의 대상.
    """

    monthly_requests: int | None = Field(default=None, description="월 요청 수")
    avg_duration_ms: int | None = Field(default=None, description="Lambda 평균 실행 시간(ms)")
    memory_mb: int | None = Field(default=None, description="Lambda 메모리(MB)")
    storage_gb: float | None = Field(default=None, description="저장 용량(GB)")
    data_transfer_out_gb: float | None = Field(default=None, description="월 외부 전송량(GB)")
    monthly_reads: int | None = Field(default=None, description="DynamoDB 월 읽기 요청 수")
    monthly_writes: int | None = Field(default=None, description="DynamoDB 월 쓰기 요청 수")
    instance_type: str | None = Field(default=None, description="예: t4g.micro, db.t4g.micro")
    instance_count: int | None = Field(default=None, description="인스턴스/태스크 수")
    hours_per_month: int | None = Field(default=None, description="월 가동 시간(상시 730)")
    multi_az: bool | None = Field(default=None, description="RDS Multi-AZ 여부")


# 비용 계산에 반드시 필요한 Usage 필드 (LLM 출력 검증에 사용)
REQUIRED_USAGE_FIELDS: dict[AwsService, tuple[str, ...]] = {
    AwsService.LAMBDA: ("monthly_requests", "avg_duration_ms", "memory_mb"),
    AwsService.API_GATEWAY: ("monthly_requests",),
    AwsService.S3: ("storage_gb",),
    AwsService.CLOUDFRONT: ("monthly_requests", "data_transfer_out_gb"),
    AwsService.DYNAMODB: ("storage_gb", "monthly_reads", "monthly_writes"),
    AwsService.RDS: (
        "instance_type",
        "instance_count",
        "storage_gb",
        "hours_per_month",
        "multi_az",
    ),
    AwsService.EC2: ("instance_type", "instance_count", "hours_per_month"),
    AwsService.ALB: ("hours_per_month",),
}


class Alternative(BaseModel):
    service: AwsService
    reason: str = Field(description="이 대안 대신 선택하지 않은 이유 또는 대안이 나은 상황")


class Rationale(BaseModel):
    """노드 클릭 시 보여줄 '왜 이 서비스인가 / 대안 / 주의점'."""

    why: str = Field(description="이 요구사항에서 이 서비스를 고른 이유 (2~3문장)")
    alternatives: list[Alternative] = Field(description="고려할 만한 대안 1~2개")
    cautions: list[str] = Field(description="운영·비용 주의점 1~3개")


class Node(BaseModel):
    id: str = Field(description="영문 소문자·숫자·밑줄, 예: api_lambda")
    service: AwsService
    label: str = Field(description="표시 이름, 예: 'API 처리 함수'")
    group_id: str | None = Field(default=None, description="소속 그룹 id. 그룹 밖이면 null")
    role: str = Field(description="이 구성에서 하는 일 (한 줄)")
    rationale: Rationale
    usage: Usage


class Edge(BaseModel):
    source: str = Field(description="출발 노드 id. 사용자 브라우저는 'user'")
    target: str = Field(description="도착 노드 id")
    label: str | None = Field(default=None, description="예: 'HTTPS 요청', '이미지 업로드'")


USER_NODE_ID = "user"  # 사용자(브라우저)를 나타내는 예약 id. nodes에 넣지 않는다.


class DesignDraft(BaseModel):
    """LLM이 한 번에 생성하는 '1안'. tier는 파이프라인이 붙인다."""

    title: str = Field(description="안 이름, 예: '서버리스 최소비용 구성'")
    summary: str = Field(description="구성 요약 2~3문장")
    groups: list[Group]
    nodes: list[Node]
    edges: list[Edge]
    operation_difficulty: int = Field(ge=1, le=5, description="운영 난이도 1(쉬움)~5(어려움)")
    pros: list[str] = Field(description="장점 2~4개")
    cons: list[str] = Field(description="단점·한계 2~4개")
    budget_note: str = Field(description="예산 대비 적합성 코멘트. 금액 숫자는 쓰지 않는다")
    scaling_path: list[str] = Field(description="사용자 1만→10만 명으로 늘 때 바꿀 부분 1~3개")


class Design(DesignDraft):
    tier: Tier


class TierFailure(BaseModel):
    tier: Tier
    reason: str


class DesignSet(BaseModel):
    """3안 생성 결과. 일부 안이 실패해도 성공한 안은 반환한다(5일차 게이트 대비)."""

    designs: list[Design]
    failures: list[TierFailure] = Field(default_factory=list)
