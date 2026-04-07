"""fix_relationship_with_subscriptions

Revision ID: 130183be23cf
Revises: 7898bfe5a42e
Create Date: 2026-04-06 17:05:31.859209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '130183be23cf'
down_revision: Union[str, Sequence[str], None] = '7898bfe5a42e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - correction des relations (sans ENUM)"""
    
    # 1. Rendre subscription_id nullable (au cas où)
    op.alter_column('pharmacies', 'subscription_id',
               existing_type=sa.UUID(),
               nullable=True)
    
    # 2. Laisser les colonnes de pharmacy_subscriptions comme VARCHAR (pas d'ENUM)
    # Ces colonnes sont déjà correctes avec VARCHAR
    
    # 3. Ajouter les colonnes selling_price_retail et selling_price_wholesale si elles n'existent pas
    try:
        op.add_column('products', sa.Column('selling_price_retail', sa.Numeric(precision=12, scale=2), nullable=True, comment='Prix de vente au détail'))
    except Exception:
        pass
    
    try:
        op.add_column('products', sa.Column('selling_price_wholesale', sa.Numeric(precision=12, scale=2), nullable=True, comment='Prix de vente en gros'))
    except Exception:
        pass
    
    # 4. Supprimer l'index s'il existe
    try:
        op.drop_index(op.f('idx_products_branch'), table_name='products')
    except Exception:
        pass
    
    # 5. Corriger l'index tenants_slug
    try:
        op.drop_index(op.f('ix_tenants_slug'), table_name='tenants')
    except Exception:
        pass
    
    op.create_index(op.f('ix_tenants_slug'), 'tenants', ['slug'], unique=True)


def downgrade() -> None:
    """Downgrade schema"""
    
    try:
        op.drop_index(op.f('ix_tenants_slug'), table_name='tenants')
    except Exception:
        pass
    
    try:
        op.create_index(op.f('ix_tenants_slug'), 'tenants', ['slug'], unique=False)
    except Exception:
        pass
    
    try:
        op.drop_column('products', 'selling_price_wholesale')
    except Exception:
        pass
    
    try:
        op.drop_column('products', 'selling_price_retail')
    except Exception:
        pass