"""create_user_pharmacy_table

Revision ID: a26ff931be73
Revises: 1f087f4514cd
Create Date: 2025-12-26 06:59:29.123098

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a26ff931be73'
down_revision: Union[str, Sequence[str], None] = '1f087f4514cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
