"""upgrade tenant_table

Revision ID: 321ae2102b0c
Revises: 82201d4ddd1b
Create Date: 2025-12-26 21:38:12.293953

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '321ae2102b0c'
down_revision: Union[str, Sequence[str], None] = '82201d4ddd1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
