"""add_pharmacy_model

Revision ID: fff3fc5d3842
Revises: b8db746bf2f4
Create Date: 2025-12-25 16:58:30.078147

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fff3fc5d3842'
down_revision: Union[str, Sequence[str], None] = 'b8db746bf2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
