"""merge heads 20250928_01 and add_plan_sku

Revision ID: mrg_20250928
Revises: 20250928_01, add_plan_sku
Create Date: 2025-09-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'mrg_20250928'
down_revision: Union[str, tuple[str, ...], None] = ('20250928_01', 'add_plan_sku')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op merge; schema state is the union of both branches
    pass


def downgrade() -> None:
    # No-op merge downgrade
    pass
