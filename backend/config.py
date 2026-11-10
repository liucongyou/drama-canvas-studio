from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings for the new layered backend.

    The legacy HTTP server remains the production entry point until the shadow
    migration and contract gates pass. This package is deliberately isolated so
    a missing PostgreSQL configuration can never prevent the current app booting.
    """

    model_config = SettingsConfigDict(
        env_prefix="CANVAS_",
        env_file=".env",
        extra="ignore",
    )

    environment: str = Field(default="development")
    database_url: str = Field(
        default=(
            "postgresql+psycopg://canvas_admin@127.0.0.1:55432/"
            "drama_canvas_shadow"
        )
    )
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=200)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_statement_timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    db_lock_timeout_ms: int = Field(default=5000, ge=500, le=60000)
    internal_api_token: str = ""

    legacy_sqlite_enabled: bool = True
    shadow_read_enabled: bool = False
    shadow_write_enabled: bool = False
    shadow_verify_interval_seconds: int = Field(default=300, ge=30)

    generation_worker_concurrency: int = Field(default=4, ge=1, le=64)
    generation_provider_concurrency: int = Field(default=4, ge=1, le=64)

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
