"""add_supplier_credit_management

Revision ID: fb9b513af5ad
Revises: d37cec63742e
Create Date: 2026-04-28 13:22:47.803128

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fb9b513af5ad'
down_revision: Union[str, Sequence[str], None] = 'd37cec63742e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajout des tables pour la gestion du crédit fournisseurs"""
    
    # =====================================
    # TABLE: supplier_credit_configs
    # Configuration du crédit par fournisseur
    # =====================================
    op.create_table('supplier_credit_configs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False, comment='Nom de la configuration'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default='false', comment='Configuration par défaut pour ce fournisseur'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('max_credit_amount', sa.Numeric(precision=15, scale=2), nullable=True, comment='Montant maximum de crédit autorisé'),
        sa.Column('max_credit_days', sa.Integer(), nullable=True, comment='Nombre maximum de jours de crédit'),
        sa.Column('interest_rate', sa.Numeric(precision=5, scale=2), server_default='0.00', comment="Taux d'intérêt annuel (%)"),
        sa.Column('late_fee_rate', sa.Numeric(precision=5, scale=2), server_default='0.00', comment='Taux de pénalité de retard (%)'),
        sa.Column('payment_frequency', sa.String(length=30), nullable=False),
        sa.Column('repayment_percentage_of_sale', sa.Numeric(precision=5, scale=2), server_default='30.00', comment='% de chaque vente alloué au remboursement'),
        sa.Column('min_repayment_amount', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Montant minimum de remboursement par période'),
        sa.Column('max_repayment_amount', sa.Numeric(precision=15, scale=2), nullable=True, comment='Montant maximum de remboursement par période'),
        sa.Column('custom_due_dates', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="Liste des dates d'échéance personnalisées"),
        sa.Column('grace_period_days', sa.Integer(), server_default='0', comment='Jours de grâce après échéance'),
        sa.Column('repayment_priority', sa.Integer(), server_default='1', comment='Priorité de remboursement (1=haute)'),
        sa.Column('auto_repayment_enabled', sa.Boolean(), server_default='true', comment='Remboursement automatique à chaque vente'),
        sa.Column('send_reminders', sa.Boolean(), server_default='true'),
        sa.Column('reminder_days_before', sa.Integer(), server_default='3', comment='Jours avant échéance pour rappel'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('meta_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.CheckConstraint('max_credit_amount IS NULL OR max_credit_amount >= 0', name='check_max_credit_positive'),
        sa.CheckConstraint('repayment_percentage_of_sale BETWEEN 0 AND 100', name='check_repayment_percentage'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_credit_configs_active', 'supplier_credit_configs', ['tenant_id', 'is_active'], unique=False)
    op.create_index('ix_supplier_credit_configs_default', 'supplier_credit_configs', ['supplier_id', 'is_default'], unique=False)
    op.create_index('ix_supplier_credit_configs_supplier', 'supplier_credit_configs', ['supplier_id'], unique=False)
    op.create_index(op.f('ix_supplier_credit_configs_supplier_id'), 'supplier_credit_configs', ['supplier_id'], unique=False)
    op.create_index(op.f('ix_supplier_credit_configs_tenant_id'), 'supplier_credit_configs', ['tenant_id'], unique=False)

    # =====================================
    # TABLE: supplier_debts
    # Dette globale par fournisseur
    # =====================================
    op.create_table('supplier_debts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('total_credit_amount', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Montant total du crédit accordé'),
        sa.Column('total_repaid_amount', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Montant total déjà remboursé'),
        sa.Column('current_debt', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Dette actuelle = total_credit - total_repaid'),
        sa.Column('accrued_interest', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Intérêts courus'),
        sa.Column('late_fees', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Pénalités de retard'),
        sa.Column('first_credit_date', sa.Date(), nullable=True, comment='Date du premier crédit'),
        sa.Column('last_repayment_date', sa.Date(), nullable=True, comment='Date du dernier remboursement'),
        sa.Column('next_due_date', sa.Date(), nullable=True, comment='Prochaine échéance'),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.CheckConstraint('total_credit_amount >= 0', name='check_total_credit_positive'),
        sa.CheckConstraint('total_repaid_amount >= 0', name='check_total_repaid_positive'),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_debts_next_due', 'supplier_debts', ['next_due_date'], unique=False)
    op.create_index(op.f('ix_supplier_debts_supplier_id'), 'supplier_debts', ['supplier_id'], unique=False)
    op.create_index('ix_supplier_debts_supplier_status', 'supplier_debts', ['supplier_id', 'status'], unique=False)
    op.create_index(op.f('ix_supplier_debts_tenant_id'), 'supplier_debts', ['tenant_id'], unique=False)
    op.create_index('ix_supplier_debts_tenant_supplier', 'supplier_debts', ['tenant_id', 'supplier_id'], unique=False)

    # =====================================
    # TABLE: purchase_credits
    # Achat spécifique à crédit
    # =====================================
    op.create_table('purchase_credits',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('purchase_id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('config_id', sa.UUID(), nullable=True),
        sa.Column('debt_id', sa.UUID(), nullable=False),
        sa.Column('credit_amount', sa.Numeric(precision=15, scale=2), nullable=False, comment='Montant crédité pour cet achat'),
        sa.Column('repaid_amount', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Montant déjà remboursé'),
        sa.Column('remaining_amount', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Reste à rembourser'),
        sa.Column('interest_rate_applied', sa.Numeric(precision=5, scale=2), server_default='0.00'),
        sa.Column('payment_frequency', sa.String(length=30), nullable=False),
        sa.Column('repayment_percentage', sa.Numeric(precision=5, scale=2), nullable=False, comment='% à rembourser par vente'),
        sa.Column('due_date', sa.Date(), nullable=False, comment="Date d'échéance"),
        sa.Column('grace_date', sa.Date(), nullable=True, comment='Date de grâce'),
        sa.Column('last_sale_trigger_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='active'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('meta_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.CheckConstraint('credit_amount >= 0', name='check_credit_amount_positive'),
        sa.ForeignKeyConstraint(['config_id'], ['supplier_credit_configs.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['debt_id'], ['supplier_debts.id'], ),
        sa.ForeignKeyConstraint(['purchase_id'], ['purchases.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_credits_due_date', 'purchase_credits', ['due_date'], unique=False)
    op.create_index('ix_purchase_credits_purchase', 'purchase_credits', ['purchase_id'], unique=False)
    op.create_index(op.f('ix_purchase_credits_purchase_id'), 'purchase_credits', ['purchase_id'], unique=False)
    op.create_index(op.f('ix_purchase_credits_supplier_id'), 'purchase_credits', ['supplier_id'], unique=False)
    op.create_index('ix_purchase_credits_supplier_status', 'purchase_credits', ['supplier_id', 'status'], unique=False)
    op.create_index(op.f('ix_purchase_credits_tenant_id'), 'purchase_credits', ['tenant_id'], unique=False)

    # =====================================
    # TABLE: product_credit_items
    # Produits achetés à crédit (traçabilité)
    # =====================================
    op.create_table('product_credit_items',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('purchase_credit_id', sa.UUID(), nullable=False),
        sa.Column('product_stock_id', sa.UUID(), nullable=True),
        sa.Column('product_name', sa.String(length=200), nullable=False),
        sa.Column('product_code', sa.String(length=50), nullable=False),
        sa.Column('batch_number', sa.String(length=100), nullable=True),
        sa.Column('ownership_status', sa.String(length=30), nullable=False, server_default='credit', comment='credit, fully_owned, partial_credit, consignment'),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('unit_cost', sa.Numeric(precision=15, scale=4), nullable=False, comment="Prix d'achat unitaire"),
        sa.Column('total_cost', sa.Numeric(precision=15, scale=2), nullable=False, comment='Coût total'),
        sa.Column('credit_portion', sa.Numeric(precision=15, scale=2), nullable=False, comment='Partie financée à crédit'),
        sa.Column('equity_portion', sa.Numeric(precision=15, scale=2), nullable=False, comment='Partie sur capital propre'),
        sa.Column('total_sold_quantity', sa.Integer(), server_default='0', comment='Quantité totale vendue'),
        sa.Column('remaining_quantity', sa.Integer(), server_default='0', comment='Quantité restante en stock'),
        sa.Column('amount_repaid_from_sales', sa.Numeric(precision=15, scale=2), server_default='0.00'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('is_fully_repaid', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.CheckConstraint('credit_portion >= 0', name='check_credit_portion_positive'),
        sa.CheckConstraint('quantity >= 0', name='check_quantity_positive'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.ForeignKeyConstraint(['product_stock_id'], ['product_stocks.id'], ),
        sa.ForeignKeyConstraint(['purchase_credit_id'], ['purchase_credits.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_product_credit_items_batch', 'product_credit_items', ['batch_number'], unique=False)
    op.create_index('ix_product_credit_items_product', 'product_credit_items', ['product_id', 'ownership_status'], unique=False)
    op.create_index(op.f('ix_product_credit_items_product_id'), 'product_credit_items', ['product_id'], unique=False)
    op.create_index('ix_product_credit_items_purchase', 'product_credit_items', ['purchase_credit_id'], unique=False)
    op.create_index(op.f('ix_product_credit_items_purchase_credit_id'), 'product_credit_items', ['purchase_credit_id'], unique=False)
    op.create_index('ix_product_credit_items_supplier_trace', 'product_credit_items', ['tenant_id', 'product_id', 'purchase_credit_id'], unique=False)
    op.create_index(op.f('ix_product_credit_items_tenant_id'), 'product_credit_items', ['tenant_id'], unique=False)

    # =====================================
    # TABLE: sale_credit_allocations
    # Allocation des ventes au remboursement
    # =====================================
    op.create_table('sale_credit_allocations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('sale_id', sa.UUID(), nullable=False),
        sa.Column('sale_item_id', sa.UUID(), nullable=False),
        sa.Column('product_credit_item_id', sa.UUID(), nullable=False),
        sa.Column('purchase_credit_id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('sale_amount', sa.Numeric(precision=15, scale=2), nullable=False, comment='Montant total de la vente'),
        sa.Column('allocated_repayment', sa.Numeric(precision=15, scale=2), nullable=False, comment='Montant alloué au remboursement'),
        sa.Column('capital_portion', sa.Numeric(precision=15, scale=2), nullable=False, comment='Partie intégrée au capital propre'),
        sa.Column('quantity_sold', sa.Integer(), nullable=False),
        sa.Column('unit_sale_price', sa.Numeric(precision=15, scale=4), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('processed_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('sale_date', sa.Date(), nullable=False),
        sa.CheckConstraint('allocated_repayment >= 0', name='check_repayment_positive'),
        sa.ForeignKeyConstraint(['product_credit_item_id'], ['product_credit_items.id'], ),
        sa.ForeignKeyConstraint(['purchase_credit_id'], ['purchase_credits.id'], ),
        sa.ForeignKeyConstraint(['sale_id'], ['sales.id'], ),
        sa.ForeignKeyConstraint(['sale_item_id'], ['sale_items.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sale_credit_allocations_date', 'sale_credit_allocations', ['sale_date'], unique=False)
    op.create_index('ix_sale_credit_allocations_product_credit', 'sale_credit_allocations', ['product_credit_item_id'], unique=False)
    op.create_index('ix_sale_credit_allocations_sale', 'sale_credit_allocations', ['sale_id'], unique=False)
    op.create_index(op.f('ix_sale_credit_allocations_sale_id'), 'sale_credit_allocations', ['sale_id'], unique=False)
    op.create_index('ix_sale_credit_allocations_supplier', 'sale_credit_allocations', ['supplier_id'], unique=False)
    op.create_index(op.f('ix_sale_credit_allocations_tenant_id'), 'sale_credit_allocations', ['tenant_id'], unique=False)

    # =====================================
    # TABLE: supplier_credit_transactions
    # Transaction de crédit (historique)
    # =====================================
    op.create_table('supplier_credit_transactions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('debt_id', sa.UUID(), nullable=False),
        sa.Column('supplier_id', sa.UUID(), nullable=False),
        sa.Column('transaction_type', sa.String(length=30), nullable=False, comment='credit_purchase, repayment_from_sale, manual_repayment, interest, late_fee, adjustment'),
        sa.Column('amount', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('amount_applied_to_principal', sa.Numeric(precision=15, scale=2), server_default='0.00'),
        sa.Column('amount_applied_to_interest', sa.Numeric(precision=15, scale=2), server_default='0.00'),
        sa.Column('amount_applied_to_fees', sa.Numeric(precision=15, scale=2), server_default='0.00'),
        sa.Column('balance_before', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('balance_after', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('sale_allocation_id', sa.UUID(), nullable=True),
        sa.Column('purchase_credit_id', sa.UUID(), nullable=True),
        sa.Column('payment_id', sa.UUID(), nullable=True, comment='ID du paiement manuel'),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('reference', sa.String(length=100), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['debt_id'], ['supplier_debts.id'], ),
        sa.ForeignKeyConstraint(['purchase_credit_id'], ['purchase_credits.id'], ),
        sa.ForeignKeyConstraint(['sale_allocation_id'], ['sale_credit_allocations.id'], ),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supplier_credit_transactions_debt', 'supplier_credit_transactions', ['debt_id', 'transaction_date'], unique=False)
    op.create_index(op.f('ix_supplier_credit_transactions_debt_id'), 'supplier_credit_transactions', ['debt_id'], unique=False)
    op.create_index('ix_supplier_credit_transactions_supplier', 'supplier_credit_transactions', ['supplier_id', 'transaction_date'], unique=False)
    op.create_index(op.f('ix_supplier_credit_transactions_supplier_id'), 'supplier_credit_transactions', ['supplier_id'], unique=False)
    op.create_index(op.f('ix_supplier_credit_transactions_tenant_id'), 'supplier_credit_transactions', ['tenant_id'], unique=False)
    op.create_index('ix_supplier_credit_transactions_type', 'supplier_credit_transactions', ['transaction_type'], unique=False)

    # =====================================
    # TABLE: adjusted_capitals
    # Capital ajusté (caisse - dettes fournisseurs)
    # =====================================
    op.create_table('adjusted_capitals',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('cash_in_hand', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Argent en caisse'),
        sa.Column('bank_balance', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Solde bancaire'),
        sa.Column('total_liquidities', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Total liquidités'),
        sa.Column('total_supplier_debt', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Total dettes fournisseurs'),
        sa.Column('gross_capital', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Capital brut (stock + équipement + caisse)'),
        sa.Column('adjusted_capital', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Capital réel = Liquidités - Dettes + Stock + Équipement'),
        sa.Column('equity_capital', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Capital propre = Actif - Dettes'),
        sa.Column('stock_value', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Valeur du stock'),
        sa.Column('equipment_value', sa.Numeric(precision=15, scale=2), server_default='0.00', comment="Valeur de l'équipement"),
        sa.Column('other_assets', sa.Numeric(precision=15, scale=2), server_default='0.00', comment='Autres actifs'),
        sa.Column('calculation_date', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('meta_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), onupdate=sa.text('now()')),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_adjusted_capitals_calculation_date'), 'adjusted_capitals', ['calculation_date'], unique=False)
    op.create_index('ix_adjusted_capitals_pharmacy_date', 'adjusted_capitals', ['pharmacy_id', 'calculation_date'], unique=False)
    op.create_index(op.f('ix_adjusted_capitals_pharmacy_id'), 'adjusted_capitals', ['pharmacy_id'], unique=False)
    op.create_index('ix_adjusted_capitals_tenant_date', 'adjusted_capitals', ['tenant_id', 'calculation_date'], unique=False)
    op.create_index(op.f('ix_adjusted_capitals_tenant_id'), 'adjusted_capitals', ['tenant_id'], unique=False)

    # =====================================
    # AJOUT DE COLONNES DANS LES TABLES EXISTANTES
    # =====================================
    
    # Table products
    op.add_column('products', sa.Column('ownership_status', sa.String(length=30), server_default='fully_owned', comment='Statut de propriété: fully_owned, credit, partial_credit, consignment'))
    op.add_column('products', sa.Column('has_credit_portion', sa.Boolean(), server_default='false', comment='Le produit a-t-il une partie à crédit?'))
    
    # Table purchases
    op.add_column('purchases', sa.Column('is_credit_purchase', sa.Boolean(), server_default='false', comment='Achat effectué à crédit'))
    op.add_column('purchases', sa.Column('credit_config_id', sa.UUID(), nullable=True))
    op.create_foreign_key(None, 'purchases', 'supplier_credit_configs', ['credit_config_id'], ['id'])


def downgrade() -> None:
    """Suppression des tables de gestion du crédit fournisseurs"""
    
    # Suppression des colonnes ajoutées
    op.drop_column('purchases', 'credit_config_id')
    op.drop_column('purchases', 'is_credit_purchase')
    op.drop_column('products', 'has_credit_portion')
    op.drop_column('products', 'ownership_status')
    
    # Suppression des tables dans l'ordre inverse des dépendances
    op.drop_table('supplier_credit_transactions')
    op.drop_table('sale_credit_allocations')
    op.drop_table('product_credit_items')
    op.drop_table('purchase_credits')
    op.drop_table('supplier_debts')
    op.drop_table('supplier_credit_configs')
    op.drop_table('adjusted_capitals')