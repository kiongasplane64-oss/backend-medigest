"""upgrade pharmacy_model and product_model

Revision ID: 3e7322fa6485
Revises: 53deb98a5979
Create Date: 2025-12-25 21:47:47.396603

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e7322fa6485'
down_revision: Union[str, Sequence[str], None] = '53deb98a5979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
