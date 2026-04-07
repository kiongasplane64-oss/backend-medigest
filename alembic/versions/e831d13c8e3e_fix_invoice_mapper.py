"""fix_invoice_mapper

Revision ID: e831d13c8e3e
Revises: e91f5190df26
Create Date: 2026-04-07 13:34:36.955749

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e831d13c8e3e'
down_revision: Union[str, Sequence[str], None] = 'e91f5190df26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    # Note: Les types ENUM ont déjà été créés manuellement:
    # - invoicestatus ('DRAFT', 'SENT', 'PAID', 'OVERDUE', 'CANCELLED')
    # - invoicetype ('SUBSCRIPTION', 'ONE_TIME', 'RENEWAL')
    # Les colonnes status et invoice_type ont déjà été converties
    
    # 1. Supprimer la table invoice_items
    op.drop_table('invoice_items')
    
    # 2. Modifier les colonnes period_start et period_end pour NOT NULL
    op.alter_column('invoices', 'period_start',
               existing_type=postgresql.TIMESTAMP(),
               nullable=False)
    op.alter_column('invoices', 'period_end',
               existing_type=postgresql.TIMESTAMP(),
               nullable=False)
    
    # 3. Modifier currency pour NOT NULL
    op.alter_column('invoices', 'currency',
               existing_type=sa.VARCHAR(length=3),
               nullable=False)
    
    # 4. Modifier subscription_plan (enlever le commentaire)
    op.alter_column('invoices', 'subscription_plan',
               existing_type=sa.VARCHAR(length=50),
               comment=None,
               existing_comment="Plan d'abonnement facturé",
               existing_nullable=True)
    
    # 5. Gérer l'index et la contrainte unique sur invoice_number
    op.drop_index(op.f('ix_invoices_invoice_number'), table_name='invoices')
    op.create_unique_constraint('uq_invoices_invoice_number', 'invoices', ['invoice_number'])
    
    # 6. Supprimer la foreign key tenant_id (sera recréée plus tard)
    op.drop_constraint(op.f('invoices_tenant_id_fkey'), 'invoices', type_='foreignkey')
    
    # 7. Modifier la table orders
    op.alter_column('orders', 'id',
               existing_type=sa.UUID(),
               type_=sa.String(length=36),
               existing_nullable=False)
    
    # 8. Modifier la table payments
    op.add_column('payments', sa.Column('invoice_payment_id', sa.UUID(), nullable=True))
    op.alter_column('payments', 'payment_method',
               existing_type=sa.VARCHAR(length=30),
               comment='cash, mobile_money, visa, mastercard, bank_transfer, cheque, credit_note',
               existing_comment='cash, mobile_money, visa, bank_transfer, cheque, credit_note',
               existing_nullable=False)
    op.alter_column('payments', 'status',
               existing_type=sa.VARCHAR(length=20),
               comment='success, failed, pending, refunded, cancelled',
               existing_nullable=True)
    op.create_foreign_key('fk_payments_invoice_payment_id', 'payments', 'invoice_payments', ['invoice_payment_id'], ['id'])
    
    # 9. Modifier pharmacy_subscriptions (si nécessaire)
    # Vérifier d'abord si les colonnes sont encore VARCHAR et les convertir
    op.execute("""
        DO $$
        BEGIN
            -- Vérifier si la colonne plan est encore VARCHAR
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'pharmacy_subscriptions' 
                AND column_name = 'plan'
                AND data_type = 'character varying'
                AND EXISTS (
                    SELECT 1 FROM pg_type WHERE typname = 'subscriptionplan'
                )
            ) THEN
                -- Convertir la colonne plan
                EXECUTE 'ALTER TABLE pharmacy_subscriptions ALTER COLUMN plan DROP DEFAULT';
                EXECUTE 'ALTER TABLE pharmacy_subscriptions 
                        ALTER COLUMN plan TYPE subscriptionplan 
                        USING (
                            CASE 
                                WHEN plan = ''trial'' THEN ''TRIAL''::subscriptionplan
                                WHEN plan = ''starter'' THEN ''STARTER''::subscriptionplan
                                WHEN plan = ''professional'' THEN ''PROFESSIONAL''::subscriptionplan
                                WHEN plan = ''enterprise'' THEN ''ENTERPRISE''::subscriptionplan
                                WHEN plan = ''infinite'' THEN ''INFINITE''::subscriptionplan
                                ELSE ''STARTER''::subscriptionplan
                            END
                        )';
                EXECUTE 'ALTER TABLE pharmacy_subscriptions ALTER COLUMN plan SET NOT NULL';
            END IF;
            
            -- Vérifier si la colonne status est encore VARCHAR
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'pharmacy_subscriptions' 
                AND column_name = 'status'
                AND data_type = 'character varying'
                AND EXISTS (
                    SELECT 1 FROM pg_type WHERE typname = 'subscriptionstatus'
                )
            ) THEN
                -- Convertir la colonne status
                EXECUTE 'ALTER TABLE pharmacy_subscriptions ALTER COLUMN status DROP DEFAULT';
                EXECUTE 'ALTER TABLE pharmacy_subscriptions 
                        ALTER COLUMN status TYPE subscriptionstatus 
                        USING (
                            CASE 
                                WHEN status = ''active'' THEN ''ACTIVE''::subscriptionstatus
                                WHEN status = ''expired'' THEN ''EXPIRED''::subscriptionstatus
                                WHEN status = ''suspended'' THEN ''SUSPENDED''::subscriptionstatus
                                WHEN status = ''cancelled'' THEN ''CANCELLED''::subscriptionstatus
                                ELSE ''ACTIVE''::subscriptionstatus
                            END
                        )';
                EXECUTE 'ALTER TABLE pharmacy_subscriptions ALTER COLUMN status SET NOT NULL';
                EXECUTE 'ALTER TABLE pharmacy_subscriptions ALTER COLUMN status SET DEFAULT ''ACTIVE''::subscriptionstatus';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    """Downgrade schema."""
    
    # 1. Revenir en arrière sur pharmacy_subscriptions
    op.execute("""
        ALTER TABLE pharmacy_subscriptions 
        ALTER COLUMN status TYPE VARCHAR(20),
        ALTER COLUMN plan TYPE VARCHAR(50)
    """)
    
    # 2. Revenir en arrière sur payments
    op.drop_constraint('fk_payments_invoice_payment_id', 'payments', type_='foreignkey')
    op.alter_column('payments', 'status',
               existing_type=sa.VARCHAR(length=20),
               comment=None,
               existing_comment='success, failed, pending, refunded, cancelled',
               existing_nullable=True)
    op.alter_column('payments', 'payment_method',
               existing_type=sa.VARCHAR(length=30),
               comment='cash, mobile_money, visa, bank_transfer, cheque, credit_note',
               existing_comment='cash, mobile_money, visa, mastercard, bank_transfer, cheque, credit_note',
               existing_nullable=False)
    op.drop_column('payments', 'invoice_payment_id')
    
    # 3. Revenir en arrière sur orders
    op.alter_column('orders', 'id',
               existing_type=sa.String(length=36),
               type_=sa.UUID(),
               existing_nullable=False)
    
    # 4. Revenir en arrière sur invoices
    op.create_foreign_key(op.f('invoices_tenant_id_fkey'), 'invoices', 'tenants', ['tenant_id'], ['id'])
    op.drop_constraint('uq_invoices_invoice_number', 'invoices', type_='unique')
    op.create_index(op.f('ix_invoices_invoice_number'), 'invoices', ['invoice_number'], unique=True)
    op.alter_column('invoices', 'subscription_plan',
               existing_type=sa.VARCHAR(length=50),
               comment="Plan d'abonnement facturé",
               existing_nullable=True)
    op.alter_column('invoices', 'currency',
               existing_type=sa.VARCHAR(length=3),
               nullable=True)
    op.alter_column('invoices', 'period_end',
               existing_type=postgresql.TIMESTAMP(),
               nullable=True)
    op.alter_column('invoices', 'period_start',
               existing_type=postgresql.TIMESTAMP(),
               nullable=True)
    
    # 5. Recréer la table invoice_items
    op.create_table('invoice_items',
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('invoice_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('tenant_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('description', sa.VARCHAR(length=500), autoincrement=False, nullable=False),
        sa.Column('product_id', sa.UUID(), autoincrement=False, nullable=True),
        sa.Column('item_type', sa.VARCHAR(length=20), autoincrement=False, nullable=True),
        sa.Column('quantity', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('unit_price', sa.NUMERIC(precision=15, scale=2), autoincrement=False, nullable=False),
        sa.Column('unit_of_measure', sa.VARCHAR(length=20), autoincrement=False, nullable=True),
        sa.Column('tax_rate', sa.NUMERIC(precision=5, scale=2), autoincrement=False, nullable=True),
        sa.Column('discount_percent', sa.NUMERIC(precision=5, scale=2), autoincrement=False, nullable=True),
        sa.Column('discount_amount', sa.NUMERIC(precision=15, scale=2), autoincrement=False, nullable=True),
        sa.Column('subtotal', sa.NUMERIC(precision=15, scale=2), autoincrement=False, nullable=False),
        sa.Column('tax_amount', sa.NUMERIC(precision=15, scale=2), autoincrement=False, nullable=False),
        sa.Column('total', sa.NUMERIC(precision=15, scale=2), autoincrement=False, nullable=False),
        sa.Column('affects_stock', sa.BOOLEAN(), autoincrement=False, nullable=True),
        sa.Column('stock_adjustment_id', sa.UUID(), autoincrement=False, nullable=True),
        sa.Column('notes', sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column('item_meta', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name='invoice_items_invoice_id_fkey'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], name='invoice_items_product_id_fkey'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='invoice_items_tenant_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='invoice_items_pkey')
    )