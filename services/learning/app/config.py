from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(hide_input_in_errors=True, env_ignore_empty=True)

    learning_database_url: SecretStr | None = None
    learning_database_pool_size: int = Field(default=5, ge=1, le=50)
    learning_database_max_overflow: int = Field(default=5, ge=0, le=50)
    learning_database_pool_timeout_seconds: float = Field(default=3, gt=0, le=30)
    learning_database_connect_timeout_seconds: int = Field(default=3, ge=1, le=15)
    learning_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    learning_environment: Literal["service", "local_dev"] = "service"
    learning_dependency_timeout_seconds: float = Field(default=3, gt=0, le=30)

    core_api_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    core_jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    core_service_key: SecretStr | None = Field(default=None, min_length=1)

    learning_internal_service_secret: SecretStr | None = Field(default=None, min_length=1)
    learning_analytics_service_secret: SecretStr | None = Field(default=None, min_length=1)

    ai_mode: Literal["disabled", "http"] = "disabled"
    ai_api_url: AnyHttpUrl | None = None
    ai_service_key: SecretStr | None = Field(default=None, min_length=1)

    document_mode: Literal["disabled", "http", "local_dev"] = "disabled"
    document_api_url: AnyHttpUrl | None = None
    document_service_key: SecretStr | None = Field(default=None, min_length=1)

    blob_storage_dir: Path = Path("/tmp/learning-blobs")
    assignment_max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    default_appeal_window_days: int = Field(default=7, ge=1, le=365)
    transfer_pending_attempt_policy: Literal["allow_finish_until_ends_at", "auto_submit_on_transfer"] = (
        "allow_finish_until_ends_at"
    )
    sweeper_interval_seconds: float = Field(default=10.0, gt=0, le=300)

    @field_validator("learning_database_url")
    @classmethod
    def postgres_url(cls, secret: SecretStr | None) -> SecretStr | None:
        if secret is not None:
            try:
                url = make_url(secret.get_secret_value())
            except (ArgumentError, ValueError):
                raise ValueError("LEARNING_DATABASE_URL must be a psycopg PostgreSQL URL") from None
            if url.drivername != "postgresql+psycopg" or not url.database:
                raise ValueError("LEARNING_DATABASE_URL must be a psycopg PostgreSQL URL")
        return secret

    @field_validator("core_api_url", "ai_api_url", "document_api_url")
    @classmethod
    def origin_only(cls, url: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if url and (url.username or url.password or url.path != "/" or url.query or url.fragment):
            raise ValueError("Expected an HTTP origin without credentials")
        return url

    @field_validator("learning_cors_origins")
    @classmethod
    def explicit_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            normalized = str(cls.origin_only(AnyHttpUrl(origin))).rstrip("/")
            if origin != normalized:
                raise ValueError("Expected exact CORS origins without trailing slash")
        return origins

    @model_validator(mode="after")
    def check_modes(self) -> "Settings":
        if self.document_mode == "local_dev" and self.learning_environment != "local_dev":
            raise ValueError("Local Document fixtures require LEARNING_ENVIRONMENT=local_dev")
        return self
