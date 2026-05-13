from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = Field(
        ...,
        description="Async SQLAlchemy URL for Postgres. Required — no default; boot fails if missing.",
    )
    base_url: str = Field(
        ...,
        description="Public base URL used to build short URLs in API responses. Required — no default; boot fails if missing.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()