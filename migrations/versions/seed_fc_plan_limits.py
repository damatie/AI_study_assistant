"""seed flash cards limits for existing plans

Revision ID: seed_fc_plan_limits
Revises: add_fc_plan_limits
Create Date: 2025-09-23 00:20:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'seed_fc_plan_limits'
down_revision: Union[str, tuple[str, ...], None] = 'add_fc_plan_limits'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Group names heuristics
    free_names = {'free', 'basic', 'starter'}
    pro_names = {'pro', 'plus', 'standard'}
    premium_names = {'premium', 'business', 'unlimited'}

    # Fetch existing plans
    plans = bind.execute(sa.text("SELECT id, name FROM plans")).fetchall()

    for plan_id, name in plans:
        n = (name or '').strip().lower()
        if n in free_names:
            m_limit = 2
            per_deck = 12
        elif n in pro_names:
            m_limit = 20
            per_deck = 40
        elif n in premium_names:
            m_limit = 0   # unlimited
            per_deck = 40
        else:
            # Sensible fallback for unknown names
            m_limit = 5
            per_deck = 20

        bind.execute(
            sa.text(
                """
                UPDATE plans
                SET monthly_flash_cards_limit = :m_limit,
                    max_cards_per_deck = :per_deck
                WHERE id = :id
                """
            ),
            {"m_limit": m_limit, "per_deck": per_deck, "id": plan_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Revert seeded values back to 0 (unlimited/disabled) to undo opinionated seeding
    bind.execute(sa.text(
        "UPDATE plans SET monthly_flash_cards_limit = 0, max_cards_per_deck = 0"
    ))
