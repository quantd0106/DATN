"""Record the resource type of an outbox aggregate.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbox_events", sa.Column("aggregate_type", sa.String(64), nullable=True), schema="core"
    )


def downgrade() -> None:
    op.drop_column("outbox_events", "aggregate_type", schema="core")
