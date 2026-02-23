"""add_pharmacy_id_to_sales

Revision ID: 1f087f4514cd
Revises: 3e7322fa6485
Create Date: 2025-12-25 22:06:53.131113

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f087f4514cd'
down_revision: Union[str, Sequence[str], None] = '3e7322fa6485'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
