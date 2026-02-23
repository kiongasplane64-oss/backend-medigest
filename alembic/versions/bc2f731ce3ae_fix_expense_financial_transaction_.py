"""fix expense financial transaction relation

Revision ID: bc2f731ce3ae
Revises: bb2c81fba2d6
Create Date: 2025-12-20 17:00:15.039714
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "bc2f731ce3ae"
down_revision = "bb2c81fba2d6"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "expenses_transaction_id_fkey",
        "expenses",
        type_="foreignkey",
    )
    op.drop_column("expenses", "transaction_id")


def downgrade():
    op.add_column(
        "expenses",
        sa.Column(
            "transaction_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "expenses_transaction_id_fkey",
        "expenses",
        "financial_transactions",
        ["transaction_id"],
        ["id"],
    )
