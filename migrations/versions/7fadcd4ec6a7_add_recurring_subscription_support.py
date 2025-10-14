"""add_recurring_subscription_support

Revision ID: 7fadcd4ec6a7
Revises: c878d27f52ca
Create Date: 2025-10-09 11:22:23.978445

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7fadcd4ec6a7'
down_revision: Union[str, None] = 'c878d27f52ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema to support recurring subscriptions."""
    
    # 1. Create TransactionType enum
    transaction_type_enum = sa.Enum('initial', 'recurring', 'refund', name='transactiontype')
    transaction_type_enum.create(op.get_bind(), checkfirst=True)
    
    # 2. Add columns to subscriptions table
    # Note: 'billinginterval' enum already exists from previous migration
    op.add_column('subscriptions', sa.Column('stripe_subscription_id', sa.String(), nullable=True))
    op.add_column('subscriptions', sa.Column('stripe_customer_id', sa.String(), nullable=True))
    op.add_column('subscriptions', sa.Column('paystack_subscription_code', sa.String(), nullable=True))
    op.add_column('subscriptions', sa.Column('paystack_customer_code', sa.String(), nullable=True))
    op.add_column('subscriptions', sa.Column('billing_interval', 
                                              sa.Enum('month', 'year', name='billinginterval'), 
                                              nullable=True, 
                                              server_default='month'))
    op.add_column('subscriptions', sa.Column('auto_renew', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('subscriptions', sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True))
    
    # Create indexes for provider subscription IDs
    op.create_index('ix_subscriptions_stripe_subscription_id', 'subscriptions', ['stripe_subscription_id'])
    op.create_index('ix_subscriptions_stripe_customer_id', 'subscriptions', ['stripe_customer_id'])
    op.create_index('ix_subscriptions_paystack_subscription_code', 'subscriptions', ['paystack_subscription_code'])
    op.create_index('ix_subscriptions_paystack_customer_code', 'subscriptions', ['paystack_customer_code'])
    
    # 3. Add columns to transactions table
    op.add_column('transactions', sa.Column('stripe_invoice_id', sa.String(), nullable=True))
    op.add_column('transactions', sa.Column('stripe_charge_id', sa.String(), nullable=True))
    op.add_column('transactions', sa.Column('transaction_type', 
                                             sa.Enum('initial', 'recurring', 'refund', name='transactiontype'), 
                                             nullable=False, 
                                             server_default='initial'))
    
    # Create index for stripe_invoice_id
    op.create_index('ix_transactions_stripe_invoice_id', 'transactions', ['stripe_invoice_id'])


def downgrade() -> None:
    """Downgrade schema."""
    
    # 1. Drop indexes
    op.drop_index('ix_transactions_stripe_invoice_id', table_name='transactions')
    op.drop_index('ix_subscriptions_paystack_customer_code', table_name='subscriptions')
    op.drop_index('ix_subscriptions_paystack_subscription_code', table_name='subscriptions')
    op.drop_index('ix_subscriptions_stripe_customer_id', table_name='subscriptions')
    op.drop_index('ix_subscriptions_stripe_subscription_id', table_name='subscriptions')
    
    # 2. Drop columns from transactions
    op.drop_column('transactions', 'transaction_type')
    op.drop_column('transactions', 'stripe_charge_id')
    op.drop_column('transactions', 'stripe_invoice_id')
    
    # 3. Drop columns from subscriptions
    op.drop_column('subscriptions', 'canceled_at')
    op.drop_column('subscriptions', 'auto_renew')
    op.drop_column('subscriptions', 'billing_interval')
    op.drop_column('subscriptions', 'paystack_customer_code')
    op.drop_column('subscriptions', 'paystack_subscription_code')
    op.drop_column('subscriptions', 'stripe_customer_id')
    op.drop_column('subscriptions', 'stripe_subscription_id')
    
    # 4. Drop TransactionType enum
    sa.Enum(name='transactiontype').drop(op.get_bind(), checkfirst=True)
