"""아키텍처 JSON 스키마 — LLM 출력 = 프론트 렌더링 입력 = 비용 계산 입력.

설계 원칙
- LLM은 가격을 추정하지 않는다. 각 노드에 '사용량(Usage)'만 출력하고,
  단가 × 사용량 계산은 비용 계산 모듈(백엔드)이 단가 데이터로 한다.
- 모든 설명 문자열은 한국어.
- Gemini·Claude 구조화 출력 양쪽에서 쓰려고 dict·재귀·Union 타입은 쓰지 않는다.
- JSON 필드명은 camelCase (CamelModel). 프론트 명세 B(`architecture`)와 같은 모양.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field

from archimate.schemas.base import CamelModel
from archimate.schemas.common import AwsService, Tier
from archimate.schemas.operations import OperationsGuide


class GroupKind(StrEnum):
    """다이어그램 묶음(프론트가 중첩 박스로 그림)."""

    PLAIN = "plain"  # 일반 묶음 (예: 운영·모니터링)
    REGION = "region"
    VPC = "vpc"
    AVAILABILITY_ZONE = "availability_zone"
    PUBLIC_SUBNET = "public_subnet"
    PRIVATE_SUBNET = "private_subnet"


class Group(CamelModel):
    id: str = Field(description="영문 소문자·숫자·밑줄, 예: vpc_main")
    kind: GroupKind
    label: str = Field(description="표시 이름, 예: 'VPC (10.0.0.0/16)'")
    parent_id: str | None = Field(default=None, description="상위 그룹 id. 최상위면 null")


class Usage(CamelModel):
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


# 비용 계산에 반드시 필요한 Usage 필드 (파이썬 속성명. LLM 출력 검증에 사용)
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


class Node(CamelModel):
    id: str = Field(description="영문 소문자·숫자·밑줄, 예: api_lambda. 사용자 노드는 'users'")
    service_type: AwsService
    label: str = Field(description="표시 이름, 예: 'API 처리 함수'")
    group_id: str | None = Field(default=None, description="소속 그룹 id. 그룹 밖이면 null")
    role: str = Field(description="이 구성에서 하는 일 (한 줄)")
    reason: str = Field(
        description="이 요구사항에서 이 서비스를 고른 이유 한 줄. 자세한 설명은 노드 설명(D)이 만든다"
    )
    usage: Usage | None = Field(
        default=None, description="비용 계산용 월 사용량. 사용자 노드는 null"
    )


class Edge(CamelModel):
    from_: str = Field(alias="from", description="출발 노드 id")
    to: str = Field(description="도착 노드 id")
    label: str | None = Field(default=None, description="예: 'HTTPS 요청', '이미지 업로드'")


class Architecture(CamelModel):
    """다이어그램 데이터. 좌표는 프론트가 그룹 구조로 자동 배치한다."""

    nodes: list[Node]
    edges: list[Edge]
    groups: list[Group]


DIFFICULTY_LABELS = {1: "쉬움", 2: "보통", 3: "어려움"}


class Difficulty(CamelModel):
    level: Literal[1, 2, 3] = Field(description="운영 난이도 1(쉬움)~3(어려움)")
    reason: str = Field(description="이 난이도인 이유 한 줄")

    @computed_field
    @property
    def label(self) -> str:
        return DIFFICULTY_LABELS[self.level]


class DesignDraft(CamelModel):
    """LLM이 한 번에 생성하는 '1안'. tier·운영 절차는 파이프라인이 붙인다."""

    title: str = Field(description="안 이름, 예: '서버리스 최소비용 구성'")
    summary: str = Field(description="구성 요약 1~2문장")
    difficulty: Difficulty
    architecture: Architecture
    pros: list[str] = Field(description="장점 2~4개")
    cons: list[str] = Field(description="단점·한계 2~4개")
    budget_note: str = Field(description="예산 대비 적합성 코멘트. 금액 숫자는 쓰지 않는다")
    scaling_path: list[str] = Field(
        description="성장 로드맵: 사용자 1만 명, 10만 명이 될 때 바꿀 부분 (단계별 1~3개)"
    )


class Design(DesignDraft):
    tier: Tier
    operations_guide: OperationsGuide | None = Field(
        default=None, description="이 안의 배포·운영 절차. 생성에 실패하면 null"
    )


# TierFailure.reason에 올 수 있는 값 (#20 리뷰). INTERNAL_ERROR는 API 계층(작업 취소, 200초 넘게
# 끝나지 않은 작업)이 쓰고, 나머지는 LLM 계층 예외(llm/errors.py)의 code다.
# 프론트는 안별 실패에는 공통 문구를 쓰고, 전체 실패만 DesignJob.error.code로 구분한다.
TIER_FAILURE_REASONS: dict[str, str] = {
    "LLM_RATE_LIMITED": "AI 호출 한도 초과 (재시도 후에도)",
    "LLM_TIMEOUT": "AI 응답 시간 초과 또는 전체 기한 초과",
    "LLM_INVALID_OUTPUT": "AI 출력이 재시도 후에도 검증을 통과하지 못함",
    "LLM_PROVIDER_ERROR": "AI 제공자 오류 (5xx, 네트워크 등)",
    "LLM_CONFIG_ERROR": "AI 설정 오류 (API 키, 모델 권한 등)",
    "TIER_INTERNAL_ERROR": "AI 호출 밖의 예상 못 한 오류",
    "TIER_CALLBACK_FAILED": "비용 계산·저장 중 오류",
    "INTERNAL_ERROR": "작업이 중단됨 (서버 재시작, 시간 초과로 강제 종료)",
    "DESIGN_GENERATION_FAILED": "원인 정보 없이 전체 실패",
}


class TierFailure(CamelModel):
    tier: Tier
    reason: str = Field(
        description="실패 이유 코드 (내부 진단용). 값: "
        + ", ".join(f"{k}({v})" for k, v in TIER_FAILURE_REASONS.items())
        + ". 프론트는 이 값으로 문구를 나누지 않고 안별 공통 문구를 쓴다"
    )


class DesignSet(CamelModel):
    """3안 생성 결과. 일부 안이 실패해도 성공한 안은 반환한다(5일차 게이트 대비)."""

    designs: list[Design]
    failures: list[TierFailure] = Field(default_factory=list)
