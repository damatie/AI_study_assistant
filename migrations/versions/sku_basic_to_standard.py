"""update BASIC SKU to STANDARD for Standard plan

Revision ID: sku_basic_to_standard
Revises: rename_basic_to_standard
Create Date: 2025-09-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'sku_basic_to_standard'
down_revision: Union[str, tuple[str, ...], None] = 'rename_basic_to_standard'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
            UPDATE plans
            SET sku = 'STANDARD'
            WHERE sku = 'BASIC'
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
            UPDATE plans
            SET sku = 'BASIC'
            WHERE sku = 'STANDARD'
        """)
    )
