"""placeholder bridge revision

Revision ID: 37a664a96853
Revises: 33b71525fe69
Create Date: 2025-09-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '37a664a96853'
down_revision: Union[str, None] = '33b71525fe69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op bridge revision to align database state
    pass


def downgrade() -> None:
    # No-op downgrade corresponding to the upgrade
    pass
