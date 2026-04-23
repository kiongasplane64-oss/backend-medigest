"""add_admin_sync_tables

Revision ID: c60f40653250
Revises: d387b050d2c7
Create Date: 2026-04-22 09:01:15.524164

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c60f40653250'
down_revision: Union[str, Sequence[str], None] = 'd387b050d2c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    # ⚠️ NE PAS recréer les ENUMs qui existent déjà
    # Les ENUMs suivants existent probablement déjà dans la base :
    # - subscriptionplan
    # - billingperiod
    # - subscriptionstatus
    # - syncstatus
    # - syncentitytype
    # - syncoperation
    # - returnreason_enum
    # - returnstatus_enum
    # - returntype_enum
    
    # Table: admin_sync_batches
    op.create_table('admin_sync_batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.String(length=255), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=True),
        sa.Column('branch_id', sa.UUID(), nullable=True),
        sa.Column('entity_types', sa.JSON(), nullable=True),
        sa.Column('total_entities', sa.Integer(), nullable=True),
        sa.Column('total_size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'SYNCED', 'FAILED', 'CONFLICT', 'IGNORED', name='syncstatus'), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exported_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ),
        sa.ForeignKeyConstraint(['exported_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('idx_admin_batch_created', 'admin_sync_batches', ['created_at'], unique=False)
    op.create_index('idx_admin_batch_status', 'admin_sync_batches', ['status'], unique=False)
    op.create_index(op.f('ix_admin_sync_batches_batch_id'), 'admin_sync_batches', ['batch_id'], unique=True)
    op.create_index(op.f('ix_admin_sync_batches_id'), 'admin_sync_batches', ['id'], unique=False)
    
    # Table: admin_sync_checkpoints
    op.create_table('admin_sync_checkpoints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),
        sa.Column('entity_type', sa.Enum('TENANT', 'BRANCH', 'USER', 'PRODUCT', 'SALE', 'INVOICE', 'PURCHASE', 'DEBT', 'CAPITAL', 'EXPENSE', 'STOCK', 'CUSTOMER', 'SUPPLIER', 'TRANSFER', 'ORDER', 'PAYMENT', 'FINANCIAL_TRANSACTION', 'AUDIT_LOG', 'CATEGORY', 'STOCK_ADJUSTMENT', name='syncentitytype'), nullable=False),
        sa.Column('last_sync_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_sync_id', sa.Integer(), nullable=True),
        sa.Column('total_synced_count', sa.BigInteger(), nullable=True),
        sa.Column('last_sync_duration', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('idx_admin_checkpoint_tenant_branch', 'admin_sync_checkpoints', ['tenant_id', 'branch_id', 'entity_type'], unique=False)
    op.create_index(op.f('ix_admin_sync_checkpoints_id'), 'admin_sync_checkpoints', ['id'], unique=False)
    
    # Table: admin_sync_filters
    op.create_table('admin_sync_filters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('entity_types', sa.JSON(), nullable=True),
        sa.Column('tenant_ids', sa.JSON(), nullable=True),
        sa.Column('branch_ids', sa.JSON(), nullable=True),
        sa.Column('date_range_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('date_range_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('custom_filters', sa.JSON(), nullable=True),
        sa.Column('admin_user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('idx_admin_filter_user', 'admin_sync_filters', ['admin_user_id'], unique=False)
    op.create_index(op.f('ix_admin_sync_filters_id'), 'admin_sync_filters', ['id'], unique=False)
    
    # Table: admin_sync_logs
    op.create_table('admin_sync_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_tenant_id', sa.UUID(), nullable=False),
        sa.Column('source_branch_id', sa.UUID(), nullable=True),
        sa.Column('entity_type', sa.Enum('TENANT', 'BRANCH', 'USER', 'PRODUCT', 'SALE', 'INVOICE', 'PURCHASE', 'DEBT', 'CAPITAL', 'EXPENSE', 'STOCK', 'CUSTOMER', 'SUPPLIER', 'TRANSFER', 'ORDER', 'PAYMENT', 'FINANCIAL_TRANSACTION', 'AUDIT_LOG', 'CATEGORY', 'STOCK_ADJUSTMENT', name='syncentitytype'), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('entity_version', sa.String(length=255), nullable=True),
        sa.Column('entity_data', sa.JSON(), nullable=False),
        sa.Column('operation', sa.Enum('CREATE', 'UPDATE', 'DELETE', 'MERGE', name='syncoperation'), nullable=True),
        sa.Column('sync_status', sa.Enum('PENDING', 'SYNCED', 'FAILED', 'CONFLICT', 'IGNORED', name='syncstatus'), nullable=True),
        sa.Column('admin_user_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_modified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('conflict_resolution', sa.Text(), nullable=True),
        sa.Column('previous_version_hash', sa.String(length=255), nullable=True),
        sa.Column('sync_duration_ms', sa.Integer(), nullable=True),
        sa.Column('data_size_bytes', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['admin_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['source_branch_id'], ['branches.id'], ),
        sa.ForeignKeyConstraint(['source_tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('idx_admin_sync_admin', 'admin_sync_logs', ['admin_user_id'], unique=False)
    op.create_index('idx_admin_sync_created', 'admin_sync_logs', ['created_at'], unique=False)
    op.create_index('idx_admin_sync_status', 'admin_sync_logs', ['sync_status'], unique=False)
    op.create_index('idx_admin_sync_tenant_entity', 'admin_sync_logs', ['source_tenant_id', 'entity_type', 'entity_id'], unique=False)
    op.create_index(op.f('ix_admin_sync_logs_id'), 'admin_sync_logs', ['id'], unique=False)
    
    # ⚠️ Table subscriptions - sans créer l'ENUM (existe déjà)
    # Utiliser sa.Enum avec create_constraint=False ou omettre la création
    op.create_table('subscriptions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('subscription_code', sa.String(length=50), nullable=False),
        sa.Column('plan', sa.String(length=50), nullable=False),  # ⚠️ Utiliser String au lieu d'Enum
        sa.Column('plan_name', sa.String(length=100), nullable=False),
        sa.Column('billing_period', sa.String(length=50), nullable=False),  # ⚠️ String au lieu d'Enum
        sa.Column('status', sa.String(length=50), nullable=False),  # ⚠️ String au lieu d'Enum
        sa.Column('monthly_price', sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column('annual_price', sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column('current_price', sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column('tax_rate', sa.DECIMAL(precision=5, scale=2), nullable=True),
        sa.Column('discount_percent', sa.DECIMAL(precision=5, scale=2), nullable=True),
        sa.Column('discount_amount', sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('end_date', sa.DateTime(), nullable=False),
        sa.Column('trial_end_date', sa.DateTime(), nullable=True),
        sa.Column('next_billing_date', sa.DateTime(), nullable=True),
        sa.Column('cancellation_date', sa.DateTime(), nullable=True),
        sa.Column('max_users', sa.Integer(), nullable=False),
        sa.Column('max_products', sa.Integer(), nullable=True),
        sa.Column('max_storage_mb', sa.Integer(), nullable=False),
        sa.Column('features', sa.Text(), nullable=True),
        sa.Column('auto_renew', sa.Boolean(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('meta_data', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Modifications sur products
    op.add_column('products', sa.Column('synced_quantity', sa.Integer(), nullable=False, server_default='0', comment='Dernière quantité synchronisée connue'))
    op.add_column('products', sa.Column('pending_quantity_change', sa.Integer(), nullable=False, server_default='0', comment='Changement de stock en attente de synchro'))
    
    # Modifications sur return_items (gardez votre code existant)
    # ... (votre code existant pour return_items)
    
    # Modifications sur returns (gardez votre code existant)
    # ... (votre code existant pour returns)
    
    # Foreign key pour subscription_payments
    op.create_foreign_key(None, 'subscription_payments', 'subscriptions', ['subscription_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    # Implémentez le downgrade
    op.drop_table('admin_sync_logs')
    op.drop_table('admin_sync_filters')
    op.drop_table('admin_sync_checkpoints')
    op.drop_table('admin_sync_batches')
    op.drop_column('products', 'pending_quantity_change')
    op.drop_column('products', 'synced_quantity')
    op.drop_constraint(None, 'subscription_payments', type_='foreignkey')
    op.drop_table('subscriptions')