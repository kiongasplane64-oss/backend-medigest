"""add_sale_id_to_stock_movements

Revision ID: 5af9d5a70964
Revises: a5863ba62531
Create Date: 2026-03-22 09:15:29.365056

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '5af9d5a70964'
down_revision: Union[str, Sequence[str], None] = 'a5863ba62531'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ajouter la colonne sale_id
    op.add_column('stock_movements', 
        sa.Column('sale_id', UUID, nullable=True)  # Note: UUID sans parenthèses
    )
    # Ajouter la colonne sale_item_id
    op.add_column('stock_movements', 
        sa.Column('sale_item_id', UUID, nullable=True)
    )
    # Ajouter des index pour améliorer les performances
    op.create_index('idx_stock_movements_sale_id', 'stock_movements', ['sale_id'])
    op.create_index('idx_stock_movements_sale_item_id', 'stock_movements', ['sale_item_id'])
    # Ajouter une clé étrangère optionnelle
    op.create_foreign_key(
        'fk_stock_movements_sale_id',
        'stock_movements', 'sales',
        ['sale_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_stock_movements_sale_item_id',
        'stock_movements', 'sale_items',
        ['sale_item_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_stock_movements_sale_item_id', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_sale_id', 'stock_movements', type_='foreignkey')
    op.drop_index('idx_stock_movements_sale_item_id', table_name='stock_movements')
    op.drop_index('idx_stock_movements_sale_id', table_name='stock_movements')
    op.drop_column('stock_movements', 'sale_item_id')
    op.drop_column('stock_movements', 'sale_id')