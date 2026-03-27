"""add_order_model

Revision ID: c593d64deb23
Revises: 37cba01f790e
Create Date: 2026-03-25 14:07:11.313085

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = 'c593d64deb23'
down_revision: Union[str, Sequence[str], None] = '37cba01f790e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Créer les ENUMs avec IF NOT EXISTS
    op.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'orderstatus') THEN
                CREATE TYPE orderstatus AS ENUM (
                    'PENDING', 'PROCESSING', 'CONFIRMED', 'SHIPPED', 
                    'DELIVERED', 'CANCELLED', 'REFUNDED'
                );
            END IF;
        END $$;
    """)
    
    op.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentstatus') THEN
                CREATE TYPE paymentstatus AS ENUM (
                    'PENDING', 'PAID', 'FAILED', 'REFUNDED', 'PARTIALLY_REFUNDED'
                );
            END IF;
        END $$;
    """)
    
    # Créer la table orders avec UUID (pour correspondre aux tables existantes)
    op.create_table('orders',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', UUID(as_uuid=True), nullable=True),
        sa.Column('customer_name', sa.String(length=255), nullable=False),
        sa.Column('customer_email', sa.String(length=255), nullable=True),
        sa.Column('customer_phone', sa.String(length=50), nullable=True),
        sa.Column('customer_address', sa.JSON(), nullable=True),
        sa.Column('order_number', sa.String(length=50), nullable=False),
        sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('subtotal', sa.Float(), nullable=False),
        sa.Column('tax_amount', sa.Float(), nullable=False),
        sa.Column('shipping_amount', sa.Float(), nullable=False),
        sa.Column('discount_amount', sa.Float(), nullable=False),
        sa.Column('total_amount', sa.Float(), nullable=False),
        sa.Column('status', 
                  postgresql.ENUM('PENDING', 'PROCESSING', 'CONFIRMED', 'SHIPPED', 
                                 'DELIVERED', 'CANCELLED', 'REFUNDED', 
                                 name='orderstatus', create_type=False), 
                  nullable=False),
        sa.Column('payment_status', 
                  postgresql.ENUM('PENDING', 'PAID', 'FAILED', 'REFUNDED', 'PARTIALLY_REFUNDED',
                                 name='paymentstatus', create_type=False), 
                  nullable=False),
        sa.Column('payment_method', sa.String(length=50), nullable=True),
        sa.Column('payment_id', sa.String(length=255), nullable=True),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.Column('shipping_method', sa.String(length=100), nullable=True),
        sa.Column('tracking_number', sa.String(length=255), nullable=True),
        sa.Column('tracking_url', sa.String(length=500), nullable=True),
        sa.Column('shipped_at', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.JSON(), nullable=True),
        sa.Column('order_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Créer les index
    op.create_index(op.f('ix_orders_customer_id'), 'orders', ['customer_id'], unique=False)
    op.create_index(op.f('ix_orders_order_number'), 'orders', ['order_number'], unique=True)
    op.create_index(op.f('ix_orders_tenant_id'), 'orders', ['tenant_id'], unique=False)
    
    # Gérer l'index tenants.slug
    try:
        op.drop_index(op.f('ix_tenants_slug'), table_name='tenants')
    except:
        pass
    
    try:
        op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=False)
    except:
        pass


def downgrade() -> None:
    """Downgrade schema."""
    # Supprimer les index
    try:
        op.drop_index('ix_tenants_slug', table_name='tenants')
    except:
        pass
    
    try:
        op.drop_index(op.f('ix_orders_tenant_id'), table_name='orders')
    except:
        pass
    
    try:
        op.drop_index(op.f('ix_orders_order_number'), table_name='orders')
    except:
        pass
    
    try:
        op.drop_index(op.f('ix_orders_customer_id'), table_name='orders')
    except:
        pass
    
    # Supprimer la table orders
    try:
        op.drop_table('orders')
    except:
        pass
    
    # Supprimer les ENUMs
    op.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'orderstatus') THEN
                DROP TYPE orderstatus CASCADE;
            END IF;
        END $$;
    """)
    
    op.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'paymentstatus') THEN
                DROP TYPE paymentstatus CASCADE;
            END IF;
        END $$;
    """)