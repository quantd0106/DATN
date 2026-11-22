"""Initialize learning schema, infrastructure tables, question bank, activities, and practice.

Revision ID: 0001_initialize_learning_schema
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initialize_learning_schema"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "learning"


def _jsonb():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body_json", _jsonb(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=True),
        sa.Column("payload_json", _jsonb(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_learning_outbox_pending",
        "outbox_events",
        ["status", "next_retry_at", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_roles_json", _jsonb(), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("metadata_json", _jsonb(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_learning_audit_scope",
        "audit_events",
        ["school_id", "class_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "question_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("body_json", _jsonb(), nullable=False),
        sa.Column("key_json", _jsonb(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("rubric", sa.Text(), nullable=False, server_default=""),
        sa.Column("topic", sa.String(200), nullable=False, server_default=""),
        sa.Column("default_points", sa.Numeric(6, 2), nullable=False, server_default="1.0"),
        sa.Column("source_ref_json", _jsonb(), nullable=True),
        sa.Column("origin", sa.String(16), nullable=False, server_default="MANUAL"),
        sa.Column("ai_draft_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("ai_provenance_json", _jsonb(), nullable=True),
        sa.Column("edited_after_generation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ai_draft_id", name="uq_question_items_ai_draft_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_question_items_class",
        "question_items",
        ["school_id", "class_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "activities",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("school_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("class_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("topic", sa.String(200), nullable=False, server_default=""),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("current_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("draft_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_activities_class_kind",
        "activities",
        ["school_id", "class_id", "kind", "updated_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "activity_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "activity_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_snapshot_json", _jsonb(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("settings_json", _jsonb(), nullable=False),
        sa.Column("deadline_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proposed_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("activity_id", "version_number", name="uq_activity_version_number"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_activity_versions_activity",
        "activity_versions",
        ["activity_id", "status"],
        schema=SCHEMA,
    )
    op.create_table(
        "practice_attempts",
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
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("current_question_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_percentage", sa.Numeric(5, 2), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_practice_attempts_lookup",
        "practice_attempts",
        ["student_id", "class_id", "activity_id", "attempt_number"],
        schema=SCHEMA,
    )
    op.create_table(
        "practice_answers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.practice_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(100), nullable=False),
        sa.Column("answer_json", _jsonb(), nullable=False),
        sa.Column("is_checked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("feedback_json", _jsonb(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("attempt_id", "question_id", name="uq_practice_answer_q"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("practice_answers", schema=SCHEMA)
    op.drop_index("idx_practice_attempts_lookup", table_name="practice_attempts", schema=SCHEMA)
    op.drop_table("practice_attempts", schema=SCHEMA)
    op.drop_index("idx_activity_versions_activity", table_name="activity_versions", schema=SCHEMA)
    op.drop_table("activity_versions", schema=SCHEMA)
    op.drop_index("idx_activities_class_kind", table_name="activities", schema=SCHEMA)
    op.drop_table("activities", schema=SCHEMA)
    op.drop_index("idx_question_items_class", table_name="question_items", schema=SCHEMA)
    op.drop_table("question_items", schema=SCHEMA)
    op.drop_index("idx_learning_audit_scope", table_name="audit_events", schema=SCHEMA)
    op.drop_table("audit_events", schema=SCHEMA)
    op.drop_index("idx_learning_outbox_pending", table_name="outbox_events", schema=SCHEMA)
    op.drop_table("outbox_events", schema=SCHEMA)
    op.drop_table("idempotency_records", schema=SCHEMA)
