"""LLM 설정. 환경 변수(.env)에서 읽는다."""

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from archimate.schemas import Tier

ProviderName = Literal["mock", "gemini", "bedrock", "anthropic"]

DEFAULT_MODELS: dict[str, str] = {
    "mock": "mock",
    # 무료 티어 지원 모델(2026-11 기준). 개발 기본은 빠른 flash-lite (설계 1안 약 13초).
    # gemini-3.8-flash는 품질 확인용 (설계 1안 약 68초라 60초 목표를 넘는다, #5)
    "gemini": "gemini-3.5-flash-lite",
    # Bedrock(Mantle 엔드포인트) 모델 ID는 'anthropic.' 접두사만 붙는다.
    # 서울 리전은 global 라우팅이라 'global.' 같은 추론 프로필 접두사가 필요 없다 (#22)
    "bedrock": "anthropic.claude-opus-5-5",
    "anthropic": "claude-opus-5-5",
}

# 노드 설명용 소형 모델 (고도화 문서 5장: 속도·비용 우선)
DEFAULT_EXPLAIN_MODELS: dict[str, str] = {
    "mock": "mock",
    "gemini": "gemini-3.5-flash-lite",
    "bedrock": "anthropic.claude-haiku-5-5",
    "anthropic": "claude-haiku-5-5",
}


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    provider: ProviderName = "mock"
    model: str | None = Field(default=None, description="설계용 모델. 비우면 제공자별 기본 모델")
    explain_model: str | None = Field(
        default=None, description="노드 설명용 소형 모델. 비우면 제공자별 기본 모델"
    )
    fallback_model: str | None = Field(
        default=None,
        description="설계용 모델이 한도 초과(429)로 실패하면 1회 시도할 대체 모델. 비우면 사용 안 함",
    )
    max_validation_retries: int = Field(default=2, ge=0, le=5)
    # 일시 오류(429, 5xx, 네트워크) 재시도 (#23). 대기: base × 2^n + 무작위(0~base)초, 전체 기한 안에서만
    max_transient_retries: int = Field(default=3, ge=0, le=5)
    retry_base_delay_s: float = Field(default=1.0, gt=0)
    request_timeout_s: float = Field(default=50.0, gt=0, description="LLM 1회 호출 제한 시간(초)")
    # 전체 기한 (#22): 호출마다 남은 시간만 기다리고, 시간이 부족하면 재시도하지 않는다
    generation_deadline_s: float = Field(
        default=150.0,
        gt=0,
        description="설계 생성 전체 기한(초). Lambda 180초 중 나머지는 비용 계산·저장·콜드 스타트용",
    )
    explain_deadline_s: float = Field(
        default=20.0,
        gt=0,
        description="노드 설명 전체 기한(초). Lambda 25초·API Gateway 29초 안에서 응답",
    )
    tiers: Annotated[list[Tier], NoDecode] = Field(
        default_factory=lambda: list(Tier),
        description="생성할 안 (쉼표 구분, 예: minimal_cost). 비우면 3안 전체 (#14 1안 축소용)",
    )
    max_output_tokens: int = Field(default=16_000, gt=0)
    claude_effort: Literal["low", "medium", "high"] = "low"

    # 접두사 없는 표준 환경 변수명도 허용
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "LLM_GEMINI_API_KEY")
    )
    aws_region: str = Field(
        default="ap-northeast-2", validation_alias=AliasChoices("AWS_REGION", "LLM_AWS_REGION")
    )

    @field_validator("tiers", mode="before")
    @classmethod
    def _split_tiers(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = [t.strip() for t in v.split(",") if t.strip()]
        return v or list(Tier)

    @field_validator("tiers")
    @classmethod
    def _order_tiers(cls, v: list[Tier]) -> list[Tier]:
        return [t for t in Tier if t in v]  # 중복 제거, 항상 최소비용 → 균형 → 확장형 순서

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS[self.provider]

    @property
    def resolved_explain_model(self) -> str:
        return self.explain_model or DEFAULT_EXPLAIN_MODELS[self.provider]


@lru_cache
def get_settings() -> LLMSettings:
    return LLMSettings()
