"""add_department_project_capital_models

Revision ID: 6907bda26f75
Revises: 5943767253be
Create Date: 2026-05-03 07:08:03.014106

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '6907bda26f75'
down_revision: Union[str, Sequence[str], None] = '5943767253be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    # ==============================================
    # CRÉATION DES TABLES
    # ==============================================
    
    # Table cost_allocations
    op.create_table('cost_allocations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('cost_id', sa.UUID(), nullable=False),
        sa.Column('department_id', sa.UUID(), nullable=True),
        sa.Column('project_id', sa.UUID(), nullable=True),
        sa.Column('allocated_amount', sa.DECIMAL(precision=15, scale=2), nullable=False, comment='Montant alloué'),
        sa.Column('allocation_percentage', sa.DECIMAL(precision=5, scale=2), nullable=True, comment="Pourcentage d'allocation"),
        sa.Column('status', sa.String(length=20), nullable=True, comment='pending, approved, rejected, cancelled'),
        sa.Column('notes', sa.Text(), nullable=True, comment="Justification de l'allocation"),
        sa.Column('allocation_reference', sa.String(length=100), nullable=True, comment='Référence externe'),
        sa.Column('approved_by', sa.UUID(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('allocation_date', sa.Date(), nullable=False, comment="Date d'allocation"),
        sa.Column('start_date', sa.Date(), nullable=True, comment='Date de début de validité'),
        sa.Column('end_date', sa.Date(), nullable=True, comment='Date de fin de validité'),
        sa.Column('allocation_metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('created_by', sa.UUID(), nullable=False),
        sa.CheckConstraint('allocated_amount >= 0', name='check_allocated_amount_positive'),
        sa.CheckConstraint('allocation_percentage IS NULL OR (allocation_percentage >= 0 AND allocation_percentage <= 100)', name='check_allocation_percentage_range'),
        sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['cost_id'], ['costs.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_cost_allocations_cost', 'cost_allocations', ['cost_id'], unique=False)
    op.create_index('ix_cost_allocations_date', 'cost_allocations', ['allocation_date'], unique=False)
    op.create_index('ix_cost_allocations_department', 'cost_allocations', ['department_id'], unique=False)
    op.create_index('ix_cost_allocations_project', 'cost_allocations', ['project_id'], unique=False)
    op.create_index('ix_cost_allocations_status', 'cost_allocations', ['status'], unique=False)
    op.create_index('ix_cost_allocations_tenant_cost', 'cost_allocations', ['tenant_id', 'cost_id'], unique=False)
    
    # ==============================================
    # MODIFICATION DE adjusted_capitals
    # ==============================================
    op.add_column('adjusted_capitals', sa.Column('branch_id', sa.UUID(), nullable=True))
    op.add_column('adjusted_capitals', sa.Column('capital_id', sa.UUID(), nullable=True))
    op.add_column('adjusted_capitals', sa.Column('other_liabilities', sa.Numeric(precision=15, scale=2), server_default='0', nullable=False, comment='Autres dettes'))
    op.add_column('adjusted_capitals', sa.Column('debt_ratio', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False, comment="Ratio d'endettement en %"))
    op.add_column('adjusted_capitals', sa.Column('liquidity_ratio', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False, comment='Ratio de liquidité'))
    op.add_column('adjusted_capitals', sa.Column('period_start', sa.Date(), nullable=True))
    op.add_column('adjusted_capitals', sa.Column('period_end', sa.Date(), nullable=True))
    op.add_column('adjusted_capitals', sa.Column('is_current', sa.Boolean(), server_default='true', nullable=True, comment='Version la plus récente'))
    op.add_column('adjusted_capitals', sa.Column('calculation_version', sa.Integer(), server_default='1', nullable=True, comment='Version du calcul'))
    op.add_column('adjusted_capitals', sa.Column('calculation_method', sa.String(length=50), server_default='auto', nullable=True, comment='auto, manual'))
    op.add_column('adjusted_capitals', sa.Column('calculated_by', sa.UUID(), nullable=True))
    
    # Modifier les commentaires des colonnes existantes
    op.alter_column('adjusted_capitals', 'cash_in_hand', comment='Argent en caisse (531)')
    op.alter_column('adjusted_capitals', 'bank_balance', comment='Solde bancaire (57)')
    op.alter_column('adjusted_capitals', 'stock_value', comment='Valeur du stock (31)')
    op.alter_column('adjusted_capitals', 'equipment_value', comment="Valeur de l'équipement (23)")
    op.alter_column('adjusted_capitals', 'total_supplier_debt', comment='Total dettes fournisseurs (40)')
    op.alter_column('adjusted_capitals', 'gross_capital', comment='Capital brut = Total actifs')
    op.alter_column('adjusted_capitals', 'adjusted_capital', comment='Capital réel = Actif total - Dettes')
    op.alter_column('adjusted_capitals', 'equity_capital', comment='Capitaux propres')
    
    # Créer les index
    op.create_index('ix_adjusted_capitals_capital', 'adjusted_capitals', ['capital_id'], unique=False)
    op.create_index('ix_adjusted_capitals_current', 'adjusted_capitals', ['tenant_id', 'pharmacy_id', 'is_current'], unique=False)
    op.create_index('ix_adjusted_capitals_period', 'adjusted_capitals', ['period_start', 'period_end'], unique=False)
    
    # Créer les foreign keys
    op.create_foreign_key(None, 'adjusted_capitals', 'capitals', ['capital_id'], ['id'])
    op.create_foreign_key(None, 'adjusted_capitals', 'users', ['calculated_by'], ['id'])
    op.create_foreign_key(None, 'adjusted_capitals', 'branches', ['branch_id'], ['id'])
    
    # ==============================================
    # MODIFICATION DE budgets
    # ==============================================
    op.add_column('budgets', sa.Column('department_id', sa.UUID(), nullable=True))
    op.add_column('budgets', sa.Column('project_id', sa.UUID(), nullable=True))
    op.create_index('ix_budgets_department', 'budgets', ['department_id'], unique=False)
    op.create_index('ix_budgets_project', 'budgets', ['project_id'], unique=False)
    op.create_foreign_key(None, 'budgets', 'projects', ['project_id'], ['id'])
    op.create_foreign_key(None, 'budgets', 'departments', ['department_id'], ['id'])
    
    # ==============================================
    # MODIFICATION DE costs
    # ==============================================
    op.add_column('costs', sa.Column('department_id', sa.UUID(), nullable=True))
    op.add_column('costs', sa.Column('project_id', sa.UUID(), nullable=True))
    op.create_index('ix_costs_tenant_department', 'costs', ['tenant_id', 'department_id'], unique=False)
    op.create_index('ix_costs_tenant_project', 'costs', ['tenant_id', 'project_id'], unique=False)
    op.create_foreign_key(None, 'costs', 'projects', ['project_id'], ['id'])
    op.create_foreign_key(None, 'costs', 'departments', ['department_id'], ['id'])
    
    # ==============================================
    # SUPPRESSION DES TABLES OBSOLÈTES
    # ==============================================
    # Supprimer invoice_counters et invoice_sequences (remplacées par invoice_counter)
    op.drop_index('idx_invoice_counters_date', table_name='invoice_counters', if_exists=True)
    op.drop_index('idx_invoice_counters_pharmacy', table_name='invoice_counters', if_exists=True)
    op.drop_index('idx_invoice_counters_tenant', table_name='invoice_counters', if_exists=True)
    op.drop_index('unique_pharmacy_date', table_name='invoice_counters', if_exists=True)
    op.drop_table('invoice_counters', if_exists=True)
    
    op.drop_index('idx_invoice_sequences_lookup', table_name='invoice_sequences', if_exists=True)
    op.drop_table('invoice_sequences', if_exists=True)
    
    # ==============================================
    # CORRECTIONS POUR subscription_codes
    # ==============================================
    op.add_column('subscription_codes', sa.Column('pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('subscription_codes', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.alter_column('subscription_codes', 'notes', type_=sa.Text(), existing_nullable=True)
    op.create_index(op.f('ix_subscription_codes_branch_id'), 'subscription_codes', ['branch_id'], unique=False)
    op.create_foreign_key(None, 'subscription_codes', 'pharmacies', ['pharmacy_id'], ['id'])
    op.create_foreign_key(None, 'subscription_codes', 'branches', ['branch_id'], ['id'])
    
    # Supprimer les colonnes existantes si elles existent
    for col in ['activated_for_pharmacy_id', 'user_id', 'is_active', 'config']:
        try:
            op.drop_column('subscription_codes', col)
        except Exception:
            pass
    
    # ==============================================
    # CRÉATION DES INDEX POUR returns
    # ==============================================
    op.create_index('ix_returns_customer_id', 'returns', ['customer_id'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_return_date', 'returns', ['return_date'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_return_number', 'returns', ['return_number'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_sale_id', 'returns', ['sale_id'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_status_date', 'returns', ['status', 'return_date'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_supplier_id', 'returns', ['supplier_id'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_sync_status', 'returns', ['is_synced', 'sync_version'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_tenant_branch', 'returns', ['tenant_id', 'branch_id'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_tenant_status', 'returns', ['tenant_id', 'status'], unique=False, if_not_exists=True)
    op.create_index('ix_returns_tenant_type', 'returns', ['tenant_id', 'return_type'], unique=False, if_not_exists=True)
    
    # ==============================================
    # CRÉATION DES INDEX POUR return_items
    # ==============================================
    op.create_index('ix_return_items_batch', 'return_items', ['batch_number'], unique=False, if_not_exists=True)
    op.create_index('ix_return_items_product_id', 'return_items', ['product_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_return_items_return_id'), 'return_items', ['return_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_return_items_sale_item_id'), 'return_items', ['sale_item_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_return_items_tenant_id'), 'return_items', ['tenant_id'], unique=False, if_not_exists=True)


def downgrade() -> None:
    """Downgrade schema."""
    
    # ==============================================
    # SUPPRESSION DES INDEX return_items
    # ==============================================
    op.drop_index(op.f('ix_return_items_tenant_id'), table_name='return_items', if_exists=True)
    op.drop_index(op.f('ix_return_items_sale_item_id'), table_name='return_items', if_exists=True)
    op.drop_index(op.f('ix_return_items_return_id'), table_name='return_items', if_exists=True)
    op.drop_index('ix_return_items_product_id', table_name='return_items', if_exists=True)
    op.drop_index('ix_return_items_batch', table_name='return_items', if_exists=True)
    
    # ==============================================
    # SUPPRESSION DES INDEX returns
    # ==============================================
    op.drop_index('ix_returns_tenant_type', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_tenant_status', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_tenant_branch', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_sync_status', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_supplier_id', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_status_date', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_sale_id', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_return_number', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_return_date', table_name='returns', if_exists=True)
    op.drop_index('ix_returns_customer_id', table_name='returns', if_exists=True)
    
    # ==============================================
    # RESTAURATION subscription_codes
    # ==============================================
    op.add_column('subscription_codes', sa.Column('config', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('subscription_codes', sa.Column('is_active', sa.Boolean(), nullable=True))
    op.add_column('subscription_codes', sa.Column('user_id', sa.UUID(), nullable=True))
    op.add_column('subscription_codes', sa.Column('activated_for_pharmacy_id', sa.UUID(), nullable=True))
    op.drop_constraint(None, 'subscription_codes', type_='foreignkey', if_exists=True)
    op.drop_constraint(None, 'subscription_codes', type_='foreignkey', if_exists=True)
    op.drop_index(op.f('ix_subscription_codes_branch_id'), table_name='subscription_codes', if_exists=True)
    op.alter_column('subscription_codes', 'notes', type_=sa.VARCHAR(length=500), existing_nullable=True)
    op.drop_column('subscription_codes', 'updated_at', if_exists=True)
    op.drop_column('subscription_codes', 'pharmacy_id', if_exists=True)
    
    # ==============================================
    # RESTAURATION des tables invoice
    # ==============================================
    op.create_table('invoice_sequences',
        sa.Column('id', sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('sequence_date', sa.DATE(), server_default=sa.text('CURRENT_DATE'), nullable=False),
        sa.Column('current_value', sa.INTEGER(), server_default=sa.text('1'), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', postgresql.TIMESTAMP(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_invoice_sequences_lookup', 'invoice_sequences', ['tenant_id', 'pharmacy_id', 'sequence_date'], unique=False)
    
    op.create_table('invoice_counters',
        sa.Column('id', sa.TEXT(), nullable=False),
        sa.Column('tenant_id', sa.TEXT(), nullable=False),
        sa.Column('pharmacy_id', sa.TEXT(), nullable=False),
        sa.Column('date', sa.TEXT(), nullable=False),
        sa.Column('current_number', sa.INTEGER(), server_default=sa.text('1'), nullable=True),
        sa.Column('created_at', sa.TEXT(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.TEXT(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('unique_pharmacy_date', 'invoice_counters', ['pharmacy_id', 'date'], unique=True)
    op.create_index('idx_invoice_counters_tenant', 'invoice_counters', ['tenant_id'], unique=False)
    op.create_index('idx_invoice_counters_pharmacy', 'invoice_counters', ['pharmacy_id'], unique=False)
    op.create_index('idx_invoice_counters_date', 'invoice_counters', ['date'], unique=False)
    
    # ==============================================
    # SUPPRESSION DES COLONNES costs
    # ==============================================
    op.drop_constraint(None, 'costs', type_='foreignkey', if_exists=True)
    op.drop_constraint(None, 'costs', type_='foreignkey', if_exists=True)
    op.drop_index('ix_costs_tenant_project', table_name='costs', if_exists=True)
    op.drop_index('ix_costs_tenant_department', table_name='costs', if_exists=True)
    op.drop_column('costs', 'project_id', if_exists=True)
    op.drop_column('costs', 'department_id', if_exists=True)
    
    # ==============================================
    # SUPPRESSION DES COLONNES budgets
    # ==============================================
    op.drop_constraint(None, 'budgets', type_='foreignkey', if_exists=True)
    op.drop_constraint(None, 'budgets', type_='foreignkey', if_exists=True)
    op.drop_index('ix_budgets_project', table_name='budgets', if_exists=True)
    op.drop_index('ix_budgets_department', table_name='budgets', if_exists=True)
    op.drop_column('budgets', 'project_id', if_exists=True)
    op.drop_column('budgets', 'department_id', if_exists=True)
    
    # ==============================================
    # SUPPRESSION DES COLONNES adjusted_capitals
    # ==============================================
    op.drop_constraint(None, 'adjusted_capitals', type_='foreignkey', if_exists=True)
    op.drop_constraint(None, 'adjusted_capitals', type_='foreignkey', if_exists=True)
    op.drop_constraint(None, 'adjusted_capitals', type_='foreignkey', if_exists=True)
    op.drop_index('ix_adjusted_capitals_period', table_name='adjusted_capitals', if_exists=True)
    op.drop_index('ix_adjusted_capitals_current', table_name='adjusted_capitals', if_exists=True)
    op.drop_index('ix_adjusted_capitals_capital', table_name='adjusted_capitals', if_exists=True)
    
    op.drop_column('adjusted_capitals', 'calculated_by', if_exists=True)
    op.drop_column('adjusted_capitals', 'calculation_method', if_exists=True)
    op.drop_column('adjusted_capitals', 'calculation_version', if_exists=True)
    op.drop_column('adjusted_capitals', 'is_current', if_exists=True)
    op.drop_column('adjusted_capitals', 'period_end', if_exists=True)
    op.drop_column('adjusted_capitals', 'period_start', if_exists=True)
    op.drop_column('adjusted_capitals', 'liquidity_ratio', if_exists=True)
    op.drop_column('adjusted_capitals', 'debt_ratio', if_exists=True)
    op.drop_column('adjusted_capitals', 'other_liabilities', if_exists=True)
    op.drop_column('adjusted_capitals', 'capital_id', if_exists=True)
    op.drop_column('adjusted_capitals', 'branch_id', if_exists=True)
    
    # Restaurer les commentaires originaux
    op.alter_column('adjusted_capitals', 'cash_in_hand', comment='Argent en caisse')
    op.alter_column('adjusted_capitals', 'bank_balance', comment='Solde bancaire')
    op.alter_column('adjusted_capitals', 'stock_value', comment='Valeur du stock')
    op.alter_column('adjusted_capitals', 'equipment_value', comment="Valeur de l'équipement")
    op.alter_column('adjusted_capitals', 'total_supplier_debt', comment='Total dettes fournisseurs')
    op.alter_column('adjusted_capitals', 'gross_capital', comment='Capital brut (stock + équipement + caisse)')
    op.alter_column('adjusted_capitals', 'adjusted_capital', comment='Capital réel = Liquidités - Dettes + Stock + Équipement')
    op.alter_column('adjusted_capitals', 'equity_capital', comment='Capital propre = Actif - Dettes')
    
    # ==============================================
    # SUPPRESSION DE cost_allocations
    # ==============================================
    op.drop_index('ix_cost_allocations_tenant_cost', table_name='cost_allocations', if_exists=True)
    op.drop_index('ix_cost_allocations_status', table_name='cost_allocations', if_exists=True)
    op.drop_index('ix_cost_allocations_project', table_name='cost_allocations', if_exists=True)
    op.drop_index('ix_cost_allocations_department', table_name='cost_allocations', if_exists=True)
    op.drop_index('ix_cost_allocations_date', table_name='cost_allocations', if_exists=True)
    op.drop_index('ix_cost_allocations_cost', table_name='cost_allocations', if_exists=True)
    op.drop_table('cost_allocations', if_exists=True)