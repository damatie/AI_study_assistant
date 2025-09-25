"""add plan sku unique business key

Revision ID: add_plan_sku
Revises: seed_fc_plan_limits
Create Date: 2025-09-24 00:10:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_plan_sku'
down_revision: Union[str, tuple[str, ...], None] = 'seed_fc_plan_limits'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Add column nullable first for backfill
    op.add_column('plans', sa.Column('sku', sa.String(length=64), nullable=True))

    # Backfill SKU from normalized name (fallback to lowercase name with dashes)
    rows = bind.execute(sa.text("SELECT id, name FROM plans")).fetchall()
    seen = set()
    for plan_id, name in rows:
        base = (name or '').strip().lower()
        # Simple mapping for common names
        m = {
            'free': 'FREE', 'basic': 'BASIC', 'starter': 'STARTER',
            'pro': 'PRO', 'plus': 'PLUS', 'standard': 'STANDARD',
            'premium': 'PREMIUM', 'business': 'BUSINESS', 'unlimited': 'UNLIMITED',
        }
        sku = m.get(base, base.replace(' ', '-').upper() or 'PLAN')
        # Ensure uniqueness if duplicates occur
        orig = sku
        i = 1
        while sku in seen:
            sku = f"{orig}-{i}"
            i += 1
        seen.add(sku)
        bind.execute(sa.text("UPDATE plans SET sku = :sku WHERE id = :id"), {"sku": sku, "id": plan_id})

    # Enforce not-null and unique
    op.alter_column('plans', 'sku', nullable=False)
    op.create_unique_constraint('uq_plans_sku', 'plans', ['sku'])


def downgrade() -> None:
    # Drop unique and column
    op.drop_constraint('uq_plans_sku', 'plans', type_='unique')
    op.drop_column('plans', 'sku')
