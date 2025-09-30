"""drop price_pence from plans (migrate to plan_prices)

Revision ID: 20250928_03
Revises: 20250928_02
Create Date: 2025-09-28
"""

from alembic import op
import sqlalchemy as sa


revision = '20250928_03'
down_revision = '20250928_02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    try:
        op.execute('ALTER TABLE plans DROP COLUMN IF EXISTS price_pence')
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.add_column('plans', sa.Column('price_pence', sa.Integer(), nullable=False, server_default='0'))
        op.execute('ALTER TABLE plans ALTER COLUMN price_pence DROP DEFAULT')
    except Exception:
        pass
