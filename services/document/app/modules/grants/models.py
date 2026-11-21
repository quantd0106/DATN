from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, UTCDateTime


class ClassDocumentGrant(Base):
    __tablename__ = "class_document_grants"
    __table_args__ = (
        Index(
            "uq_active_class_document_grant",
            "class_id",
            "document_id",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = true"),
        ),
        Index("idx_class_document_grants_class", "class_id", "is_active"),
        Index("idx_class_document_grants_document", "document_id", "is_active"),
    )

    school_id: Mapped[UUID]
    class_id: Mapped[UUID]
    document_id: Mapped[UUID] = mapped_column(ForeignKey("document.documents.id"))
    granted_by_user_id: Mapped[UUID]
    revoked_by_user_id: Mapped[UUID | None]
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
