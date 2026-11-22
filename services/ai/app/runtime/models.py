from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, UUIDPrimaryKey


class GenerationProvenance(Base, UUIDPrimaryKey, CreatedAt):
    __tablename__ = "generation_provenance"
    __table_args__ = (
        CheckConstraint(
            "attempt >= 1 AND (seed IS NULL OR seed >= 0)", name="ck_provenance_attempt"
        ),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0)"
            " AND (completion_tokens IS NULL OR completion_tokens >= 0)"
            " AND (waited_ms IS NULL OR waited_ms >= 0) AND (latency_ms IS NULL OR latency_ms >= 0)",
            name="ck_provenance_counters",
        ),
        CheckConstraint("jsonb_typeof(flags_json) = 'array'", name="ck_provenance_flags"),
        CheckConstraint(
            "prompt_sha256 ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$'"
            " AND (raw_response_sha256 IS NULL OR raw_response_sha256 ~ '^[0-9a-f]{64}$')"
            " AND (model_sha256 IS NULL OR model_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_provenance_hashes",
        ),
    )

    task_name: Mapped[str] = mapped_column(String(64))
    task_version: Mapped[str] = mapped_column(String(64))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    raw_response_sha256: Mapped[str | None] = mapped_column(String(64))
    model_sha256: Mapped[str | None] = mapped_column(String(64))
    seed: Mapped[int | None]
    attempt: Mapped[int]
    outcome: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    flags_json: Mapped[list[str]] = mapped_column(JSONB)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    waited_ms: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
