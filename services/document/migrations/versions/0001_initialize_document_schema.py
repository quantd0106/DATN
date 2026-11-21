"""Initialize the Document Service schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "document"


def uuid_column(name, *, nullable=False, foreign_key=None):
    constraints = [sa.ForeignKey(f"{SCHEMA}.{foreign_key}.id")] if foreign_key else []
    return sa.Column(name, sa.Uuid(), *constraints, nullable=nullable)


def primary_key():
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def timestamp(name, *, nullable=False):
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.func.now(),
    )


def source_columns():
    return [
        uuid_column("school_id"),
        uuid_column("user_id"),
        uuid_column("class_id"),
        uuid_column("document_id", foreign_key="documents"),
        uuid_column("document_version_id", nullable=True, foreign_key="document_versions"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("locator", sa.String(512), nullable=False),
        sa.Column("page_number", sa.Integer()),
    ]


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS document")
    op.create_table(
        "documents",
        primary_key(),
        uuid_column("school_id"),
        uuid_column("owner_user_id"),
        sa.Column("owner_display_name", sa.String(200)),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000)),
        sa.Column("format", sa.String(8), nullable=False),
        timestamp("deleted_at", nullable=True),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.CheckConstraint(
            "format IN ('PDF', 'PPTX', 'DOCX', 'TXT', 'MARKDOWN')", name="ck_documents_format"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_documents_owner",
        "documents",
        ["school_id", "owner_user_id", "deleted_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "document_versions",
        primary_key(),
        uuid_column("document_id", foreign_key="documents"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(8), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("canonical_storage_key", sa.String(512)),
        sa.Column("canonical_sha256", sa.String(64)),
        sa.Column("canonical_schema_version", sa.String(64)),
        sa.Column("page_count", sa.Integer()),
        sa.Column(
            "processing_status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")
        ),
        sa.Column("acceptance_mode", sa.String(16)),
        uuid_column("accepted_by_user_id", nullable=True),
        timestamp("accepted_at", nullable=True),
        sa.Column("review_summary_json", postgresql.JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(512)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        timestamp("available_at"),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.CheckConstraint("version_number >= 1", name="ck_document_version_number"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_document_version_size"),
        sa.CheckConstraint(
            "processing_status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'NEEDS_REVIEW', 'FAILED')",
            name="ck_document_version_status",
        ),
        sa.CheckConstraint(
            "acceptance_mode IN ('AUTO_PUBLISHED', 'OWNER_ACCEPTED')",
            name="ck_document_version_acceptance",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_document_version_claim",
        "document_versions",
        ["processing_status", "available_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_document_version_serving",
        "document_versions",
        ["document_id", "processing_status", "version_number"],
        schema=SCHEMA,
    )

    op.create_table(
        "class_document_grants",
        primary_key(),
        uuid_column("school_id"),
        uuid_column("class_id"),
        uuid_column("document_id", foreign_key="documents"),
        uuid_column("granted_by_user_id"),
        uuid_column("revoked_by_user_id", nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        timestamp("granted_at"),
        timestamp("revoked_at", nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_active_class_document_grant",
        "class_document_grants",
        ["class_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_class_document_grants_class",
        "class_document_grants",
        ["class_id", "is_active"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_class_document_grants_document",
        "class_document_grants",
        ["document_id", "is_active"],
        schema=SCHEMA,
    )

    op.create_table(
        "source_bound_notes",
        primary_key(),
        *source_columns(),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("source_quote", sa.Text()),
        timestamp("created_at"),
        timestamp("updated_at"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_source_notes_owner_source",
        "source_bound_notes",
        ["user_id", "class_id", "document_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_source_notes_owner_updated",
        "source_bound_notes",
        ["user_id", sa.text("updated_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "document_bookmarks",
        primary_key(),
        *source_columns(),
        timestamp("created_at"),
        sa.UniqueConstraint(
            "user_id", "class_id", "document_id", "locator", name="uq_document_bookmark_locator"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "outbox_events",
        primary_key(),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("event_type", sa.String(64), nullable=False),
        uuid_column("aggregate_id"),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        timestamp("available_at"),
        timestamp("published_at", nullable=True),
        sa.Column("last_error_code", sa.String(64)),
        timestamp("created_at"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_doc_outbox_unpublished",
        "outbox_events",
        ["available_at", "id"],
        postgresql_where=sa.text("published_at IS NULL"),
        schema=SCHEMA,
    )

    op.create_table(
        "audit_events",
        primary_key(),
        uuid_column("school_id"),
        uuid_column("actor_id"),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        uuid_column("target_id"),
        uuid_column("correlation_id"),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        timestamp("occurred_at"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in (
        "audit_events",
        "outbox_events",
        "document_bookmarks",
        "source_bound_notes",
        "class_document_grants",
        "document_versions",
        "documents",
    ):
        op.drop_table(table, schema=SCHEMA)
