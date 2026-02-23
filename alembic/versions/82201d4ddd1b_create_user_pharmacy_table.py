"""create_user_pharmacy_table

Revision ID: 82201d4ddd1b
Revises: a26ff931be73
Create Date: 2025-12-26 08:03:26.910788

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82201d4ddd1b'
down_revision: Union[str, Sequence[str], None] = 'a26ff931be73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
