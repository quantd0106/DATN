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


class QuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"


class QuestionOrigin(StrEnum):
    MANUAL = "MANUAL"
    AI = "AI"


class ActivityKind(StrEnum):
    PRACTICE = "PRACTICE"
    ASSESSMENT = "ASSESSMENT"
    ASSIGNMENT = "ASSIGNMENT"


class VersionStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PUBLISHED = "PUBLISHED"
    SUPERSEDED = "SUPERSEDED"
    CLOSED = "CLOSED"


class QuestionItem(UUIDPrimaryKey, CreatedAt, UpdatedAt, Base):
    __tablename__ = "question_items"
    __table_args__ = (
        Index("idx_question_items_class", "school_id", "class_id", "created_at"),
        UniqueConstraint("ai_draft_id", name="uq_question_items_ai_draft_id"),
    )

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    body_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    key_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rubric: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    default_points: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=1.0)
    source_ref_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default=QuestionOrigin.MANUAL)
    ai_draft_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    ai_provenance_json: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    edited_after_generation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class Activity(UUIDPrimaryKey, CreatedAt, UpdatedAt, Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index("idx_activities_class_kind", "school_id", "class_id", "kind", "updated_at"),
    )

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    topic: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    draft_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class ActivityVersion(UUIDPrimaryKey, CreatedAt, UpdatedAt, Base):
    __tablename__ = "activity_versions"
    __table_args__ = (
        UniqueConstraint("activity_id", "version_number", name="uq_activity_version_number"),
        Index("idx_activity_versions_activity", "activity_id", "status"),
    )

    activity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=VersionStatus.DRAFT)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBType, nullable=False, default=list
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    deadline_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class ActivityAudience(UpdatedAt, Base):
    __tablename__ = "activity_audience"

    activity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    assigned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class DueOverride(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "due_overrides"
    __table_args__ = (Index("idx_due_overrides_activity_student", "activity_id", "student_id"),)

    activity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    new_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    applies_to_future_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
