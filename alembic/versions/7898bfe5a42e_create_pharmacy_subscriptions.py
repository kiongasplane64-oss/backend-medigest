"""create_pharmacy_subscriptions

Revision ID: 7898bfe5a42e
Revises: cd027ced3934
Create Date: 2026-04-06 14:31:00.788479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '7898bfe5a42e'
down_revision: Union[str, Sequence[str], None] = 'cd027ced3934'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    # =========================================================
    # 1. Créer les ENUMs avec checkfirst=True
    # =========================================================
    conn = op.get_bind()
    
    # Vérifier et créer subscriptionplan
    result = conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'subscriptionplan')"))
    enum_exists = result.scalar()
    if not enum_exists:
        conn.execute(text("CREATE TYPE subscriptionplan AS ENUM ('TRIAL', 'STARTER', 'PROFESSIONAL', 'ENTERPRISE', 'INFINITE')"))
    
    # Vérifier et créer subscriptionstatus
    result = conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'subscriptionstatus')"))
    enum_exists = result.scalar()
    if not enum_exists:
        conn.execute(text("CREATE TYPE subscriptionstatus AS ENUM ('ACTIVE', 'EXPIRED', 'SUSPENDED', 'CANCELLED')"))
    
    # =========================================================
    # 2. Créer la table pharmacy_subscriptions
    # =========================================================
    op.create_table('pharmacy_subscriptions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('pharmacy_id', sa.UUID(), nullable=False),
        sa.Column('plan', 
                  sa.Enum('TRIAL', 'STARTER', 'PROFESSIONAL', 'ENTERPRISE', 'INFINITE', 
                          name='subscriptionplan', create_type=False), 
                  nullable=False),
        sa.Column('plan_name', sa.String(length=100), nullable=False),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('end_date', sa.DateTime(), nullable=False),
        sa.Column('trial_end_date', sa.DateTime(), nullable=True),
        sa.Column('status', 
                  sa.Enum('ACTIVE', 'EXPIRED', 'SUSPENDED', 'CANCELLED', 
                          name='subscriptionstatus', create_type=False), 
                  nullable=False),
        sa.Column('billing_cycle', sa.String(length=20), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=True, server_default='EUR'),
        sa.Column('auto_renew', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('max_products', sa.Integer(), nullable=False),
        sa.Column('max_users', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.UUID(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(['pharmacy_id'], ['pharmacies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index(op.f('ix_pharmacy_subscriptions_pharmacy_id'), 'pharmacy_subscriptions', ['pharmacy_id'], unique=True)
    
    # =========================================================
    # 3. Ajouter la colonne subscription_id à pharmacies
    # =========================================================
    op.add_column('pharmacies', sa.Column('subscription_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_pharmacies_subscription_id', 
        'pharmacies', 'pharmacy_subscriptions', 
        ['subscription_id'], ['id'], 
        ondelete='SET NULL'
    )
    
    # =========================================================
    # 4. Créer des abonnements par défaut pour les pharmacies existantes
    # =========================================================
    conn.execute(text("""
        INSERT INTO pharmacy_subscriptions (
            id, pharmacy_id, plan, plan_name, start_date, end_date, 
            trial_end_date, status, billing_cycle, price, currency, 
            auto_renew, max_products, max_users, created_at, updated_at
        )
        SELECT 
            gen_random_uuid(),
            p.id,
            'TRIAL',
            'Essai',
            COALESCE(p.created_at, NOW()),
            COALESCE(p.created_at, NOW()) + INTERVAL '14 days',
            COALESCE(p.created_at, NOW()) + INTERVAL '14 days',
            'ACTIVE',
            'monthly',
            0,
            'EUR',
            true,
            2000,
            5,
            NOW(),
            NOW()
        FROM pharmacies p
        LEFT JOIN pharmacy_subscriptions ps ON ps.pharmacy_id = p.id
        WHERE ps.id IS NULL
    """))
    
    # =========================================================
    # 5. Mettre à jour la colonne subscription_id dans pharmacies
    # =========================================================
    conn.execute(text("""
        UPDATE pharmacies p
        SET subscription_id = ps.id
        FROM pharmacy_subscriptions ps
        WHERE ps.pharmacy_id = p.id
        AND p.subscription_id IS NULL
    """))
    
    # =========================================================
    # 6. Rendre subscription_id NOT NULL après migration
    # =========================================================
    op.alter_column('pharmacies', 'subscription_id', nullable=False)
    
    # =========================================================
    # 7. Corriger l'index tenants_slug
    # =========================================================
    try:
        op.drop_index('ix_tenants_slug', table_name='tenants')
    except:
        pass
    
    op.create_index(op.f('ix_tenants_slug'), 'tenants', ['slug'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    
    # Supprimer la foreign key
    try:
        op.drop_constraint('fk_pharmacies_subscription_id', 'pharmacies', type_='foreignkey')
    except:
        pass
    
    # Supprimer la colonne subscription_id
    op.drop_column('pharmacies', 'subscription_id')
    
    # Supprimer la table
    op.drop_index(op.f('ix_pharmacy_subscriptions_pharmacy_id'), table_name='pharmacy_subscriptions')
    op.drop_table('pharmacy_subscriptions')
    
    # Note: Les ENUMs ne sont pas supprimés pour éviter les erreurs