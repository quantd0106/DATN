from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import JSON_OBJECT, Base, UpdatedAt, UTCDateTime


class DocumentVersion(UpdatedAt, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        CheckConstraint("version_number >= 1", name="ck_document_version_number"),
        CheckConstraint("file_size_bytes > 0", name="ck_document_version_size"),
        CheckConstraint(
            "processing_status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'NEEDS_REVIEW', 'FAILED')",
            name="ck_document_version_status",
        ),
        CheckConstraint(
            "acceptance_mode IN ('AUTO_PUBLISHED', 'OWNER_ACCEPTED')",
            name="ck_document_version_acceptance",
        ),
        Index("idx_document_version_claim", "processing_status", "available_at"),
        Index("idx_document_version_serving", "document_id", "processing_status", "version_number"),
    )

    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.documents.id"))
    version_number: Mapped[int]
    original_filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8))
    mime_type: Mapped[str] = mapped_column(String(128))
    file_size_bytes: Mapped[int]
    source_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    canonical_storage_key: Mapped[str | None] = mapped_column(String(512))
    canonical_sha256: Mapped[str | None] = mapped_column(String(64))
    canonical_schema_version: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None]
    processing_status: Mapped[str] = mapped_column(String(16), server_default=text("'PENDING'"))
    acceptance_mode: Mapped[str | None] = mapped_column(String(16))
    accepted_by_user_id: Mapped[UUID | None]
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review_summary_json: Mapped[dict | None] = mapped_column(JSON_OBJECT)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    attempt_count: Mapped[int] = mapped_column(server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
