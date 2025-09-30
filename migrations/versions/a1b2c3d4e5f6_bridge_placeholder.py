"""bridge placeholder

Revision ID: a1b2c3d4e5f6
Revises: 33b71525fe69
Create Date: 2025-09-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '33b71525fe69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op placeholder to bridge missing local revision
    pass


def downgrade() -> None:
    # No-op
    pass
