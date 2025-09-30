"""transactions.updated_at nullable with no default

Revision ID: 20250929_02
Revises: 20250929_01
Create Date: 2025-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20250929_02'
down_revision: Union[str, None] = '20250929_01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop default and allow NULLs on updated_at
    op.execute("ALTER TABLE transactions ALTER COLUMN updated_at DROP DEFAULT;")
    op.alter_column('transactions', 'updated_at', existing_type=sa.DateTime(timezone=True), nullable=True)

    # For rows that shouldn't have an updated_at yet (still pending), set it to NULL
    op.execute("UPDATE transactions SET updated_at = NULL WHERE status = 'pending';")


def downgrade() -> None:
    # Revert to NOT NULL with default now()
    op.alter_column('transactions', 'updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.execute("ALTER TABLE transactions ALTER COLUMN updated_at SET DEFAULT now();")
