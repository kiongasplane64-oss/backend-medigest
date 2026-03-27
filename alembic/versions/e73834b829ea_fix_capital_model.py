"""fix_capital_model

Revision ID: e73834b829ea
Revises: 6110943ce2bb
Create Date: 2026-03-27 08:34:20.735404

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e73834b829ea'
down_revision: Union[str, Sequence[str], None] = '6110943ce2bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Fix capital model structure."""
    
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # ============================================
    # 1. SKIP ORDERS TABLE MODIFICATION - NOT NEEDED
    # ============================================
    # ❌ COMMENTEZ TOUTE LA SECTION ORDERS
    # Les colonnes sont déjà en UUID, pas besoin de les modifier
    
    # ============================================
    # 2. MODIFY EXISTING TABLES (ADD COMMENTS)
    # ============================================
    
    # Capital accounts table - add comments
    with op.batch_alter_table('capital_accounts') as batch_op:
        batch_op.alter_column(
            'account_code',
            comment='Code du compte SYSCOHADA (101, 531, 31, etc.)',
            existing_type=sa.VARCHAR(length=20),
            existing_nullable=False
        )
        batch_op.alter_column(
            'account_name',
            comment='Nom du compte',
            existing_type=sa.VARCHAR(length=200),
            existing_nullable=False
        )
        batch_op.alter_column(
            'account_type',
            comment='Type de compte: asset, liability, equity, income, expense',
            existing_type=sa.VARCHAR(length=50),
            existing_nullable=False
        )
        batch_op.alter_column(
            'balance',
            comment='Solde du compte',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'debit',
            comment='Total débit',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'credit',
            comment='Total crédit',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'period_year',
            comment='Année comptable',
            existing_type=sa.INTEGER(),
            existing_nullable=False
        )
        batch_op.alter_column(
            'period_month',
            comment='Mois comptable (optionnel)',
            existing_type=sa.INTEGER(),
            existing_nullable=True
        )
        
        # Add indexes if they don't exist
        try:
            batch_op.create_index('ix_capital_accounts_branch_id', ['branch_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capital_accounts_pharmacy_id', ['pharmacy_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capital_accounts_tenant_id', ['tenant_id'], unique=False)
        except:
            pass

    # Capital transactions table - add comments and indexes
    with op.batch_alter_table('capital_transactions') as batch_op:
        batch_op.alter_column(
            'transaction_type',
            comment='initial, increase, decrease, profit_added, loss_deducted',
            existing_type=sa.VARCHAR(length=50),
            existing_nullable=False
        )
        batch_op.alter_column(
            'transaction_category',
            comment='cash, stock, equipment, other, turnover, expense',
            existing_type=sa.VARCHAR(length=50),
            existing_nullable=False
        )
        batch_op.alter_column(
            'amount',
            comment='Montant de la transaction',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'previous_capital',
            comment='Capital avant transaction',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'new_capital',
            comment='Capital après transaction',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'reference_id',
            comment='ID de référence (sale, purchase, etc.)',
            existing_type=sa.UUID(),
            existing_nullable=True
        )
        batch_op.alter_column(
            'reference_type',
            comment='sale, purchase, expense, investment',
            existing_type=sa.VARCHAR(length=50),
            existing_nullable=True
        )
        batch_op.alter_column(
            'description',
            comment='Description de la transaction',
            existing_type=sa.VARCHAR(length=500),
            existing_nullable=True
        )
        batch_op.alter_column(
            'notes',
            comment='Notes supplémentaires',
            existing_type=sa.TEXT(),
            existing_nullable=True
        )
        batch_op.alter_column(
            'transaction_date',
            comment='Date de la transaction',
            existing_type=sa.DATE(),
            existing_nullable=False
        )
        batch_op.alter_column(
            'created_by',
            comment='Utilisateur ayant créé la transaction',
            existing_type=sa.UUID(),
            existing_nullable=True
        )
        
        # Add indexes
        try:
            batch_op.create_index('ix_capital_transactions_branch_id', ['branch_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capital_transactions_capital_id', ['capital_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capital_transactions_created_at', ['created_at'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capital_transactions_pharmacy_id', ['pharmacy_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capital_transactions_tenant_id', ['tenant_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index(
                'ix_capital_transactions_tenant_pharmacy',
                ['tenant_id', 'pharmacy_id', 'transaction_date'],
                unique=False
            )
        except:
            pass

    # ============================================
    # 3. RESTRUCTURE CAPITALS TABLE
    # ============================================
    
    # First, check if columns exist
    columns = {col['name'] for col in inspector.get_columns('capitals')}
    
    # Add new columns
    if 'pharmacy_id' not in columns:
        op.add_column('capitals', sa.Column('pharmacy_id', sa.UUID(), nullable=True))  # nullable=True temporarily
        # Update existing records to set a default pharmacy_id if needed
        # Then alter to nullable=False
    
    if 'branch_id' not in columns:
        op.add_column('capitals', sa.Column('branch_id', sa.UUID(), nullable=True))
    
    if 'initial_capital' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'initial_capital',
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                server_default='0',
                comment='Capital initial (1011)'
            )
        )
    
    if 'current_capital' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'current_capital',
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                server_default='0',
                comment='Capital actuel'
            )
        )
    
    if 'cash_capital' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'cash_capital',
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                server_default='0',
                comment='Capital en caisse (531)'
            )
        )
    
    if 'stock_capital' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'stock_capital',
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                server_default='0',
                comment='Capital en stock (31)'
            )
        )
    
    if 'equipment_capital' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'equipment_capital',
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                server_default='0',
                comment='Capital en équipement (23)'
            )
        )
    
    if 'other_capital' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'other_capital',
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                server_default='0',
                comment='Autres capitaux'
            )
        )
    
    if 'start_date' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'start_date',
                sa.Date(),
                nullable=True,  # Temporarily nullable
                comment='Date de début du capital'
            )
        )
        # Update with current date
        op.execute("UPDATE capitals SET start_date = CURRENT_DATE WHERE start_date IS NULL")
        op.alter_column('capitals', 'start_date', nullable=False)
    
    if 'last_update_date' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'last_update_date',
                sa.Date(),
                nullable=True,
                comment='Date de dernière mise à jour'
            )
        )
        op.execute("UPDATE capitals SET last_update_date = CURRENT_DATE WHERE last_update_date IS NULL")
        op.alter_column('capitals', 'last_update_date', nullable=False)
    
    if 'notes' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'notes',
                sa.Text(),
                nullable=True,
                comment='Notes sur le capital'
            )
        )
    
    if 'meta_data' not in columns:
        op.add_column(
            'capitals',
            sa.Column(
                'meta_data',
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
                comment='Métadonnées supplémentaires'
            )
        )
    
    # Add indexes
    with op.batch_alter_table('capitals') as batch_op:
        try:
            batch_op.create_index('ix_capitals_branch_id', ['branch_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_created_at', ['created_at'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_last_update', ['last_update_date'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_pharmacy_id', ['pharmacy_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_start_date', ['start_date'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_tenant_branch', ['tenant_id', 'branch_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_tenant_id', ['tenant_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_capitals_tenant_pharmacy', ['tenant_id', 'pharmacy_id'], unique=False)
        except:
            pass
    
    # Add foreign keys
    try:
        op.create_foreign_key('fk_capitals_pharmacy', 'capitals', 'pharmacies', ['pharmacy_id'], ['id'])
    except Exception as e:
        print(f"Warning: Could not create pharmacy foreign key: {e}")
    
    try:
        op.create_foreign_key('fk_capitals_branch', 'capitals', 'branches', ['branch_id'], ['id'])
    except Exception as e:
        print(f"Warning: Could not create branch foreign key: {e}")
    
    # Drop old columns (only if they exist)
    old_columns = ['amount', 'source', 'approved_at', 'status', 'approved_by', 
                   'reference', 'description', 'destination', 'capital_date', 'capital_type']
    for col in old_columns:
        if col in columns:
            op.drop_column('capitals', col)

    # ============================================
    # 4. MODIFY TENANTS TABLE
    # ============================================
    
    with op.batch_alter_table('tenants') as batch_op:
        # Drop and recreate unique index
        try:
            batch_op.drop_index('ix_tenants_slug')
        except:
            pass
        try:
            batch_op.create_index('ix_tenants_slug', ['slug'], unique=True)
        except:
            pass
    
    # ============================================
    # 5. MODIFY TURNOVERS TABLE
    # ============================================
    
    with op.batch_alter_table('turnovers') as batch_op:
        batch_op.alter_column(
            'total_turnover',
            comment='CA total TTC',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'net_turnover',
            comment='CA net HT',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'tax_amount',
            comment='Montant TVA',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'discount_amount',
            comment='Montant remises',
            existing_type=sa.NUMERIC(precision=15, scale=2),
            existing_nullable=False
        )
        batch_op.alter_column(
            'sales_count',
            comment='Nombre de ventes',
            existing_type=sa.INTEGER(),
            existing_nullable=False
        )
        batch_op.alter_column(
            'items_sold',
            comment="Nombre d'articles vendus",
            existing_type=sa.INTEGER(),
            existing_nullable=False
        )
        batch_op.alter_column(
            'period_date',
            comment='Date de début de période',
            existing_type=sa.DATE(),
            existing_nullable=False
        )
        batch_op.alter_column(
            'period_type',
            comment='day, week, month, year',
            existing_type=sa.VARCHAR(length=20),
            existing_nullable=False
        )
        
        # Add indexes
        try:
            batch_op.create_index('ix_turnovers_branch_id', ['branch_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_turnovers_period_date', ['period_date'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_turnovers_period_type', ['period_type'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_turnovers_pharmacy_id', ['pharmacy_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('ix_turnovers_tenant_id', ['tenant_id'], unique=False)
        except:
            pass
    
    # ============================================
    # 6. MODIFY USER SESSIONS TABLE
    # ============================================
    
    with op.batch_alter_table('user_sessions') as batch_op:
        try:
            batch_op.create_index('idx_user_sessions_active_branch', ['active_branch_id'], unique=False)
        except:
            pass
        try:
            batch_op.create_index('idx_user_sessions_active_pharmacy', ['active_pharmacy_id'], unique=False)
        except:
            pass


def downgrade() -> None:
    """Downgrade schema - Revert changes."""
    
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # ============================================
    # 1. REVERT USER SESSIONS
    # ============================================
    
    with op.batch_alter_table('user_sessions') as batch_op:
        try:
            batch_op.drop_index('idx_user_sessions_active_pharmacy')
        except:
            pass
        try:
            batch_op.drop_index('idx_user_sessions_active_branch')
        except:
            pass
    
    # ============================================
    # 2. REVERT TURNOVERS
    # ============================================
    
    with op.batch_alter_table('turnovers') as batch_op:
        try:
            batch_op.drop_index('ix_turnovers_tenant_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_turnovers_pharmacy_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_turnovers_period_type')
        except:
            pass
        try:
            batch_op.drop_index('ix_turnovers_period_date')
        except:
            pass
        try:
            batch_op.drop_index('ix_turnovers_branch_id')
        except:
            pass
        
        # Revert comments
        batch_op.alter_column('period_type', comment=None)
        batch_op.alter_column('period_date', comment=None)
        batch_op.alter_column('items_sold', comment=None)
        batch_op.alter_column('sales_count', comment=None)
        batch_op.alter_column('discount_amount', comment=None)
        batch_op.alter_column('tax_amount', comment=None)
        batch_op.alter_column('net_turnover', comment=None)
        batch_op.alter_column('total_turnover', comment=None)
    
    # ============================================
    # 3. REVERT TENANTS
    # ============================================
    
    with op.batch_alter_table('tenants') as batch_op:
        try:
            batch_op.drop_index('ix_tenants_slug')
        except:
            pass
        try:
            batch_op.create_index('ix_tenants_slug', ['slug'], unique=False)
        except:
            pass
    
    # ============================================
    # 4. REVERT CAPITALS - Add back old columns
    # ============================================
    
    # Add back old columns
    op.add_column('capitals', sa.Column('capital_type', sa.VARCHAR(length=30), nullable=True))
    op.add_column('capitals', sa.Column('capital_date', sa.DATE(), nullable=True))
    op.add_column('capitals', sa.Column('destination', sa.VARCHAR(length=200), nullable=True))
    op.add_column('capitals', sa.Column('description', sa.VARCHAR(length=500), nullable=True))
    op.add_column('capitals', sa.Column('reference', sa.VARCHAR(length=100), nullable=True))
    op.add_column('capitals', sa.Column('approved_by', sa.UUID(), nullable=True))
    op.add_column('capitals', sa.Column('status', sa.VARCHAR(length=20), nullable=True))
    op.add_column('capitals', sa.Column('approved_at', postgresql.TIMESTAMP(), nullable=True))
    op.add_column('capitals', sa.Column('source', sa.VARCHAR(length=200), nullable=True))
    op.add_column('capitals', sa.Column('amount', sa.NUMERIC(precision=15, scale=2), nullable=True))
    
    # Drop new foreign keys
    try:
        op.drop_constraint('fk_capitals_branch', 'capitals', type_='foreignkey')
    except:
        pass
    
    try:
        op.drop_constraint('fk_capitals_pharmacy', 'capitals', type_='foreignkey')
    except:
        pass
    
    # Drop indexes
    with op.batch_alter_table('capitals') as batch_op:
        try:
            batch_op.drop_index('ix_capitals_tenant_pharmacy')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_tenant_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_tenant_branch')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_start_date')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_pharmacy_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_last_update')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_created_at')
        except:
            pass
        try:
            batch_op.drop_index('ix_capitals_branch_id')
        except:
            pass
    
    # Drop new columns
    new_columns = ['meta_data', 'notes', 'last_update_date', 'start_date', 'other_capital',
                   'equipment_capital', 'stock_capital', 'cash_capital', 'current_capital',
                   'initial_capital', 'branch_id', 'pharmacy_id']
    
    for col in new_columns:
        try:
            op.drop_column('capitals', col)
        except:
            pass
    
    # ============================================
    # 5. REVERT CAPITAL TRANSACTIONS
    # ============================================
    
    with op.batch_alter_table('capital_transactions') as batch_op:
        try:
            batch_op.drop_index('ix_capital_transactions_tenant_pharmacy')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_transactions_tenant_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_transactions_pharmacy_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_transactions_created_at')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_transactions_capital_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_transactions_branch_id')
        except:
            pass
        
        # Revert comments
        batch_op.alter_column('created_by', comment=None)
        batch_op.alter_column('transaction_date', comment=None)
        batch_op.alter_column('notes', comment=None)
        batch_op.alter_column('description', comment=None)
        batch_op.alter_column('reference_type', comment=None)
        batch_op.alter_column('reference_id', comment=None)
        batch_op.alter_column('new_capital', comment=None)
        batch_op.alter_column('previous_capital', comment=None)
        batch_op.alter_column('amount', comment=None)
        batch_op.alter_column('transaction_category', comment=None)
        batch_op.alter_column('transaction_type', comment=None)
    
    # ============================================
    # 6. REVERT CAPITAL ACCOUNTS
    # ============================================
    
    with op.batch_alter_table('capital_accounts') as batch_op:
        try:
            batch_op.drop_index('ix_capital_accounts_tenant_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_accounts_pharmacy_id')
        except:
            pass
        try:
            batch_op.drop_index('ix_capital_accounts_branch_id')
        except:
            pass
        
        # Revert comments
        batch_op.alter_column('period_month', comment=None)
        batch_op.alter_column('period_year', comment=None)
        batch_op.alter_column('credit', comment=None)
        batch_op.alter_column('debit', comment=None)
        batch_op.alter_column('balance', comment=None)
        batch_op.alter_column('account_type', comment=None)
        batch_op.alter_column('account_name', comment=None)
        batch_op.alter_column('account_code', comment=None)