"""update_product_and_transfert

Revision ID: 611a395a507c
Revises: 7a166aa8aa96
Create Date: 2026-03-24 05:55:17.541188

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '611a395a507c'
down_revision: Union[str, Sequence[str], None] = '7a166aa8aa96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    # =============================================
    # 1. CRÉATION DES ENUMS (AVANT DE LES UTILISER)
    # =============================================
    
    # Créer l'ENUM transferpriority
    op.execute("CREATE TYPE transferpriority AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'URGENT')")
    
    # Créer l'ENUM transferstatus s'il n'existe pas (ajout de IN_TRANSIT et PARTIALLY_RECEIVED)
    op.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'transferstatus') THEN
                CREATE TYPE transferstatus AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'COMPLETED', 'CANCELLED', 'IN_TRANSIT', 'PARTIALLY_RECEIVED');
            ELSE
                -- Ajouter les nouvelles valeurs si elles n'existent pas
                ALTER TYPE transferstatus ADD VALUE IF NOT EXISTS 'IN_TRANSIT';
                ALTER TYPE transferstatus ADD VALUE IF NOT EXISTS 'PARTIALLY_RECEIVED';
            END IF;
        END $$;
    """)
    
    # =============================================
    # 2. MODIFICATIONS SUR product_stocks
    # =============================================
    
    op.add_column('product_stocks', sa.Column('pharmacy_id', sa.UUID(), nullable=False))
    op.create_index(op.f('ix_product_stocks_pharmacy_id'), 'product_stocks', ['pharmacy_id'], unique=False)
    op.create_index('ix_product_stocks_pharmacy_product', 'product_stocks', ['pharmacy_id', 'product_id'], unique=False)
    op.create_index('ix_product_stocks_pharmacy_status', 'product_stocks', ['pharmacy_id', 'status'], unique=False)
    op.create_foreign_key(None, 'product_stocks', 'pharmacies', ['pharmacy_id'], ['id'])
    
    # =============================================
    # 3. MODIFICATIONS SUR product_transfers
    # =============================================
    
    # Ajouter les nouvelles colonnes
    op.add_column('product_transfers', sa.Column('source_pharmacy_id', sa.UUID(), nullable=False))
    op.add_column('product_transfers', sa.Column('destination_pharmacy_id', sa.UUID(), nullable=False))
    op.add_column('product_transfers', sa.Column('priority', sa.Enum('LOW', 'MEDIUM', 'HIGH', 'URGENT', name='transferpriority'), nullable=True))
    op.add_column('product_transfers', sa.Column('prepared_date', sa.DateTime(), nullable=True))
    op.add_column('product_transfers', sa.Column('shipped_date', sa.DateTime(), nullable=True))
    op.add_column('product_transfers', sa.Column('cancelled_date', sa.DateTime(), nullable=True))
    op.add_column('product_transfers', sa.Column('actual_delivery_date', sa.DateTime(), nullable=True))
    op.add_column('product_transfers', sa.Column('tracking_number', sa.String(length=100), nullable=True))
    op.add_column('product_transfers', sa.Column('shipping_cost', sa.Numeric(precision=12, scale=2), nullable=True, server_default='0.0'))
    op.add_column('product_transfers', sa.Column('prepared_by_id', sa.UUID(), nullable=True))
    op.add_column('product_transfers', sa.Column('shipped_by_id', sa.UUID(), nullable=True))
    op.add_column('product_transfers', sa.Column('cancelled_by_id', sa.UUID(), nullable=True))
    op.add_column('product_transfers', sa.Column('total_quantity_requested', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('product_transfers', sa.Column('total_quantity_transferred', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('product_transfers', sa.Column('total_quantity_received', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('product_transfers', sa.Column('is_urgent', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('product_transfers', sa.Column('is_completed', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('product_transfers', sa.Column('has_discrepancy', sa.Boolean(), nullable=True, server_default='false'))
    
    # Modifier requested_date pour qu'il ne soit pas nullable
    op.alter_column('product_transfers', 'requested_date',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=False)
    
    # Créer les index
    op.create_index(op.f('ix_product_transfers_destination_pharmacy_id'), 'product_transfers', ['destination_pharmacy_id'], unique=False)
    op.create_index(op.f('ix_product_transfers_source_pharmacy_id'), 'product_transfers', ['source_pharmacy_id'], unique=False)
    
    # Supprimer les anciennes contraintes
    op.drop_constraint('product_transfers_tenant_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_to_pharmacy_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_from_pharmacy_id_fkey', 'product_transfers', type_='foreignkey')
    
    # Créer les nouvelles contraintes
    op.create_foreign_key('product_transfers_source_pharmacy_id_fkey', 'product_transfers', 'pharmacies', ['source_pharmacy_id'], ['id'])
    op.create_foreign_key('product_transfers_destination_pharmacy_id_fkey', 'product_transfers', 'pharmacies', ['destination_pharmacy_id'], ['id'])
    op.create_foreign_key('product_transfers_shipped_by_id_fkey', 'product_transfers', 'users', ['shipped_by_id'], ['id'])
    op.create_foreign_key('product_transfers_prepared_by_id_fkey', 'product_transfers', 'users', ['prepared_by_id'], ['id'])
    op.create_foreign_key('product_transfers_cancelled_by_id_fkey', 'product_transfers', 'users', ['cancelled_by_id'], ['id'])
    op.create_foreign_key('product_transfers_tenant_id_fkey', 'product_transfers', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    
    # Supprimer les anciennes colonnes
    op.drop_column('product_transfers', 'from_pharmacy_id')
    op.drop_column('product_transfers', 'to_pharmacy_id')
    op.drop_column('product_transfers', 'total_quantity')
    
    # =============================================
    # 4. MODIFICATIONS SUR tenants
    # =============================================
    
    op.drop_index('ix_tenants_slug', table_name='tenants')
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=False)
    
    # =============================================
    # 5. MODIFICATIONS SUR transfer_items
    # =============================================
    
    op.create_index(op.f('ix_transfer_items_product_id'), 'transfer_items', ['product_id'], unique=False)
    
    # Supprimer les anciennes contraintes
    op.drop_constraint('transfer_items_transfer_id_fkey', 'transfer_items', type_='foreignkey')
    op.drop_constraint('transfer_items_product_id_fkey', 'transfer_items', type_='foreignkey')
    
    # Créer les nouvelles contraintes avec CASCADE et RESTRICT
    op.create_foreign_key('transfer_items_product_id_fkey', 'transfer_items', 'products', ['product_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key('transfer_items_transfer_id_fkey', 'transfer_items', 'product_transfers', ['transfer_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    """Downgrade schema."""
    
    # =============================================
    # 1. RESTAURATION DES MODIFICATIONS transfer_items
    # =============================================
    
    op.drop_constraint('transfer_items_transfer_id_fkey', 'transfer_items', type_='foreignkey')
    op.drop_constraint('transfer_items_product_id_fkey', 'transfer_items', type_='foreignkey')
    op.create_foreign_key('transfer_items_product_id_fkey', 'transfer_items', 'products', ['product_id'], ['id'])
    op.create_foreign_key('transfer_items_transfer_id_fkey', 'transfer_items', 'product_transfers', ['transfer_id'], ['id'])
    op.drop_index(op.f('ix_transfer_items_product_id'), table_name='transfer_items')
    
    # =============================================
    # 2. RESTAURATION DES MODIFICATIONS tenants
    # =============================================
    
    op.drop_index('ix_tenants_slug', table_name='tenants')
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=True)
    
    # =============================================
    # 3. RESTAURATION DES MODIFICATIONS product_transfers
    # =============================================
    
    # Restaurer les anciennes colonnes
    op.add_column('product_transfers', sa.Column('total_quantity', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('product_transfers', sa.Column('to_pharmacy_id', sa.UUID(), autoincrement=False, nullable=False))
    op.add_column('product_transfers', sa.Column('from_pharmacy_id', sa.UUID(), autoincrement=False, nullable=False))
    
    # Supprimer les nouvelles contraintes
    op.drop_constraint('product_transfers_tenant_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_cancelled_by_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_prepared_by_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_shipped_by_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_destination_pharmacy_id_fkey', 'product_transfers', type_='foreignkey')
    op.drop_constraint('product_transfers_source_pharmacy_id_fkey', 'product_transfers', type_='foreignkey')
    
    # Recréer les anciennes contraintes
    op.create_foreign_key('product_transfers_from_pharmacy_id_fkey', 'product_transfers', 'pharmacies', ['from_pharmacy_id'], ['id'])
    op.create_foreign_key('product_transfers_to_pharmacy_id_fkey', 'product_transfers', 'pharmacies', ['to_pharmacy_id'], ['id'])
    op.create_foreign_key('product_transfers_tenant_id_fkey', 'product_transfers', 'tenants', ['tenant_id'], ['id'])
    
    # Supprimer les index
    op.drop_index(op.f('ix_product_transfers_source_pharmacy_id'), table_name='product_transfers')
    op.drop_index(op.f('ix_product_transfers_destination_pharmacy_id'), table_name='product_transfers')
    
    # Restaurer requested_date nullable
    op.alter_column('product_transfers', 'requested_date',
                    existing_type=postgresql.TIMESTAMP(),
                    nullable=True)
    
    # Supprimer les nouvelles colonnes
    op.drop_column('product_transfers', 'has_discrepancy')
    op.drop_column('product_transfers', 'is_completed')
    op.drop_column('product_transfers', 'is_urgent')
    op.drop_column('product_transfers', 'total_quantity_received')
    op.drop_column('product_transfers', 'total_quantity_transferred')
    op.drop_column('product_transfers', 'total_quantity_requested')
    op.drop_column('product_transfers', 'cancelled_by_id')
    op.drop_column('product_transfers', 'shipped_by_id')
    op.drop_column('product_transfers', 'prepared_by_id')
    op.drop_column('product_transfers', 'shipping_cost')
    op.drop_column('product_transfers', 'tracking_number')
    op.drop_column('product_transfers', 'actual_delivery_date')
    op.drop_column('product_transfers', 'cancelled_date')
    op.drop_column('product_transfers', 'shipped_date')
    op.drop_column('product_transfers', 'prepared_date')
    op.drop_column('product_transfers', 'priority')
    op.drop_column('product_transfers', 'destination_pharmacy_id')
    op.drop_column('product_transfers', 'source_pharmacy_id')
    
    # =============================================
    # 4. RESTAURATION DES MODIFICATIONS product_stocks
    # =============================================
    
    op.drop_constraint(None, 'product_stocks', type_='foreignkey')
    op.drop_index('ix_product_stocks_pharmacy_status', table_name='product_stocks')
    op.drop_index('ix_product_stocks_pharmacy_product', table_name='product_stocks')
    op.drop_index(op.f('ix_product_stocks_pharmacy_id'), table_name='product_stocks')
    op.drop_column('product_stocks', 'pharmacy_id')
    
    # =============================================
    # 5. SUPPRESSION DES ENUMS (optionnel - garder pour ne pas casser)
    # =============================================
    
    # Note: On ne supprime pas les ENUMS en downgrade car ils pourraient être utilisés ailleurs
    # op.execute("DROP TYPE transferpriority")