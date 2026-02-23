"""update pharmacy_model and user_model

Revision ID: fa3950a1303c
Revises: 68197742eb69
Create Date: 2025-12-25 21:20:39.780267

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa3950a1303c'
down_revision: Union[str, Sequence[str], None] = '68197742eb69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
