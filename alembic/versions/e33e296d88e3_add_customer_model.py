"""add_customer_model

Revision ID: e33e296d88e3
Revises: bba2b861ecdd
Create Date: 2026-03-18 01:03:01.195623

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import reflection

# revision identifiers, used by Alembic.
revision: str = 'e33e296d88e3'
down_revision: Union[str, Sequence[str], None] = 'bba2b861ecdd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # --- 1. Création de la table customers ---
    op.create_table('customers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=50), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('blood_type', sa.String(length=5), nullable=True),
        sa.Column('allergies', sa.Text(), nullable=True),
        sa.Column('medical_notes', sa.Text(), nullable=True),
        sa.Column('insurance_provider', sa.String(length=255), nullable=True),
        sa.Column('insurance_number', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('is_vip', sa.Boolean(), nullable=True),
        sa.Column('loyalty_points', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # --- 2. Gestion sécurisée de l'index sur inventory_items ---
    conn = op.get_bind()
    inspect_obj = reflection.Inspector.from_engine(conn)
    existing_indices = [idx['name'] for idx in inspect_obj.get_indexes('inventory_items')]
    
    if 'ix_inventory_items_product' not in existing_indices:
        op.create_index('ix_inventory_items_product', 'inventory_items', ['tenant_id', 'product_id'], unique=False)

    # --- 3. Mise à jour de l'index tenants (Suppression de l'unicité) ---
    # On vérifie si l'index existe avant de le manipuler
    tenants_indices = [idx['name'] for idx in inspect_obj.get_indexes('tenants')]
    
    # Supprime l'ancien index s'il existe (celui généré par Alembic avec f())
    # Note: On utilise try/except au cas où le nom généré diffère
    try:
        op.drop_index(op.f('ix_tenants_slug'), table_name='tenants')
    except Exception:
        pass

    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tenants_slug', table_name='tenants')
    op.create_index(op.f('ix_tenants_slug'), 'tenants', ['slug'], unique=True)
    
    # Drop sécurisé
    conn = op.get_bind()
    inspect_obj = reflection.Inspector.from_engine(conn)
    existing_indices = [idx['name'] for idx in inspect_obj.get_indexes('inventory_items')]
    if 'ix_inventory_items_product' in existing_indices:
        op.drop_index('ix_inventory_items_product', table_name='inventory_items')
        
    op.drop_table('customers')