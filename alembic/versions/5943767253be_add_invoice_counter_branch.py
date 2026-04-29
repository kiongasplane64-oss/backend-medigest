"""add_invoice_counter_branch

Revision ID: 5943767253be
Revises: fb9b513af5ad
Create Date: 2026-04-29 07:28:33.189673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '5943767253be'
down_revision: Union[str, Sequence[str], None] = 'fb9b513af5ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    
    # Vérifier si la colonne existe déjà
    columns = [col['name'] for col in inspector.get_columns('invoice_counter')]
    
    if 'branch_id' not in columns:
        op.add_column('invoice_counter', sa.Column('branch_id', sa.UUID(), nullable=True))
        print("✅ Colonne branch_id ajoutée")
    else:
        print("ℹ️ Colonne branch_id existe déjà")
    
    # Mettre à jour les enregistrements existants
    # Vérifier d'abord s'il y a des enregistrements avec branch_id NULL
    result = conn.execute(sa.text("SELECT COUNT(*) FROM invoice_counter WHERE branch_id IS NULL"))
    count = result.fetchone()[0]
    
    if count > 0:
        conn.execute(sa.text("""
            UPDATE invoice_counter 
            SET branch_id = (
                SELECT b.id 
                FROM branches b 
                WHERE b.parent_pharmacy_id = invoice_counter.pharmacy_id 
                LIMIT 1
            )
            WHERE branch_id IS NULL
        """))
        print(f"✅ {count} enregistrements mis à jour avec branch_id")
    
    # Rendre branch_id NOT NULL (si la colonne existe)
    if 'branch_id' in columns:
        op.alter_column('invoice_counter', 'branch_id', nullable=False)
        print("✅ branch_id maintenant NOT NULL")
    
    # Ajouter la clé étrangère (si elle n'existe pas)
    try:
        op.create_foreign_key(
            'fk_invoice_counter_branch',
            'invoice_counter', 'branches',
            ['branch_id'], ['id']
        )
        print("✅ Clé étrangère ajoutée")
    except Exception as e:
        print(f"ℹ️ Clé étrangère: {e}")
    
    # Ajouter la contrainte unique composite (si elle n'existe pas)
    try:
        op.create_unique_constraint(
            'unique_invoice_counter_per_branch',
            'invoice_counter',
            ['pharmacy_id', 'branch_id', 'date']
        )
        print("✅ Contrainte unique ajoutée")
    except Exception as e:
        print(f"ℹ️ Contrainte unique: {e}")

def downgrade():
    print("ℹ️ Downgrade: rien à faire, la colonne branch_id est conservée")