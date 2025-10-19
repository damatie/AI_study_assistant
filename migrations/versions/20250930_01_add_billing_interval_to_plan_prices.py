"""add billing_interval and interval_count to plan_prices

Revision ID: 20250930_01
Revises: 
Create Date: 2025-09-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250930_01'
# Chain this migration after the latest head so it actually runs
down_revision = '20250929_03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # If plan_prices table doesn't exist yet (branch order), skip here.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'plan_prices' not in inspector.get_table_names(schema='public'):
        return
    # Add new enum type for billing_interval
    billing_enum = sa.Enum('month', 'year', name='billinginterval')
    billing_enum.create(op.get_bind(), checkfirst=True)

    # Add columns (nullable for back-compat). Default at ORM level; DB nullable here.
    op.add_column('plan_prices', sa.Column('billing_interval', billing_enum, nullable=True))
    op.add_column('plan_prices', sa.Column('interval_count', sa.Integer(), nullable=False, server_default=sa.text('1')))

    # Backfill NULL billing_interval to 'month'
    op.execute("UPDATE plan_prices SET billing_interval='month' WHERE billing_interval IS NULL")

    # Drop old index and recreate including billing_interval
    try:
        op.drop_index('ix_plan_prices_lookup', table_name='plan_prices')
    except Exception:
        pass
    op.create_index('ix_plan_prices_lookup', 'plan_prices', ['plan_id','currency','provider','scope_type','scope_value','billing_interval','active'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'plan_prices' not in inspector.get_table_names(schema='public'):
        return
    # Drop updated index, recreate without interval
    try:
        op.drop_index('ix_plan_prices_lookup', table_name='plan_prices')
    except Exception:
        pass
    op.create_index('ix_plan_prices_lookup', 'plan_prices', ['plan_id','currency','provider','scope_type','scope_value','active'], unique=False)

    # Drop columns
    op.drop_column('plan_prices', 'interval_count')
    op.drop_column('plan_prices', 'billing_interval')

    # Drop enum type
    try:
        sa.Enum(name='billinginterval').drop(op.get_bind(), checkfirst=True)
    except Exception:
        pass
