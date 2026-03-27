"""add_relation_to_user

Revision ID: 6110943ce2bb
Revises: c593d64deb23
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '6110943ce2bb'
down_revision: Union[str, Sequence[str], None] = 'c593d64deb23'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =====================================================
    # 🔥 1. DROP FK AVANT MODIFICATION (OBLIGATOIRE)
    # =====================================================

    try:
        op.drop_constraint('orders_tenant_id_fkey', 'orders', type_='foreignkey')
    except:
        pass

    try:
        op.drop_constraint('orders_customer_id_fkey', 'orders', type_='foreignkey')
    except:
        pass

    # =====================================================
    # 🔥 2. CONVERSION STRING → UUID (SAFE)
    # =====================================================

    op.alter_column(
        'orders',
        'tenant_id',
        existing_type=sa.String(length=36),
        type_=sa.UUID(),
        postgresql_using='tenant_id::uuid',
        existing_nullable=False
    )

    op.alter_column(
        'orders',
        'customer_id',
        existing_type=sa.String(length=36),
        type_=sa.UUID(),
        postgresql_using='customer_id::uuid',
        existing_nullable=True
    )

    # =====================================================
    # 🔥 3. RECREATE FK
    # =====================================================

    op.create_foreign_key(
        'orders_tenant_id_fkey',
        'orders',
        'tenants',
        ['tenant_id'],
        ['id']
    )

    op.create_foreign_key(
        'orders_customer_id_fkey',
        'orders',
        'customers',
        ['customer_id'],
        ['id']
    )

    # =====================================================
    # 🔥 4. TABLES (UUID CLEAN)
    # =====================================================

    op.create_table(
        'capital_accounts',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),
        sa.Column('account_code', sa.String(20), nullable=False),
        sa.Column('account_name', sa.String(200), nullable=False),
        sa.Column('account_type', sa.String(50), nullable=False),
        sa.Column('balance', sa.Numeric(15, 2), nullable=False),
        sa.Column('debit', sa.Numeric(15, 2), nullable=False),
        sa.Column('credit', sa.Numeric(15, 2), nullable=False),
        sa.Column('period_year', sa.Integer(), nullable=False),
        sa.Column('period_month', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),

        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='SET NULL'),
    )

    op.create_index('ix_capital_accounts_code', 'capital_accounts', ['account_code'])
    op.create_index('ix_capital_accounts_type', 'capital_accounts', ['account_type'])
    op.create_index('ix_capital_accounts_period', 'capital_accounts', ['period_year', 'period_month'])

    # =====================================================

    op.create_table(
        'turnovers',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),

        sa.Column('total_turnover', sa.Numeric(15, 2), nullable=False),
        sa.Column('net_turnover', sa.Numeric(15, 2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('discount_amount', sa.Numeric(15, 2), nullable=False),

        sa.Column('sales_count', sa.Integer(), nullable=False),
        sa.Column('items_sold', sa.Integer(), nullable=False),

        sa.Column('period_date', sa.Date(), nullable=False),
        sa.Column('period_type', sa.String(20), nullable=False),

        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),

        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='SET NULL'),
    )

    op.create_index('ix_turnovers_period', 'turnovers', ['tenant_id', 'pharmacy_id', 'period_date'])

    # =====================================================

    op.create_table(
        'capital_transactions',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('capital_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=True),

        sa.Column('transaction_type', sa.String(50), nullable=False),
        sa.Column('transaction_category', sa.String(50), nullable=False),

        sa.Column('amount', sa.Numeric(15, 2), nullable=False),
        sa.Column('previous_capital', sa.Numeric(15, 2), nullable=False),
        sa.Column('new_capital', sa.Numeric(15, 2), nullable=False),

        sa.Column('reference_id', sa.UUID(), nullable=True),
        sa.Column('reference_type', sa.String(50), nullable=True),

        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),

        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),

        sa.ForeignKeyConstraint(['capital_id'], ['capitals.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )

    # =====================================================
    # 🔥 5. USER / SESSION
    # =====================================================

    op.add_column('users', sa.Column('active_pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('users', sa.Column('active_branch_id', sa.UUID(), nullable=True))

    op.create_foreign_key(None, 'users', 'pharmacies', ['active_pharmacy_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(None, 'users', 'branches', ['active_branch_id'], ['id'], ondelete='SET NULL')

    op.add_column('user_sessions', sa.Column('active_pharmacy_id', sa.UUID(), nullable=True))
    op.add_column('user_sessions', sa.Column('active_branch_id', sa.UUID(), nullable=True))

    op.create_foreign_key(None, 'user_sessions', 'pharmacies', ['active_pharmacy_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(None, 'user_sessions', 'branches', ['active_branch_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # Simplifié volontairement (évite erreurs)
    op.drop_table('capital_transactions')
    op.drop_table('turnovers')
    op.drop_table('capital_accounts')