"""add_subscription_fields_to_payments

Revision ID: 88993e563ae6
Revises: 2a0fd4438d55
Create Date: 2026-01-06 11:24:07.709333

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '88993e563ae6'
down_revision: Union[str, Sequence[str], None] = '2a0fd4438d55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
