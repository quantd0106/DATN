from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, UpdatedAt


class SourceBinding:
    school_id: Mapped[UUID]
    user_id: Mapped[UUID]
    class_id: Mapped[UUID]
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.documents.id"))
    document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document.document_versions.id")
    )
    version_number: Mapped[int]
    locator: Mapped[str] = mapped_column(String(512))
    page_number: Mapped[int | None]


class SourceBoundNote(SourceBinding, UpdatedAt, Base):
    __tablename__ = "source_bound_notes"
    __table_args__ = (
        Index("idx_source_notes_owner_source", "user_id", "class_id", "document_id"),
        Index("idx_source_notes_owner_updated", "user_id", text("updated_at DESC")),
    )

    note_text: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str | None] = mapped_column(Text)


class DocumentBookmark(SourceBinding, CreatedAt, Base):
    __tablename__ = "document_bookmarks"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "class_id", "document_id", "locator", name="uq_document_bookmark_locator"
        ),
    )
