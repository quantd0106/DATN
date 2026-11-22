from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, UpdatedAt


class IndexStatus(StrEnum):
    READY = "READY"
    FAILED = "FAILED"


class IndexedDocumentVersion(Base, UpdatedAt):
    __tablename__ = "indexed_document_versions"
    __table_args__ = (
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_indexed_document_source_hash"),
        CheckConstraint(
            "(status = 'READY' AND corpus_generation_id IS NOT NULL AND artifact_path IS NOT NULL "
            "AND error_code IS NULL) OR (status = 'FAILED' AND corpus_generation_id IS NULL "
            "AND artifact_path IS NULL AND error_code IS NOT NULL)",
            name="ck_indexed_document_publication",
        ),
    )

    document_version_id: Mapped[UUID] = mapped_column(primary_key=True)
    status: Mapped[IndexStatus] = mapped_column(
        Enum(IndexStatus, native_enum=False, create_constraint=True, name="indexed_document_status")
    )
    corpus_generation_id: Mapped[UUID | None]
    source_sha256: Mapped[str] = mapped_column(String(64))
    artifact_path: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
