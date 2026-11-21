from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, UpdatedAt, UTCDateTime


class Document(UpdatedAt, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "format IN ('PDF', 'PPTX', 'DOCX', 'TXT', 'MARKDOWN')", name="ck_documents_format"
        ),
        Index("idx_documents_owner", "school_id", "owner_user_id", "deleted_at"),
    )

    school_id: Mapped[UUID]
    owner_user_id: Mapped[UUID]
    owner_display_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000))
    format: Mapped[str] = mapped_column(String(8))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
