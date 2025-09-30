"""fix transactions.created_at default to now()

Revision ID: 4e5f8a1b2c34
Revises: 37a664a96853
Create Date: 2025-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e5f8a1b2c34'
down_revision: Union[str, None] = '37a664a96853'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ensure created_at/updated_at use server-side now() defaults.

    Some earlier migrations used string defaults like 'now()' which can compile incorrectly
    or end up as a static value. This explicitly sets Postgres defaults to now().
    """
    # Use raw SQL for portability with existing types; target Postgres functions
    op.execute("ALTER TABLE transactions ALTER COLUMN created_at SET DEFAULT now();")
    op.execute("ALTER TABLE transactions ALTER COLUMN updated_at SET DEFAULT now();")


def downgrade() -> None:
    """Revert defaults to NULL (no default)."""
    op.execute("ALTER TABLE transactions ALTER COLUMN created_at DROP DEFAULT;")
    op.execute("ALTER TABLE transactions ALTER COLUMN updated_at DROP DEFAULT;")
