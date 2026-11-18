"""LLM 설정. 환경 변수(.env)에서 읽는다."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["mock", "gemini", "bedrock", "anthropic"]

DEFAULT_MODELS: dict[str, str] = {
    "mock": "mock",
    # 무료 티어 지원 모델(2026-11 기준). 일일 한도가 낮으면 gemini-3.5-flash-lite 로 변경
    "gemini": "gemini-3.8-flash",
    # Bedrock 모델 ID는 'anthropic.' 접두사가 붙는다
    "bedrock": "anthropic.claude-opus-5-5",
    "anthropic": "claude-opus-5-5",
}


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    provider: ProviderName = "mock"
    model: str | None = Field(default=None, description="비우면 제공자별 기본 모델")
    max_validation_retries: int = Field(default=2, ge=0, le=5)
    request_timeout_s: float = Field(default=50.0, gt=0)
    max_output_tokens: int = Field(default=16_000, gt=0)
    claude_effort: Literal["low", "medium", "high"] = "low"

    # 접두사 없는 표준 환경 변수명도 허용
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "LLM_GEMINI_API_KEY")
    )
    aws_region: str = Field(
        default="us-east-1", validation_alias=AliasChoices("AWS_REGION", "LLM_AWS_REGION")
    )

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS[self.provider]


@lru_cache
def get_settings() -> LLMSettings:
    return LLMSettings()
