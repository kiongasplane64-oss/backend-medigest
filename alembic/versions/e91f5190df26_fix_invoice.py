"""fix_invoice

Revision ID: e91f5190df26
Revises: 130183be23cf
Create Date: 2026-04-07 12:00:23.475925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'e91f5190df26'
down_revision: Union[str, Sequence[str], None] = '130183be23cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - version simplifiée sans ENUM"""
    
    conn = op.get_bind()
    
    # 1. Ajouter les nouvelles colonnes à invoices si elles n'existent pas
    try:
        op.add_column('invoices', sa.Column('pharmacy_id', sa.UUID(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('period_start', sa.DateTime(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('period_end', sa.DateTime(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('tax_rate', sa.Float(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('tax_amount', sa.Float(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('discount_amount', sa.Float(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('description', sa.Text(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('billing_cycle', sa.String(length=20), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('payment_method', sa.String(length=50), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('payment_reference', sa.String(length=100), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('invoices', sa.Column('invoice_metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    except Exception:
        pass
    
    # 2. Modifier les colonnes existantes sans utiliser ENUM
    # Garder invoice_type comme VARCHAR
    op.execute("ALTER TABLE invoices ALTER COLUMN invoice_type TYPE VARCHAR(50)")
    op.execute("ALTER TABLE invoices ALTER COLUMN invoice_type SET DEFAULT 'subscription'")
    
    # Garder status comme VARCHAR
    op.execute("ALTER TABLE invoices ALTER COLUMN status TYPE VARCHAR(30)")
    op.execute("ALTER TABLE invoices ALTER COLUMN status SET DEFAULT 'sent'")
    
    # Modifier les types de colonnes
    op.execute("ALTER TABLE invoices ALTER COLUMN subtotal TYPE FLOAT USING subtotal::FLOAT")
    op.execute("ALTER TABLE invoices ALTER COLUMN total_amount TYPE FLOAT USING total_amount::FLOAT")
    
    # Modifier les dates
    op.execute("ALTER TABLE invoices ALTER COLUMN issue_date TYPE TIMESTAMP USING issue_date::TIMESTAMP")
    op.execute("ALTER TABLE invoices ALTER COLUMN due_date TYPE TIMESTAMP USING due_date::TIMESTAMP")
    op.execute("ALTER TABLE invoices ALTER COLUMN due_date SET NOT NULL")
    
    # 3. Mettre à jour les foreign keys
    try:
        op.create_foreign_key(None, 'invoices', 'pharmacies', ['pharmacy_id'], ['id'], ondelete='CASCADE')
    except Exception:
        pass
    
    try:
        op.create_foreign_key(None, 'invoices', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
    except Exception:
        pass
    
    # 4. Supprimer les colonnes inutiles si elles existent
    columns_to_drop = [
        'invoice_sequence', 'parent_invoice_id', 'invoice_meta', 'sent_at',
        'created_by', 'email_recipients', 'invoice_year', 'pdf_path',
        'subscription_end', 'tax_exempt', 'client_email', 'shipping_amount',
        'notes', 'client_address', 'payment_gateway_id', 'email_sent',
        'is_recurring', 'xml_path', 'subscription_start', 'client_name',
        'tax_details', 'cancelled_at', 'client_phone', 'archived_at',
        'payment_status', 'email_sent_at', 'client_tax_id', 'terms',
        'sale_id', 'payment_methods', 'client_id', 'invoice_prefix',
        'footer', 'total_discount', 'is_credit_note', 'recurring_interval',
        'receipt_path', 'credit_note_for', 'subscription_period', 'amount_paid',
        'next_invoice_date', 'payment_gateway', 'total_tax', 'payment_gateway_status'
    ]
    
    for col in columns_to_drop:
        try:
            op.drop_column('invoices', col)
        except Exception:
            pass
    
    # 5. Mettre à jour subscription_codes
    try:
        op.add_column('subscription_codes', sa.Column('activated_for_pharmacy_id', sa.UUID(), nullable=True))
    except Exception:
        pass
    
    try:
        op.add_column('subscription_codes', sa.Column('config', sa.JSON(), nullable=True))
    except Exception:
        pass
    
    # 6. Rendre pharmacy_id NOT NULL après migration
    op.execute("UPDATE invoices SET pharmacy_id = (SELECT id FROM pharmacies LIMIT 1) WHERE pharmacy_id IS NULL")
    op.alter_column('invoices', 'pharmacy_id', nullable=False)


def downgrade() -> None:
    """Downgrade schema - version simplifiée"""
    
    # Supprimer les foreign keys
    try:
        op.drop_constraint(op.f('invoices_pharmacy_id_fkey'), 'invoices', type_='foreignkey')
    except Exception:
        pass
    
    try:
        op.drop_constraint(op.f('invoices_tenant_id_fkey'), 'invoices', type_='foreignkey')
    except Exception:
        pass
    
    # Supprimer les colonnes ajoutées
    columns_to_drop = [
        'pharmacy_id', 'period_start', 'period_end', 'tax_rate', 'tax_amount',
        'discount_amount', 'description', 'billing_cycle', 'payment_method',
        'payment_reference', 'invoice_metadata'
    ]
    
    for col in columns_to_drop:
        try:
            op.drop_column('invoices', col)
        except Exception:
            pass
    
    # Restaurer les types d'origine
    op.execute("ALTER TABLE invoices ALTER COLUMN subtotal TYPE NUMERIC(15,2)")
    op.execute("ALTER TABLE invoices ALTER COLUMN total_amount TYPE NUMERIC(15,2)")
    op.execute("ALTER TABLE invoices ALTER COLUMN issue_date TYPE DATE")
    op.execute("ALTER TABLE invoices ALTER COLUMN due_date TYPE DATE")
    op.execute("ALTER TABLE invoices ALTER COLUMN due_date DROP NOT NULL")
    
    # Supprimer les colonnes de subscription_codes
    try:
        op.drop_column('subscription_codes', 'activated_for_pharmacy_id')
    except Exception:
        pass
    
    try:
        op.drop_column('subscription_codes', 'config')
    except Exception:
        pass