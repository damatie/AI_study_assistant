"""add flash cards plan limits and usage counter

Revision ID: add_fc_plan_limits
Revises: merge_37a_and_add_flash_card_status
Create Date: 2025-09-23 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = 'add_fc_plan_limits'
down_revision: Union[str, tuple[str, ...], None] = 'mrg_add_fcs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, name: str) -> bool:
    insp = Inspector.from_engine(bind)
    return name in insp.get_table_names()


def _column_exists(bind, table: str, column: str) -> bool:
    insp = Inspector.from_engine(bind)
    return column in [c['name'] for c in insp.get_columns(table)]


def upgrade() -> None:
    bind = op.get_bind()

    # Add plan limits for flash cards
    if _table_exists(bind, 'plans') and not _column_exists(bind, 'plans', 'monthly_flash_cards_limit'):
        op.add_column('plans', sa.Column('monthly_flash_cards_limit', sa.Integer(), nullable=False, server_default='0'))
        op.alter_column('plans', 'monthly_flash_cards_limit', server_default=None)

    if _table_exists(bind, 'plans') and not _column_exists(bind, 'plans', 'max_cards_per_deck'):
        op.add_column('plans', sa.Column('max_cards_per_deck', sa.Integer(), nullable=False, server_default='0'))
        op.alter_column('plans', 'max_cards_per_deck', server_default=None)

    # Add usage counter for flash card sets
    if _table_exists(bind, 'usage_tracking') and not _column_exists(bind, 'usage_tracking', 'flash_card_sets_count'):
        op.add_column('usage_tracking', sa.Column('flash_card_sets_count', sa.Integer(), nullable=False, server_default='0'))
        op.alter_column('usage_tracking', 'flash_card_sets_count', server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, 'usage_tracking') and _column_exists(bind, 'usage_tracking', 'flash_card_sets_count'):
        op.drop_column('usage_tracking', 'flash_card_sets_count')

    if _table_exists(bind, 'plans') and _column_exists(bind, 'plans', 'max_cards_per_deck'):
        op.drop_column('plans', 'max_cards_per_deck')

    if _table_exists(bind, 'plans') and _column_exists(bind, 'plans', 'monthly_flash_cards_limit'):
        op.drop_column('plans', 'monthly_flash_cards_limit')
