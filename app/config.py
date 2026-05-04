from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = Field(
        default="postgresql+asyncpg://fluxurl:fluxurl@localhost:5432/fluxurl",
        description="Async SQLAlchemy URL for Postgres.",
    )
    base_url: str = Field(
        default="http://localhost:8000",
        description="Public base URL used to build short URLs in API responses.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
