"""upgrade_pharmacy_model

Revision ID: d62706e95712
Revises: bbbe2a94de2b
Create Date: 2026-03-18 16:57:27.509605

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd62706e95712'
down_revision: Union[str, Sequence[str], None] = 'bbbe2a94de2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema and resolve data length issues."""
    
    # --- 1. Correction de la colonne 'country' ---
    # On force la conversion en prenant les 2 premiers caractères pour éviter l'erreur de troncature
    op.execute(
        "ALTER TABLE pharmacies ALTER COLUMN country TYPE VARCHAR(2) USING LEFT(country, 2)"
    )
    # On synchronise l'état avec Alembic (nullable, etc.)
    op.alter_column('pharmacies', 'country',
               existing_type=sa.VARCHAR(length=100),
               type_=sa.String(length=2),
               existing_nullable=False)

    # --- 2. Mise à jour de 'pharmacy_type' ---
    # Suppression du commentaire existant
    op.alter_column('pharmacies', 'pharmacy_type',
               existing_type=sa.VARCHAR(length=50),
               comment=None,
               existing_comment='retail, hospital, clinic',
               existing_nullable=True)

    # --- 3. Gestion des contraintes et index ---
    # Suppression de la contrainte d'unicité sur pharmacy_code
    op.drop_constraint('pharmacies_pharmacy_code_key', 'pharmacies', type_='unique')

    # Passage des index 'tenants' en UNIQUE
    # On supprime d'abord les anciens index non-uniques
    op.drop_index('ix_tenants_slug', table_name='tenants')
    op.create_index(op.f('ix_tenants_slug'), 'tenants', ['slug'], unique=True)
    
    op.drop_index('ix_tenants_tenant_code', table_name='tenants')
    op.create_index(op.f('ix_tenants_tenant_code'), 'tenants', ['tenant_code'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    
    # Rétablissement des index non-uniques sur tenants
    op.drop_index(op.f('ix_tenants_tenant_code'), table_name='tenants')
    op.create_index('ix_tenants_tenant_code', 'tenants', ['tenant_code'], unique=False)
    
    op.drop_index(op.f('ix_tenants_slug'), table_name='tenants')
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=False)

    # Rétablissement de la contrainte d'unicité
    op.create_unique_constraint('pharmacies_pharmacy_code_key', 'pharmacies', ['pharmacy_code'])

    # Remise du commentaire sur pharmacy_type
    op.alter_column('pharmacies', 'pharmacy_type',
               existing_type=sa.VARCHAR(length=50),
               comment='retail, hospital, clinic',
               existing_nullable=True)

    # Rétablissement de la longueur de la colonne country
    op.alter_column('pharmacies', 'country',
               existing_type=sa.String(length=2),
               type_=sa.VARCHAR(length=100),
               existing_nullable=False)