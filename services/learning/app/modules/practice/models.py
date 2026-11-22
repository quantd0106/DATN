from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, JSONBType, UpdatedAt, UUIDPrimaryKey


class PracticeAttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class PracticeAttempt(UUIDPrimaryKey, Base):
    """Strictly separate from official grades (Rule D8)."""

    __tablename__ = "practice_attempts"
    __table_args__ = (
        Index("idx_practice_attempts_lookup", "student_id", "class_id", "activity_id", "attempt_number"),
    )

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    activity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False
    )
    activity_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activity_versions.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PracticeAttemptStatus.IN_PROGRESS
    )
    current_question_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)


class PracticeAnswer(UUIDPrimaryKey, UpdatedAt, Base):
    __tablename__ = "practice_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_practice_answer_q"),)

    attempt_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("practice_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    answer_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    is_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
