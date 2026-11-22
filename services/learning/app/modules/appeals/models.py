from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, UpdatedAt, UUIDPrimaryKey


class AppealStatus(StrEnum):
    OPEN = "OPEN"
    PROPOSED = "PROPOSED"
    RESOLVED = "RESOLVED"


class Appeal(UUIDPrimaryKey, CreatedAt, UpdatedAt, Base):
    __tablename__ = "appeals"
    __table_args__ = (Index("idx_appeals_class_student", "school_id", "class_id", "student_id"),)

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    grade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grades.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    question_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    student_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=AppealStatus.OPEN)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
