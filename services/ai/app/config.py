from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(hide_input_in_errors=True, env_ignore_empty=True)

    ai_database_url: SecretStr | None = None
    ai_database_pool_size: int = Field(default=5, ge=1, le=50)
    ai_database_max_overflow: int = Field(default=5, ge=0, le=50)
    ai_database_pool_timeout_seconds: float = Field(default=3, gt=0, le=30)
    ai_database_connect_timeout_seconds: int = Field(default=3, ge=1, le=15)
    ai_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    ai_environment: Literal["service", "local_dev"] = "service"

    core_api_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    core_jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    core_service_key: SecretStr | None = Field(default=None, min_length=1)
    ai_internal_service_secret: SecretStr | None = Field(default=None, min_length=1)
    ai_analytics_service_secret: SecretStr | None = Field(default=None, min_length=1)
    ai_index_root: Path | None = None
    ai_embedding_model_path: Path | None = None
    ai_embedding_tokenizer_path: Path | None = None
    ai_embedding_repo: str | None = None
    ai_embedding_revision: str | None = None
    ai_embedding_variant: str | None = None
    ai_embedding_threads: int = Field(default=2, ge=1, le=16)
    ai_embedding_max_tokens: int = Field(default=512, ge=8, le=8192)

    document_mode: Literal["disabled", "http", "local_dev"] = "disabled"
    document_api_url: AnyHttpUrl | None = None
    document_service_key: SecretStr | None = Field(default=None, min_length=1)
    document_locate_path: str | None = None
    learning_mode: Literal["disabled", "http"] = "disabled"
    learning_api_url: AnyHttpUrl | None = None
    learning_service_key: SecretStr | None = Field(default=None, min_length=1)
    learning_active_assessment_path: str | None = None
    learning_quiz_export_path: str | None = None
    ai_dependency_timeout_seconds: float = Field(default=3, gt=0, le=30)

    llama_mode: Literal["disabled", "local", "openai_compatible"] = "disabled"
    llama_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8080")
    llm_api_base: AnyHttpUrl | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = Field(default="deepseek-chat", min_length=1, max_length=128)
    llama_model_path: Path | None = None
    llama_server_path: Path | None = None
    llama_model_repo: str | None = Field(default=None, min_length=1)
    llama_model_revision: str | None = Field(default=None, min_length=1)
    llama_model_variant: str | None = Field(default=None, min_length=1)
    llama_model_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    llama_context_tokens: int = Field(default=4096, ge=833, le=32768)
    llama_output_ceiling: int = Field(default=768, ge=768, le=2048)
    llama_timeout_seconds: float = Field(default=180, gt=0, le=600)

    ai_rag_workers: int = Field(default=4, ge=2, le=32)
    ai_rag_admission_seconds: float = Field(default=3, gt=0, le=30)
    ai_worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    ai_stale_operation_seconds: int = Field(default=300, ge=30, le=3600)

    @field_validator("ai_database_url")
    @classmethod
    def postgres_url(cls, secret: SecretStr | None) -> SecretStr | None:
        if secret is not None:
            try:
                url = make_url(secret.get_secret_value())
            except (ArgumentError, ValueError):
                raise ValueError("AI_DATABASE_URL must be a psycopg PostgreSQL URL") from None
            if url.drivername != "postgresql+psycopg" or not url.database:
                raise ValueError("AI_DATABASE_URL must be a psycopg PostgreSQL URL")
        return secret

    @field_validator("core_api_url", "document_api_url", "learning_api_url", "llama_url")
    @classmethod
    def origin_only(cls, url: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if url and (url.username or url.password or url.path != "/" or url.query or url.fragment):
            raise ValueError("Expected an HTTP origin without credentials")
        return url

    @field_validator(
        "document_locate_path", "learning_active_assessment_path", "learning_quiz_export_path"
    )
    @classmethod
    def internal_path(cls, path: str | None) -> str | None:
        if path is None:
            return None
        parsed = urlsplit(path)
        if (
            not path.startswith("/internal/")
            or parsed.path != path
            or any(part in {"", ".", ".."} for part in path[1:].split("/"))
            or not all(c.isascii() and (c.isalnum() or c in "/_-") for c in path)
        ):
            raise ValueError("Expected a fixed /internal/... path")
        return path

    @field_validator("ai_cors_origins")
    @classmethod
    def explicit_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            normalized = str(cls.origin_only(AnyHttpUrl(origin))).rstrip("/")
            if origin != normalized:
                raise ValueError("Expected exact CORS origins without trailing slash")
        return origins

    @model_validator(mode="after")
    def check_modes(self) -> "Settings":
        if self.document_mode == "local_dev" and self.ai_environment != "local_dev":
            raise ValueError("Local Document fixtures require AI_ENVIRONMENT=local_dev")
        if self.llama_mode == "openai_compatible" and (
            self.llm_api_base is None
            or self.llm_api_key is None
            or not self.llm_api_key.get_secret_value().strip()
        ):
            raise ValueError(
                "LLM_API_BASE and LLM_API_KEY are required when LLAMA_MODE=openai_compatible"
            )
        if self.llama_mode == "local":
            origin = self.llama_url
            if (
                origin.scheme != "http"
                or origin.host not in {"localhost", "127.0.0.1", "[::1]"}
                or urlsplit(str(origin)).port is None
            ):
                raise ValueError("LLAMA_URL must be a loopback HTTP origin with an explicit port")
            if not all(
                (
                    self.llama_model_path,
                    self.llama_server_path,
                    self.llama_model_repo,
                    self.llama_model_revision,
                    self.llama_model_variant,
                    self.llama_model_sha256,
                )
            ):
                raise ValueError("Local llama mode requires pinned model and executable paths")
            if self.llama_output_ceiling + 64 >= self.llama_context_tokens:
                raise ValueError(
                    "LLAMA_CONTEXT_TOKENS is too small for the configured output ceiling"
                )
        return self
