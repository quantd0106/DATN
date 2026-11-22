from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, UUIDPrimaryKey


class AssignmentUpload(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "assignment_uploads"
    __table_args__ = (Index("idx_assignment_uploads_student", "activity_id", "student_id"),)

    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    activity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("activities.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    blob_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssignmentSubmission(UUIDPrimaryKey, Base):
    __tablename__ = "assignment_submissions"
    __table_args__ = (
        UniqueConstraint("activity_id", "student_id", "submission_number", name="uq_submission_no"),
        UniqueConstraint("operation_id", name="uq_submission_operation"),
        Index("idx_assignment_submissions_class", "school_id", "class_id", "activity_id"),
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
    submission_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assignment_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    upload_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assignment_uploads.id", ondelete="RESTRICT"), nullable=False
    )
    blob_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    operation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    is_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_graded_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
