"""Add assessment_attempts and attempt_answers tables with D6 and sweeper indexes.

Revision ID: 0002_assessment_attempts
Revises: 0001_initialize_learning_schema
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_assessment_attempts"
down_revision = "0001_initialize_learning_schema"
branch_labels = None
depends_on = None

SCHEMA = "learning"


def _jsonb():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


def upgrade() -> None:
    op.create_table(
        "assessment_attempts",
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
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saved_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submit_operation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("submit_reason", sa.String(16), nullable=True),
        sa.Column("receipt_json", _jsonb(), nullable=True),
        sa.Column("results_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("results_released_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.UniqueConstraint("activity_id", "student_id", "attempt_number", name="uq_assessment_attempt_no"),
        sa.UniqueConstraint("submit_operation_id", name="uq_assessment_submit_op"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_assessment_active_student",
        "assessment_attempts",
        ["school_id", "student_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )
    op.create_index(
        "idx_assessment_expiring",
        "assessment_attempts",
        ["ends_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )
    op.create_table(
        "attempt_answers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.assessment_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(100), nullable=False),
        sa.Column("answer_json", _jsonb(), nullable=False),
        sa.Column("saved_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_attempt_answer_q"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("attempt_answers", schema=SCHEMA)
    op.drop_index("idx_assessment_expiring", table_name="assessment_attempts", schema=SCHEMA)
    op.drop_index("idx_assessment_active_student", table_name="assessment_attempts", schema=SCHEMA)
    op.drop_table("assessment_attempts", schema=SCHEMA)
