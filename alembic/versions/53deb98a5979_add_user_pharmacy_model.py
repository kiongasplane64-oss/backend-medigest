"""add_user_pharmacy_model

Revision ID: 53deb98a5979
Revises: fa3950a1303c
Create Date: 2025-12-25 21:21:27.944580

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53deb98a5979'
down_revision: Union[str, Sequence[str], None] = 'fa3950a1303c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
