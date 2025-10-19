"""add_cascade_delete_to_flash_card_sets

Revision ID: c878d27f52ca
Revises: ef6b102dc261
Create Date: 2025-10-06 15:57:25.342745

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c878d27f52ca'
down_revision: Union[str, None] = 'ef6b102dc261'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add CASCADE delete to flash_card_sets.material_id foreign key."""
    # Drop the existing foreign key constraint
    op.drop_constraint(
        'flash_card_sets_material_id_fkey',
        'flash_card_sets',
        type_='foreignkey'
    )
    
    # Recreate it with ON DELETE CASCADE
    op.create_foreign_key(
        'flash_card_sets_material_id_fkey',
        'flash_card_sets',
        'study_materials',
        ['material_id'],
        ['id'],
        ondelete='CASCADE'
    )


def downgrade() -> None:
    """Remove CASCADE delete from flash_card_sets.material_id foreign key."""
    # Drop the CASCADE constraint
    op.drop_constraint(
        'flash_card_sets_material_id_fkey',
        'flash_card_sets',
        type_='foreignkey'
    )
    
    # Recreate without CASCADE (original behavior)
    op.create_foreign_key(
        'flash_card_sets_material_id_fkey',
        'flash_card_sets',
        'study_materials',
        ['material_id'],
        ['id']
    )
