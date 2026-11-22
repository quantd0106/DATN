from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, JSONBType, UpdatedAt, UUIDPrimaryKey


class AssessmentAttemptStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    AUTO_SUBMITTED = "AUTO_SUBMITTED"
    GRADED = "GRADED"
    RELEASED = "RELEASED"


class SubmitReason(StrEnum):
    MANUAL = "MANUAL"
    TIMEOUT = "TIMEOUT"


class AssessmentAttempt(UUIDPrimaryKey, Base):
    __tablename__ = "assessment_attempts"
    __table_args__ = (
        UniqueConstraint("activity_id", "student_id", "attempt_number", name="uq_assessment_attempt_no"),
        UniqueConstraint("submit_operation_id", name="uq_assessment_submit_op"),
        Index(
            "idx_assessment_active_student",
            "school_id",
            "student_id",
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
        Index(
            "idx_assessment_expiring",
            "ends_at",
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
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
    student_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AssessmentAttemptStatus.IN_PROGRESS
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    saved_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submit_operation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    submit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    receipt_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    results_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results_released_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class AttemptAnswer(UUIDPrimaryKey, UpdatedAt, Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_attempt_answer_q"),)

    attempt_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    answer_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    saved_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
