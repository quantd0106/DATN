"""Add slot metadata column on quiz item drafts for single-item regeneration.

Revision ID: 0002_ai_quiz_slot_meta
Revises: 0001_ai
Create Date: 2026-11-22 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_ai_quiz_slot_meta"
down_revision = "0001_ai"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quiz_item_drafts",
        sa.Column(
            "slot_meta_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        schema="ai",
    )


def downgrade() -> None:
    op.drop_column("quiz_item_drafts", "slot_meta_json", schema="ai")
