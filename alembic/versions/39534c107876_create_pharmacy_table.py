"""create pharmacy_table

Revision ID: 39534c107876
Revises: 321ae2102b0c
Create Date: 2025-12-26 21:50:44.763201

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '39534c107876'
down_revision: Union[str, Sequence[str], None] = '321ae2102b0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
