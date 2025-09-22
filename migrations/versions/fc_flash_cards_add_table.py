"""add flash card sets table

Revision ID: fc_flash_cards_add_table
Revises: 33b71525fe69
Create Date: 2025-09-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


# revision identifiers, used by Alembic.
revision: str = 'fc_flash_cards_add_table'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'flash_card_sets' in insp.get_table_names():
        # Table already exists; no-op (likely from previous local run)
        return
    op.create_table(
        'flash_card_sets',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('material_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('topic', sa.String(), nullable=True),
        sa.Column('difficulty', PG_ENUM('easy', 'medium', 'hard', name='difficulty', create_type=False), nullable=False),
        sa.Column('cards_payload', sa.JSON(), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')), 
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['material_id'], ['study_materials.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    # Use IF EXISTS to avoid errors if table was manually removed
    op.execute('DROP TABLE IF EXISTS flash_card_sets CASCADE')
