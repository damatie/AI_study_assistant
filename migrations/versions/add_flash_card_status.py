"""add flash_card_status column

Revision ID: add_flash_card_status
Revises: 
Create Date: 2025-09-23
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_flash_card_status'
down_revision = 'fc_flash_cards_add_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    enum_type = sa.Enum('processing', 'completed', 'failed', name='flash_card_status')
    enum_type.create(bind, checkfirst=True)
    if 'flash_card_sets' in insp.get_table_names():
        cols = [c['name'] for c in insp.get_columns('flash_card_sets')]
        if 'status' not in cols:
            op.add_column('flash_card_sets', sa.Column('status', enum_type, nullable=False, server_default='processing'))
            # drop server_default after backfilling
            op.alter_column('flash_card_sets', 'status', server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'flash_card_sets' in insp.get_table_names():
        cols = [c['name'] for c in insp.get_columns('flash_card_sets')]
        if 'status' in cols:
            op.drop_column('flash_card_sets', 'status')
    enum_type = sa.Enum('processing', 'completed', 'failed', name='flash_card_status')
    enum_type.drop(bind, checkfirst=True)
