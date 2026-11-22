from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, UpdatedAt, UUIDPrimaryKey


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DraftStatus(StrEnum):
    GENERATED = "GENERATED"
    PASSED = "PASSED"
    FLAGGED = "FLAGGED"
    REJECTED_AUTO = "REJECTED_AUTO"
    ACCEPTED = "ACCEPTED"
    EDITED_ACCEPTED = "EDITED_ACCEPTED"
    REJECTED = "REJECTED"
    REGENERATE = "REGENERATE"
    EXPORT_PENDING = "EXPORT_PENDING"
    EXPORTED = "EXPORTED"
    BLOCKED = "BLOCKED"


class QuizJob(Base, UUIDPrimaryKey, CreatedAt, UpdatedAt):
    __tablename__ = "quiz_jobs"
    __table_args__ = (
        CheckConstraint(
            "completed_items >= 0 AND total_items >= completed_items", name="ck_quiz_job_progress"
        ),
        Index("ix_quiz_jobs_class_created", "school_id", "class_id", "created_at"),
        Index("ix_quiz_jobs_queue", "status", "created_at"),
    )

    school_id: Mapped[UUID]
    class_id: Mapped[UUID]
    requested_by: Mapped[UUID]
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, create_constraint=True, name="quiz_job_status"),
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    spec_json: Mapped[dict] = mapped_column(JSONB)
    fact_cache: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    completed_items: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    total_items: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(64))


class QuizItemDraft(Base, UUIDPrimaryKey, CreatedAt, UpdatedAt):
    __tablename__ = "quiz_item_drafts"
    __table_args__ = (
        UniqueConstraint("quiz_job_id", "slot_id", name="uq_quiz_item_draft_slot"),
        CheckConstraint(
            "status NOT IN ('ACCEPTED','EDITED_ACCEPTED','EXPORT_PENDING','EXPORTED') "
            "OR (reviewer_id IS NOT NULL AND item_json IS NOT NULL)",
            name="ck_quiz_approved_draft_review",
        ),
        CheckConstraint(
            "(status = 'EXPORTED') = (learning_item_id IS NOT NULL)",
            name="ck_quiz_draft_export_receipt",
        ),
    )

    quiz_job_id: Mapped[UUID] = mapped_column(ForeignKey("ai.quiz_jobs.id", ondelete="CASCADE"))
    slot_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[DraftStatus] = mapped_column(
        Enum(DraftStatus, native_enum=False, create_constraint=True, name="quiz_draft_status")
    )
    level: Mapped[str] = mapped_column(String(32))
    item_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    original_item_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    seed: Mapped[int | None]
    slot_meta_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    reviewer_id: Mapped[UUID | None]
    review_reason: Mapped[str | None] = mapped_column(String(128))
    learning_item_id: Mapped[UUID | None]
