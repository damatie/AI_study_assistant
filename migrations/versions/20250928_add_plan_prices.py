"""add plan_prices table

Revision ID: add_plan_prices_20250928
Revises: 
Create Date: 2025-09-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'add_plan_prices_20250928'
down_revision = 'sku_basic_to_standard'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create only if not exists
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'plan_prices' in inspector.get_table_names(schema='public'):
        return
    op.create_table(
        'plan_prices',
        sa.Column('id', sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('plan_id', sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey('plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        # Reuse existing enum type; do not attempt to recreate it
        sa.Column('provider', postgresql.ENUM('stripe', 'paystack', name='paymentprovider', create_type=False), nullable=False),
        sa.Column('price_minor', sa.Integer(), nullable=False),
        sa.Column('provider_price_id', sa.String(), nullable=True),
        sa.Column('scope_type', sa.Enum('global', 'continent', 'country', name='regionscopetype'), nullable=False, server_default='global'),
        sa.Column('scope_value', sa.String(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        'ix_plan_prices_lookup',
        'plan_prices',
        ['plan_id', 'currency', 'provider', 'scope_type', 'scope_value', 'active']
    )


def downgrade() -> None:
    try:
        op.drop_index('ix_plan_prices_lookup', table_name='plan_prices')
    except Exception:
        pass
    try:
        op.drop_table('plan_prices')
    except Exception:
        pass