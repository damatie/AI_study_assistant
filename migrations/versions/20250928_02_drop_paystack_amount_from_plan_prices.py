"""drop paystack_amount column from plan_prices

Revision ID: 20250928_02
Revises: add_plan_prices_20250928
Create Date: 2025-09-28
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250928_02'
down_revision = 'add_plan_prices_20250928'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop column if it exists (idempotent on Postgres via IF EXISTS)
    try:
        op.execute('ALTER TABLE plan_prices DROP COLUMN IF EXISTS paystack_amount')
    except Exception:
        # Some engines or Alembic versions may not support IF EXISTS; ignore
        pass


def downgrade() -> None:
    # Recreate the column as nullable integer for downgrade paths
    try:
        op.add_column('plan_prices', sa.Column('paystack_amount', sa.Integer(), nullable=True))
    except Exception:
        pass
