"""Add grades, grade_revisions, appeals, and scoring_adjustments (L4).

Revision ID: 0004_grades_appeals_adjustments
Revises: 0003_assignments_uploads
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_grades_appeals_adjustments"
down_revision = "0003_assignments_uploads"
branch_labels = None
depends_on = None

SCHEMA = "learning"


def _jsonb():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


def upgrade() -> None:
    op.create_table(
        "grades",
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
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effective_revision_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("effective_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_released", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("target_type", "target_id", name="uq_grade_target"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_grades_class_student",
        "grades",
        ["school_id", "class_id", "student_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "scoring_adjustments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "activity_version_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activity_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(100), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("payload_json", _jsonb(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PROPOSED"),
        sa.Column("proposed_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_scoring_adjustments_version",
        "scoring_adjustments",
        ["activity_version_id", "status"],
        schema=SCHEMA,
    )
    op.create_table(
        "grade_revisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "grade_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.grades.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scores_by_question_json", _jsonb(), nullable=True),
        sa.Column("total_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("max_score", sa.Numeric(6, 2), nullable=False, server_default="10.0"),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("change_kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("appeal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("proposed_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("proposed_by_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("decided_by_name", sa.String(200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column(
            "adjustment_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.scoring_adjustments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("grade_id", "version", name="uq_grade_revision_version"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_grade_revisions_grade_status",
        "grade_revisions",
        ["grade_id", "status"],
        schema=SCHEMA,
    )
    op.create_table(
        "appeals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "grade_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.grades.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("question_id", sa.String(100), nullable=True),
        sa.Column("student_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="OPEN"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_appeals_class_student",
        "appeals",
        ["school_id", "class_id", "student_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("idx_appeals_class_student", table_name="appeals", schema=SCHEMA)
    op.drop_table("appeals", schema=SCHEMA)
    op.drop_index("idx_grade_revisions_grade_status", table_name="grade_revisions", schema=SCHEMA)
    op.drop_table("grade_revisions", schema=SCHEMA)
    op.drop_index("idx_scoring_adjustments_version", table_name="scoring_adjustments", schema=SCHEMA)
    op.drop_table("scoring_adjustments", schema=SCHEMA)
    op.drop_index("idx_grades_class_student", table_name="grades", schema=SCHEMA)
    op.drop_table("grades", schema=SCHEMA)
