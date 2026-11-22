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
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, JSONBType, UpdatedAt, UUIDPrimaryKey


class GradeTargetType(StrEnum):
    ASSESSMENT_ATTEMPT = "ASSESSMENT_ATTEMPT"
    ASSIGNMENT_SUBMISSION = "ASSIGNMENT_SUBMISSION"


class GradeRevisionStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class GradeChangeKind(StrEnum):
    INITIAL_AUTO = "INITIAL_AUTO"
    INITIAL_TEACHER = "INITIAL_TEACHER"
    CORRECTION_PROPOSAL = "CORRECTION_PROPOSAL"
    SCORING_ADJUSTMENT = "SCORING_ADJUSTMENT"
    ADMIN_EXCEPTION = "ADMIN_EXCEPTION"


class AdjustmentAction(StrEnum):
    VOID_QUESTION = "VOID_QUESTION"
    GRANT_FULL_CREDIT = "GRANT_FULL_CREDIT"
    ACCEPT_ADDITIONAL_KEY = "ACCEPT_ADDITIONAL_KEY"


class Grade(UUIDPrimaryKey, CreatedAt, UpdatedAt, Base):
    """Official grades only — Practice attempts never enter this table (Rule D8)."""

    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", name="uq_grade_target"),
        Index("idx_grades_class_student", "school_id", "class_id", "student_id"),
    )

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    activity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    effective_revision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    effective_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_released: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScoringAdjustment(UUIDPrimaryKey, CreatedAt, Base):
    """Rule M4: adjust scoring for a flawed published question without mutating the version."""

    __tablename__ = "scoring_adjustments"
    __table_args__ = (Index("idx_scoring_adjustments_version", "activity_version_id", "status"),)

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    activity_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activity_versions.id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=GradeRevisionStatus.PROPOSED)
    proposed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class GradeRevision(UUIDPrimaryKey, Base):
    __tablename__ = "grade_revisions"
    __table_args__ = (
        UniqueConstraint("grade_id", "version", name="uq_grade_revision_version"),
        Index("idx_grade_revisions_grade_status", "grade_id", "status"),
    )

    grade_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("grades.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scores_by_question_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    total_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    max_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=10.0)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    change_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    appeal_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    proposed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    proposed_by_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decided_by_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    adjustment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("scoring_adjustments.id", ondelete="SET NULL"), nullable=True
    )
