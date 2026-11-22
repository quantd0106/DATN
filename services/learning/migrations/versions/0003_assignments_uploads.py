"""Add audience, due_overrides, assignment_uploads, and assignment_submissions.

Revision ID: 0003_assignments_uploads
Revises: 0002_assessment_attempts
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_assignments_uploads"
down_revision = "0002_assessment_attempts"
branch_labels = None
depends_on = None

SCHEMA = "learning"


def upgrade() -> None:
    op.create_table(
        "activity_audience",
        sa.Column(
            "activity_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("student_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("assigned", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "due_overrides",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "activity_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("student_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("new_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("applies_to_future_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_due_overrides_activity_student",
        "due_overrides",
        ["activity_id", "student_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "assignment_uploads",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "activity_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("student_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("blob_ref", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assignment_uploads_student",
        "assignment_uploads",
        ["activity_id", "student_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "assignment_submissions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "activity_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activity_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activity_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("student_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("student_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("submission_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("assignment_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "upload_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.assignment_uploads.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("blob_ref", sa.String(500), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("operation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("is_late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_graded_version", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("activity_id", "student_id", "submission_number", name="uq_submission_no"),
        sa.UniqueConstraint("operation_id", name="uq_submission_operation"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assignment_submissions_class",
        "assignment_submissions",
        ["school_id", "class_id", "activity_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("idx_assignment_submissions_class", table_name="assignment_submissions", schema=SCHEMA)
    op.drop_table("assignment_submissions", schema=SCHEMA)
    op.drop_index("idx_assignment_uploads_student", table_name="assignment_uploads", schema=SCHEMA)
    op.drop_table("assignment_uploads", schema=SCHEMA)
    op.drop_index("idx_due_overrides_activity_student", table_name="due_overrides", schema=SCHEMA)
    op.drop_table("due_overrides", schema=SCHEMA)
    op.drop_table("activity_audience", schema=SCHEMA)
