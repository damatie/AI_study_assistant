"""change_subscription_period_fields_to_timestamp

Revision ID: 2979396d20ec
Revises: 499ecdad32fc
Create Date: 2025-10-13 19:17:53.274216

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2979396d20ec'
down_revision: Union[str, None] = '499ecdad32fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Change period_start and period_end from DATE to TIMESTAMP WITH TIME ZONE."""
    # Change column type from DATE to TIMESTAMP WITH TIME ZONE
    # Note: Existing DATE values will be automatically converted to midnight UTC
    op.alter_column('subscriptions', 'period_start',
                    existing_type=sa.Date(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False)
    
    op.alter_column('subscriptions', 'period_end',
                    existing_type=sa.Date(),
                    type_=sa.DateTime(timezone=True),
                    existing_nullable=False)


def downgrade() -> None:
    """Revert period_start and period_end back to DATE."""
    # Note: This will truncate time information
    op.alter_column('subscriptions', 'period_start',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.Date(),
                    existing_nullable=False)
    
    op.alter_column('subscriptions', 'period_end',
                    existing_type=sa.DateTime(timezone=True),
                    type_=sa.Date(),
                    existing_nullable=False)
