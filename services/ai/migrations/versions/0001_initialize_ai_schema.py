"""Initialize the independent AI schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_ai"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "default_mode",
            sa.Enum(
                "QUICK_QA",
                "STEP_BY_STEP",
                name="conversation_mode",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            nullable=False,
        ),
        sa.Column(
            "step_session_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="ai",
    )
    op.create_index(
        "ix_conversations_owner_class",
        "conversations",
        ["school_id", "user_id", "class_id"],
        unique=False,
        schema="ai",
    )
    op.create_table(
        "generation_provenance",
        sa.Column("task_name", sa.String(length=64), nullable=False),
        sa.Column("task_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_response_sha256", sa.String(length=64), nullable=True),
        sa.Column("model_sha256", sa.String(length=64), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("flags_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("waited_ms", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(flags_json) = 'array'", name="ck_provenance_flags"),
        sa.CheckConstraint(
            "prompt_sha256 ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$' AND (raw_response_sha256 IS NULL OR raw_response_sha256 ~ '^[0-9a-f]{64}$') AND (model_sha256 IS NULL OR model_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_provenance_hashes",
        ),
        sa.CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0) AND (waited_ms IS NULL OR waited_ms >= 0) AND (latency_ms IS NULL OR latency_ms >= 0)",
            name="ck_provenance_counters",
        ),
        sa.CheckConstraint(
            "attempt >= 1 AND (seed IS NULL OR seed >= 0)", name="ck_provenance_attempt"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="ai",
    )
    op.create_table(
        "indexed_document_versions",
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "READY",
                "FAILED",
                name="indexed_document_status",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            nullable=False,
        ),
        sa.Column("corpus_generation_id", sa.Uuid(), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'READY' AND corpus_generation_id IS NOT NULL AND artifact_path IS NOT NULL AND error_code IS NULL) OR (status = 'FAILED' AND corpus_generation_id IS NULL AND artifact_path IS NULL AND error_code IS NOT NULL)",
            name="ck_indexed_document_publication",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_indexed_document_source_hash"
        ),
        sa.PrimaryKeyConstraint("document_version_id"),
        schema="ai",
    )
    op.create_table(
        "quiz_jobs",
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="quiz_job_status",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column("spec_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "fact_cache",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("completed_items", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_items", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "completed_items >= 0 AND total_items >= completed_items", name="ck_quiz_job_progress"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="ai",
    )
    op.create_index(
        "ix_quiz_jobs_class_created",
        "quiz_jobs",
        ["school_id", "class_id", "created_at"],
        unique=False,
        schema="ai",
    )
    op.create_index(
        "ix_quiz_jobs_queue", "quiz_jobs", ["status", "created_at"], unique=False, schema="ai"
    )
    op.create_table(
        "tutor_configs",
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("allow_general_knowledge", sa.Boolean(), nullable=False),
        sa.Column("hint_reveal_after", sa.Integer(), nullable=False),
        sa.Column("allow_solution_reveal", sa.Boolean(), nullable=False),
        sa.Column("guidance_level", sa.String(length=8), nullable=False),
        sa.Column("max_answer_words", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "guidance_level IN ('light', 'balanced', 'strong')", name="ck_tutor_config_guidance"
        ),
        sa.CheckConstraint("hint_reveal_after BETWEEN 1 AND 10", name="ck_tutor_config_hint"),
        sa.CheckConstraint(
            "max_answer_words BETWEEN 40 AND 300", name="ck_tutor_config_word_limit"
        ),
        sa.CheckConstraint("version >= 1", name="ck_tutor_config_version"),
        sa.PrimaryKeyConstraint("class_id"),
        schema="ai",
    )
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "USER",
                "ASSISTANT",
                name="message_role",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.Enum(
                "QUICK_QA",
                "STEP_BY_STEP",
                name="message_mode",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "STREAMING",
                "COMPLETED",
                "STOPPED",
                "FAILED",
                name="message_status",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "rendered_payload_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("knowledge_type", sa.String(length=32), nullable=True),
        sa.Column("step_number", sa.Integer(), nullable=True),
        sa.Column("total_steps", sa.Integer(), nullable=True),
        sa.Column("pedagogical_hint", sa.Text(), nullable=True),
        sa.Column("retry_of_message_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(role = 'ASSISTANT' OR status = 'COMPLETED')", name="ck_message_user_completed"
        ),
        sa.CheckConstraint(
            "(step_number IS NULL OR step_number >= 1) AND (total_steps IS NULL OR total_steps >= 1) AND (step_number IS NULL OR total_steps IS NULL OR step_number <= total_steps)",
            name="ck_message_step_bounds",
        ),
        sa.CheckConstraint(
            "retry_of_message_id IS DISTINCT FROM id", name="ck_message_retry_target"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "retry_of_message_id"],
            ["ai.messages.conversation_id", "ai.messages.id"],
            name="fk_message_retry_same_conversation",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai.conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "id", name="uq_messages_conversation_id"),
        schema="ai",
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at", "id"],
        unique=False,
        schema="ai",
    )
    op.create_table(
        "quiz_item_drafts",
        sa.Column("quiz_job_id", sa.Uuid(), nullable=False),
        sa.Column("slot_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "GENERATED",
                "PASSED",
                "FLAGGED",
                "REJECTED_AUTO",
                "ACCEPTED",
                "EDITED_ACCEPTED",
                "REJECTED",
                "REGENERATE",
                "EXPORT_PENDING",
                "EXPORTED",
                "BLOCKED",
                name="quiz_draft_status",
                native_enum=False,
                create_constraint=True,
                schema="ai",
            ),
            nullable=False,
        ),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column(
            "item_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "original_item_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("review_reason", sa.String(length=128), nullable=True),
        sa.Column("learning_item_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'EXPORTED') = (learning_item_id IS NOT NULL)",
            name="ck_quiz_draft_export_receipt",
        ),
        sa.CheckConstraint(
            "status NOT IN ('ACCEPTED','EDITED_ACCEPTED','EXPORT_PENDING','EXPORTED') OR (reviewer_id IS NOT NULL AND item_json IS NOT NULL)",
            name="ck_quiz_approved_draft_review",
        ),
        sa.ForeignKeyConstraint(["quiz_job_id"], ["ai.quiz_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quiz_job_id", "slot_id", name="uq_quiz_item_draft_slot"),
        schema="ai",
    )


def downgrade() -> None:
    op.drop_table("quiz_item_drafts", schema="ai")
    op.drop_index("ix_messages_conversation_created", table_name="messages", schema="ai")
    op.drop_table("messages", schema="ai")
    op.drop_table("tutor_configs", schema="ai")
    op.drop_index("ix_quiz_jobs_queue", table_name="quiz_jobs", schema="ai")
    op.drop_index("ix_quiz_jobs_class_created", table_name="quiz_jobs", schema="ai")
    op.drop_table("quiz_jobs", schema="ai")
    op.drop_table("indexed_document_versions", schema="ai")
    op.drop_table("generation_provenance", schema="ai")
    op.drop_index("ix_conversations_owner_class", table_name="conversations", schema="ai")
    op.drop_table("conversations", schema="ai")
