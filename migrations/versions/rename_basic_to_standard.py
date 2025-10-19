"""rename Basic plan to Standard (name only)

Revision ID: rename_basic_to_standard
Revises: mrg_20250928
Create Date: 2025-09-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'rename_basic_to_standard'
down_revision: Union[str, tuple[str, ...], None] = 'mrg_20250928'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Update any plan named 'Basic' (case-insensitive) to 'Standard'. Keep SKU (BASIC) unchanged.
    bind.execute(
        sa.text("""
            UPDATE plans
            SET name = 'Standard'
            WHERE lower(name) = 'basic'
        """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("""
            UPDATE plans
            SET name = 'Basic'
            WHERE lower(name) = 'standard' AND sku = 'BASIC'
        """)
    )
