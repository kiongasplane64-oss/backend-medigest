"""Add stock_adjustments and stock_adjustment_items tables

Revision ID: 1668f9ff34a3
Revises: b9a77f62da73
Create Date: 2026-03-23 14:15:48.426342

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1668f9ff34a3'
down_revision: Union[str, Sequence[str], None] = 'b9a77f62da73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================
    # ÉTAPE 1: Créer stock_adjustments d'abord
    # ============================================
    op.create_table('stock_adjustments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),
        sa.Column('adjustment_number', sa.String(length=50), nullable=False),
        sa.Column('adjustment_type', sa.String(length=50), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('total_quantity_change', sa.DECIMAL(precision=15, scale=3), nullable=False, server_default='0'),
        sa.Column('total_value_change', sa.DECIMAL(precision=15, scale=2), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('approved_by', sa.UUID(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approval_notes', sa.Text(), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('inventory_count_id', sa.UUID(), nullable=True),
        sa.Column('inventory_count_item_id', sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('adjustment_number')
    )
    
    # Créer les indexes pour stock_adjustments
    op.create_index('ix_stock_adjustments_adjustment_number', 'stock_adjustments', ['adjustment_number'], unique=True)
    op.create_index('ix_stock_adjustments_adjustment_type', 'stock_adjustments', ['adjustment_type'], unique=False)
    op.create_index('ix_stock_adjustments_branch_id', 'stock_adjustments', ['branch_id'], unique=False)
    op.create_index('ix_stock_adjustments_created_at', 'stock_adjustments', ['created_at'], unique=False)
    op.create_index('ix_stock_adjustments_created_by', 'stock_adjustments', ['created_by'], unique=False)
    op.create_index('ix_stock_adjustments_pharmacy_date', 'stock_adjustments', ['pharmacy_id', 'created_at'], unique=False)
    op.create_index('ix_stock_adjustments_pharmacy_id', 'stock_adjustments', ['pharmacy_id'], unique=False)
    op.create_index('ix_stock_adjustments_status', 'stock_adjustments', ['status'], unique=False)
    op.create_index('ix_stock_adjustments_tenant_id', 'stock_adjustments', ['tenant_id'], unique=False)
    op.create_index('ix_stock_adjustments_tenant_status', 'stock_adjustments', ['tenant_id', 'status'], unique=False)
    op.create_index('ix_stock_adjustments_type', 'stock_adjustments', ['adjustment_type', 'status'], unique=False)
    
    # ============================================
    # ÉTAPE 2: Créer stock_adjustment_items
    # ============================================
    op.create_table('stock_adjustment_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('adjustment_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('product_stock_id', sa.UUID(), nullable=True),
        sa.Column('old_quantity', sa.DECIMAL(precision=15, scale=3), nullable=False, server_default='0'),
        sa.Column('new_quantity', sa.DECIMAL(precision=15, scale=3), nullable=False, server_default='0'),
        sa.Column('quantity_change', sa.DECIMAL(precision=15, scale=3), nullable=False, server_default='0'),
        sa.Column('unit_price', sa.DECIMAL(precision=15, scale=2), nullable=True),
        sa.Column('old_value', sa.DECIMAL(precision=15, scale=2), nullable=False, server_default='0'),
        sa.Column('new_value', sa.DECIMAL(precision=15, scale=2), nullable=False, server_default='0'),
        sa.Column('value_change', sa.DECIMAL(precision=15, scale=2), nullable=False, server_default='0'),
        sa.Column('batch_number', sa.String(length=100), nullable=True),
        sa.Column('expiry_date', sa.DateTime(), nullable=True),
        sa.Column('location', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('stock_movement_id', sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Créer les indexes pour stock_adjustment_items
    op.create_index('ix_stock_adjustment_items_adjustment', 'stock_adjustment_items', ['adjustment_id'], unique=False)
    op.create_index('ix_stock_adjustment_items_product', 'stock_adjustment_items', ['product_id', 'adjustment_id'], unique=False)
    
    # Ajouter les clés étrangères après la création des tables
    op.create_foreign_key('fk_stock_adjustment_items_adjustment', 'stock_adjustment_items', 'stock_adjustments', ['adjustment_id'], ['id'])
    op.create_foreign_key('fk_stock_adjustment_items_tenant', 'stock_adjustment_items', 'tenants', ['tenant_id'], ['id'])
    op.create_foreign_key('fk_stock_adjustment_items_pharmacy', 'stock_adjustment_items', 'pharmacies', ['pharmacy_id'], ['id'])
    op.create_foreign_key('fk_stock_adjustment_items_product', 'stock_adjustment_items', 'products', ['product_id'], ['id'])
    op.create_foreign_key('fk_stock_adjustment_items_product_stock', 'stock_adjustment_items', 'product_stocks', ['product_stock_id'], ['id'])
    op.create_foreign_key('fk_stock_adjustment_items_stock_movement', 'stock_adjustment_items', 'stock_movements', ['stock_movement_id'], ['id'])

    # ============================================
    # ÉTAPE 3: Ajouter les colonnes manquantes à stock_movements
    # ============================================
    # Ajouter les colonnes (si elles n'existent pas déjà)
    op.add_column('stock_movements', sa.Column('pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('branch_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('purchase_price', sa.DECIMAL(precision=15, scale=2), nullable=True))
    op.add_column('stock_movements', sa.Column('selling_price', sa.DECIMAL(precision=15, scale=2), nullable=True))
    op.add_column('stock_movements', sa.Column('direction', sa.String(length=10), nullable=True))
    op.add_column('stock_movements', sa.Column('from_pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('to_pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('transfer_status', sa.String(length=20), nullable=True, server_default='pending'))
    op.add_column('stock_movements', sa.Column('purchase_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('purchase_item_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('adjustment_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('adjustment_item_id', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('validated_by', sa.UUID(), nullable=True))
    op.add_column('stock_movements', sa.Column('validated_at', sa.DateTime(), nullable=True))
    op.add_column('stock_movements', sa.Column('is_validated', sa.Boolean(), nullable=True, server_default='false'))
    op.add_column('stock_movements', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('stock_movements', sa.Column('movement_date', sa.DateTime(), nullable=True))
    
    # Modifier le commentaire de movement_type
    op.alter_column('stock_movements', 'movement_type',
                    comment='initial, purchase, sale, adjustment, return, transfer_in, transfer_out, expiry, correction')
    
    # Ajouter les indexes
    op.create_index('ix_stock_movements_branch_date', 'stock_movements', ['branch_id', 'created_at'], unique=False)
    op.create_index('ix_stock_movements_branch_id', 'stock_movements', ['branch_id'], unique=False)
    op.create_index('ix_stock_movements_movement_date', 'stock_movements', ['movement_date'], unique=False)
    op.create_index('ix_stock_movements_pharmacy_date', 'stock_movements', ['pharmacy_id', 'created_at'], unique=False)
    op.create_index('ix_stock_movements_pharmacy_id', 'stock_movements', ['pharmacy_id'], unique=False)
    op.create_index('ix_stock_movements_purchase', 'stock_movements', ['purchase_id'], unique=False)
    op.create_index('ix_stock_movements_sale', 'stock_movements', ['sale_id'], unique=False)
    op.create_index('ix_stock_movements_transfer', 'stock_movements', ['from_pharmacy_id', 'to_pharmacy_id'], unique=False)
    
    # Ajouter les clés étrangères
    op.create_foreign_key('fk_stock_movements_pharmacy', 'stock_movements', 'pharmacies', ['pharmacy_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_branch', 'stock_movements', 'branches', ['branch_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_from_pharmacy', 'stock_movements', 'pharmacies', ['from_pharmacy_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_to_pharmacy', 'stock_movements', 'pharmacies', ['to_pharmacy_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_purchase', 'stock_movements', 'purchases', ['purchase_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_purchase_item', 'stock_movements', 'purchase_items', ['purchase_item_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_adjustment', 'stock_movements', 'stock_adjustments', ['adjustment_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_adjustment_item', 'stock_movements', 'stock_adjustment_items', ['adjustment_item_id'], ['id'])
    op.create_foreign_key('fk_stock_movements_validated_by', 'stock_movements', 'users', ['validated_by'], ['id'])
    
    # ============================================
    # ÉTAPE 4: Ajouter les colonnes à inventory_counts
    # ============================================
    op.add_column('inventory_counts', sa.Column('pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('inventory_counts', sa.Column('branch_id', sa.UUID(), nullable=True))
    
    op.create_index('ix_inventory_counts_branch_id', 'inventory_counts', ['branch_id'], unique=False)
    op.create_index('ix_inventory_counts_pharmacy', 'inventory_counts', ['pharmacy_id', 'status'], unique=False)
    op.create_index('ix_inventory_counts_pharmacy_id', 'inventory_counts', ['pharmacy_id'], unique=False)
    
    op.create_foreign_key('fk_inventory_counts_pharmacy', 'inventory_counts', 'pharmacies', ['pharmacy_id'], ['id'])
    op.create_foreign_key('fk_inventory_counts_branch', 'inventory_counts', 'branches', ['branch_id'], ['id'])


def downgrade() -> None:
    # Supprimer d'abord les clés étrangères
    op.drop_constraint('fk_stock_movements_adjustment_item', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_adjustment', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_purchase_item', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_purchase', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_to_pharmacy', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_from_pharmacy', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_branch', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_pharmacy', 'stock_movements', type_='foreignkey')
    op.drop_constraint('fk_stock_movements_validated_by', 'stock_movements', type_='foreignkey')
    
    op.drop_constraint('fk_stock_adjustment_items_stock_movement', 'stock_adjustment_items', type_='foreignkey')
    op.drop_constraint('fk_stock_adjustment_items_product_stock', 'stock_adjustment_items', type_='foreignkey')
    op.drop_constraint('fk_stock_adjustment_items_product', 'stock_adjustment_items', type_='foreignkey')
    op.drop_constraint('fk_stock_adjustment_items_pharmacy', 'stock_adjustment_items', type_='foreignkey')
    op.drop_constraint('fk_stock_adjustment_items_tenant', 'stock_adjustment_items', type_='foreignkey')
    op.drop_constraint('fk_stock_adjustment_items_adjustment', 'stock_adjustment_items', type_='foreignkey')
    
    op.drop_constraint('fk_inventory_counts_branch', 'inventory_counts', type_='foreignkey')
    op.drop_constraint('fk_inventory_counts_pharmacy', 'inventory_counts', type_='foreignkey')
    
    # Supprimer les indexes
    op.drop_index('ix_stock_movements_transfer', table_name='stock_movements')
    op.drop_index('ix_stock_movements_sale', table_name='stock_movements')
    op.drop_index('ix_stock_movements_purchase', table_name='stock_movements')
    op.drop_index('ix_stock_movements_pharmacy_id', table_name='stock_movements')
    op.drop_index('ix_stock_movements_pharmacy_date', table_name='stock_movements')
    op.drop_index('ix_stock_movements_movement_date', table_name='stock_movements')
    op.drop_index('ix_stock_movements_branch_id', table_name='stock_movements')
    op.drop_index('ix_stock_movements_branch_date', table_name='stock_movements')
    
    op.drop_index('ix_stock_adjustment_items_product', table_name='stock_adjustment_items')
    op.drop_index('ix_stock_adjustment_items_adjustment', table_name='stock_adjustment_items')
    
    op.drop_index('ix_inventory_counts_pharmacy_id', table_name='inventory_counts')
    op.drop_index('ix_inventory_counts_pharmacy', table_name='inventory_counts')
    op.drop_index('ix_inventory_counts_branch_id', table_name='inventory_counts')
    
    op.drop_index('ix_stock_adjustments_type', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_tenant_status', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_tenant_id', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_status', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_pharmacy_id', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_pharmacy_date', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_created_by', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_created_at', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_branch_id', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_adjustment_type', table_name='stock_adjustments')
    op.drop_index('ix_stock_adjustments_adjustment_number', table_name='stock_adjustments')
    
    # Supprimer les colonnes
    op.drop_column('stock_movements', 'movement_date')
    op.drop_column('stock_movements', 'updated_at')
    op.drop_column('stock_movements', 'is_validated')
    op.drop_column('stock_movements', 'validated_at')
    op.drop_column('stock_movements', 'validated_by')
    op.drop_column('stock_movements', 'adjustment_item_id')
    op.drop_column('stock_movements', 'adjustment_id')
    op.drop_column('stock_movements', 'purchase_item_id')
    op.drop_column('stock_movements', 'purchase_id')
    op.drop_column('stock_movements', 'transfer_status')
    op.drop_column('stock_movements', 'to_pharmacy_id')
    op.drop_column('stock_movements', 'from_pharmacy_id')
    op.drop_column('stock_movements', 'direction')
    op.drop_column('stock_movements', 'selling_price')
    op.drop_column('stock_movements', 'purchase_price')
    op.drop_column('stock_movements', 'branch_id')
    op.drop_column('stock_movements', 'pharmacy_id')
    
    op.drop_column('inventory_counts', 'branch_id')
    op.drop_column('inventory_counts', 'pharmacy_id')
    
    op.drop_table('stock_adjustment_items')
    op.drop_table('stock_adjustments')