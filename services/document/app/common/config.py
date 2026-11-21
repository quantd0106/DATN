from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    database_url: str = Field(
        default="postgresql+psycopg://document@localhost:5432/document",
        validation_alias=AliasChoices("DATABASE_URL", "DOCUMENT_DATABASE_URL"),
    )
    core_api_url: str | None = None
    internal_service_secret: SecretStr | None = None
    document_storage_path: Path = Path("./storage")
    document_max_size_bytes: int = Field(default=20_971_520, gt=0)
    document_max_pages: int = Field(default=300, gt=0)
    idp_package_path: Path | None = None
    document_worker_lease_seconds: int = Field(default=300, gt=0)
    document_worker_poll_seconds: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    document_worker_max_attempts: int = Field(default=3, ge=1)
    core_client_timeout_seconds: float = Field(default=3.0, gt=0)
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("database_url")
    @classmethod
    def use_psycopg(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


Config = Annotated[Settings, Depends(get_settings)]
