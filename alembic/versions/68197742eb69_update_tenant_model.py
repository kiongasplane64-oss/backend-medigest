"""update tenant_model

Revision ID: 68197742eb69
Revises: fff3fc5d3842
Create Date: 2025-12-25 17:02:06.335686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '68197742eb69'
down_revision: Union[str, Sequence[str], None] = 'fff3fc5d3842'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
