"""add expires_at and status_reason fields; extend enums

Revision ID: 20250929_03
Revises: 20250929_02
Create Date: 2025-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20250929_03'
down_revision: Union[str, None] = '20250929_02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the enum type explicitly (Postgres requires types to exist before use)
    op.execute("CREATE TYPE statusreason AS ENUM ('awaiting_payment', 'awaiting_webhook', 'ttl_elapsed', 'superseded', 'provider_failed', 'user_cancelled')")

    # Add new columns
    op.add_column('transactions', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('transactions', sa.Column('status_reason', sa.Enum('awaiting_payment', 'awaiting_webhook', 'ttl_elapsed', 'superseded', 'provider_failed', 'user_cancelled', name='statusreason'), nullable=True))
    op.add_column('transactions', sa.Column('status_message', sa.String(), nullable=True))
    op.add_column('transactions', sa.Column('failure_code', sa.String(), nullable=True))

    # Extend transactionstatus enum with expired, canceled
    # Postgres: alter type by creating a new type and casting or using ADD VALUE if available
    op.execute("ALTER TYPE transactionstatus ADD VALUE IF NOT EXISTS 'expired'")
    op.execute("ALTER TYPE transactionstatus ADD VALUE IF NOT EXISTS 'canceled'")


def downgrade() -> None:
    # Drop added columns; can't easily remove enum values safely
    op.drop_column('transactions', 'failure_code')
    op.drop_column('transactions', 'status_message')
    op.drop_column('transactions', 'status_reason')
    op.drop_column('transactions', 'expires_at')
    # Drop the enum type
    op.execute("DROP TYPE IF EXISTS statusreason")
