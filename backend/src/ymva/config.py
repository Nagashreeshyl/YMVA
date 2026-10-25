"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://ymva:ymva@localhost:5432/ymva"
    redis_url: str = "redis://localhost:6379/0"

    # HMAC key for hashing session tokens and API keys before they are stored.
    secret_key: SecretStr = SecretStr("dev-insecure-secret-change-me")
    # Fernet key (urlsafe base64, 32 bytes) for encrypting provider credentials at rest.
    encryption_key: SecretStr = SecretStr("")

    public_url: str = "http://localhost:3000"
    session_ttl_hours: int = 12
    # Defaults to true whenever PUBLIC_URL is https.
    cookie_secure: bool | None = None

    # First admin, created on startup only when the users table is empty.
    admin_email: str = "admin@yellowmetal.co"
    admin_password: SecretStr = SecretStr("")
    admin_name: str = "Administrator"

    login_max_attempts: int = 5
    # Failed attempts per account from all addresses, against distributed guessing.
    login_max_attempts_per_account: int = 20
    login_window_seconds: int = 900

    timezone: str = "Asia/Kolkata"
    cors_origins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key.get_secret_value().startswith("dev-insecure"):
                raise ValueError("SECRET_KEY must be set in production")
            if not self.encryption_key.get_secret_value():
                raise ValueError("ENCRYPTION_KEY must be set in production")
        return self

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
