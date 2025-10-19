"""add grace period columns to subscriptions

Revision ID: grace_period_2025
Revises: 2979396d20ec
Create Date: 2025-10-13 20:28:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'grace_period_2025'
down_revision = '2979396d20ec'
branch_labels = None
depends_on = None


def upgrade():
    # Add grace period tracking columns to subscriptions table
    op.add_column('subscriptions', sa.Column('is_in_retry_period', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('subscriptions', sa.Column('retry_attempt_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('subscriptions', sa.Column('last_payment_failure_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Remove grace period columns
    op.drop_column('subscriptions', 'last_payment_failure_at')
    op.drop_column('subscriptions', 'retry_attempt_count')
    op.drop_column('subscriptions', 'is_in_retry_period')
