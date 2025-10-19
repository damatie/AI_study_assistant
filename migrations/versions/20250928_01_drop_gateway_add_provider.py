"""drop legacy gateway column; add provider enum to transactions

Revision ID: 20250928_01
Revises: 
Create Date: 2025-09-28

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20250928_01'
down_revision = 'mrg_add_fcs'
branch_labels = None
depends_on = None


def upgrade():
    # Create enum type for provider
    provider_enum = postgresql.ENUM('stripe', 'paystack', name='paymentprovider')
    provider_enum.create(op.get_bind(), checkfirst=True)

    # Add new column
    op.add_column('transactions', sa.Column('provider', provider_enum, nullable=True))

    # Backfill provider from legacy gateway column when present
    # Use simple updates to avoid casting issues
    try:
        op.execute("UPDATE transactions SET provider = 'stripe' WHERE LOWER(gateway) = 'stripe'")
        op.execute("UPDATE transactions SET provider = 'paystack' WHERE LOWER(gateway) = 'paystack'")
    except Exception:
        # If gateway column does not exist, ignore
        pass

    # Drop legacy gateway column if it exists
    try:
        op.drop_column('transactions', 'gateway')
    except Exception:
        # Column might already be absent
        pass


def downgrade():
    # Recreate legacy gateway column as String (nullable)
    try:
        op.add_column('transactions', sa.Column('gateway', sa.String(), nullable=True))
        # Backfill from provider enum to gateway string
        op.execute("UPDATE transactions SET gateway = 'stripe' WHERE provider = 'stripe'")
        op.execute("UPDATE transactions SET gateway = 'paystack' WHERE provider = 'paystack'")
    except Exception:
        pass

    # Drop provider column and enum
    try:
        op.drop_column('transactions', 'provider')
    except Exception:
        pass

    try:
        provider_enum = postgresql.ENUM('stripe', 'paystack', name='paymentprovider')
        provider_enum.drop(op.get_bind(), checkfirst=True)
    except Exception:
        pass
