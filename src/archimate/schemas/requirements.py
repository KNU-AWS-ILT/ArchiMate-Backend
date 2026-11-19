"""인터뷰 답변을 정리한 요구사항. LLM 입력이 된다."""

from enum import StrEnum

from pydantic import Field

from archimate.schemas.base import CamelModel


class ServiceType(StrEnum):
    WEB_APP = "web_app"  # 일반 웹 서비스(프론트 + API)
    API_ONLY = "api_only"  # 모바일 앱 백엔드 등 API만
    STATIC_SITE = "static_site"  # 정적 사이트/랜딩
    AI_CHATBOT = "ai_chatbot"  # LLM 호출이 핵심인 서비스
    DATA_BATCH = "data_batch"  # 주기적 수집·처리
    OTHER = "other"


class UserScale(StrEnum):
    """동시 사용자 수 구간."""

    UNDER_100 = "under_100"  # 100명 이하
    UNDER_1K = "under_1k"
    UNDER_10K = "under_10k"
    OVER_10K = "over_10k"


class AwsExperience(StrEnum):
    NONE = "none"  # AWS 경험 없음
    BEGINNER = "beginner"  # 콘솔로 몇 번 써 봄
    INTERMEDIATE = "intermediate"  # 직접 배포·운영해 봄
    ADVANCED = "advanced"  # 인프라 담당 경험


class DataStorageNeed(StrEnum):
    RELATIONAL = "relational"  # 회원·주문처럼 관계가 많은 데이터
    KEY_VALUE = "key_value"  # 단순 조회 위주 데이터
    FILE_STORAGE = "file_storage"  # 이미지·파일 업로드
    NONE = "none"


class TrafficPattern(StrEnum):
    STEADY = "steady"
    BUSINESS_HOURS = "business_hours"
    SPIKY = "spiky"  # 이벤트·모집 기간에 몰림
    UNKNOWN = "unknown"


class AvailabilityNeed(StrEnum):
    BEST_EFFORT = "best_effort"  # 잠깐 멈춰도 괜찮음
    STANDARD = "standard"  # 업무 시간엔 안정적이어야 함
    HIGH = "high"  # 항상 떠 있어야 함


class Requirements(CamelModel):
    """가이드형 인터뷰 답변 (API 요청의 `answers`). JSON 필드명 = 인터뷰 질문 id."""

    service_type: ServiceType
    concurrent_users: UserScale
    monthly_budget: int = Field(ge=0, le=100_000_000, description="월 예산(원, KRW)")
    aws_experience: AwsExperience
    data_storage: list[DataStorageNeed] = Field(min_length=1)
    traffic_pattern: TrafficPattern = TrafficPattern.UNKNOWN
    availability: AvailabilityNeed = AvailabilityNeed.STANDARD
    free_text: str | None = Field(
        default=None, max_length=1000, description="자유 서술 보완 (서비스 설명, 특이사항)"
    )
