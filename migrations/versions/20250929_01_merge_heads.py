"""merge heads 20250928_03 and 4e5f8a1b2c34

Revision ID: 20250929_01
Revises: 20250928_03, 4e5f8a1b2c34
Create Date: 2025-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20250929_01'
down_revision: Union[str, None] = ('20250928_03', '4e5f8a1b2c34')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge only; no schema changes.
    pass


def downgrade() -> None:
    # Cannot un-merge cleanly; no-op.
    pass
