from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEV_SECRET = "development-only-secret-change-before-production"
KNOWN_INSECURE_SECRETS = {
    DEFAULT_DEV_SECRET,
    "local-compose-secret-change-before-production",
    "replace-with-at-least-32-random-characters",
}


class Settings(BaseSettings):
    app_name: str = "LinkForge"
    app_env: Literal["local", "test", "production"] = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://linkforge:linkforge@localhost:5432/linkforge"
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = Field(default=0.5, gt=0)
    jwt_secret: SecretStr = SecretStr(DEFAULT_DEV_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, gt=0)
    public_base_url: str = "http://localhost:8000"
    cache_ttl_seconds: int = Field(default=3600, gt=0)
    api_rate_limit: int = Field(default=100, gt=0)
    auth_rate_limit: int = Field(default=10, gt=0)
    rate_limit_window_seconds: int = Field(default=60, gt=0)
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def reject_insecure_production_secret(self) -> "Settings":
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use PostgreSQL with the asyncpg driver")
        secret = self.jwt_secret.get_secret_value()
        if self.app_env == "production" and (secret in KNOWN_INSECURE_SECRETS or len(secret) < 32):
            raise ValueError("JWT_SECRET must be a non-default secret of at least 32 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
