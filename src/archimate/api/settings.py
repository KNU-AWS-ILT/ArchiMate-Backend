"""API 서버 설정. 환경 변수(.env)에서 `API_` 접두사로 읽는다."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    docs_enabled: bool = Field(
        default=True, description="Swagger UI(/docs)·ReDoc(/redoc)·/openapi.json 노출 여부"
    )
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"], description="브라우저 호출을 허용할 프론트 주소"
    )


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
