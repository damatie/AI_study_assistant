"""merge heads before deploy

Revision ID: ef6b102dc261
Revises: 20250929_03, 20250930_01
Create Date: 2025-09-30 13:48:00.130381

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef6b102dc261'
down_revision: Union[str, None] = ('20250929_03', '20250930_01')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
