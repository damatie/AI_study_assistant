"""merge heads 37a664a96853 and add_flash_card_status

Revision ID: mrg_add_fcs
Revises: 37a664a96853, add_flash_card_status
Create Date: 2025-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'mrg_add_fcs'
down_revision: Union[str, tuple[str, ...], None] = ('37a664a96853', 'add_flash_card_status')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op merge; schema state remains as-is
    pass


def downgrade() -> None:
    # No-op downgrade corresponding to merge
    pass
