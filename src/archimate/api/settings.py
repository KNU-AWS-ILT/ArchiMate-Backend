"""API 서버 설정. 환경 변수(.env)에서 `API_` 접두사로 읽는다."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    docs_enabled: bool = Field(
        default=True, description="Swagger UI(/docs)·ReDoc(/redoc)·/openapi.json 노출 여부"
    )
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"], description="브라우저 호출을 허용할 프론트 주소"
    )
    daily_generation_limit: int = Field(
        default=5,
        ge=0,
        description=(
            "사용자별 하루(한국 시간) 3안 생성 횟수. 0이면 제한 없음. "
            "LLM_PROVIDER=mock(고정 응답)에서는 비용이 없어 적용하지 않는다 (#52)"
        ),
    )
    mock_delay_s: float = Field(
        default=0.0,
        ge=0,
        le=30,
        validation_alias=AliasChoices("MOCK_DELAY_S", "API_MOCK_DELAY_S"),
        description=(
            "모킹 LLM 지연(초). 0보다 크면 queued·running을 각각 이만큼 보여 주고 "
            "안을 하나씩 이 간격으로 채운다. LLM_PROVIDER=mock일 때만 적용"
        ),
    )


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
