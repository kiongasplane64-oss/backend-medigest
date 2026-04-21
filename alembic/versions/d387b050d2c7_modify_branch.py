"""modify_branch

Revision ID: d387b050d2c7
Revises: b67db3eab64f
Create Date: 2026-04-21 07:22:53.954760

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd387b050d2c7'
down_revision: Union[str, Sequence[str], None] = 'b67db3eab64f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Unique migration for modify_branch branch."""
    
    # 1. Modifications sur branch_subscriptions
    with op.batch_alter_table('branch_subscriptions') as batch_op:
        # Suppression des contraintes et index
        batch_op.drop_constraint('branch_subscriptions_branch_id_key', type_='unique')
        batch_op.drop_index('idx_branch_subscriptions_branch_id')
        batch_op.drop_index('idx_branch_subscriptions_end_date')
        batch_op.drop_index('idx_branch_subscriptions_pharmacy_id')
        batch_op.drop_index('idx_branch_subscriptions_status')
        batch_op.drop_index('idx_branch_subscriptions_tenant_id')
        batch_op.drop_constraint('fk_branch_subscriptions_tenant', type_='foreignkey')
        batch_op.drop_constraint('fk_branch_subscriptions_pharmacy', type_='foreignkey')
        
        # Suppression des valeurs par défaut des colonnes
        batch_op.alter_column('id', server_default=None)
        batch_op.alter_column('plan', server_default=None)
        batch_op.alter_column('start_date', server_default=None)
        batch_op.alter_column('status', server_default=None)
        batch_op.alter_column('billing_cycle', server_default=None)
        batch_op.alter_column('price', server_default=None)
        batch_op.alter_column('currency', server_default=None)
        batch_op.alter_column('auto_renew', server_default=None)
        batch_op.alter_column('max_products', server_default=None)
        batch_op.alter_column('max_users', server_default=None)
        batch_op.alter_column('max_storage_mb', server_default=None)
        batch_op.alter_column('created_at', server_default=None)
        batch_op.alter_column('updated_at', server_default=None)
        
        # Création des nouveaux index
        batch_op.create_index('ix_branch_subscriptions_branch_id', ['branch_id'], unique=True)
        batch_op.create_index('ix_branch_subscriptions_pharmacy_id', ['pharmacy_id'], unique=False)
        batch_op.create_index('ix_branch_subscriptions_tenant_id', ['tenant_id'], unique=False)
        
        # Recréation des foreign keys
        batch_op.create_foreign_key(None, 'tenants', ['tenant_id'], ['id'])
        batch_op.create_foreign_key(None, 'pharmacies', ['pharmacy_id'], ['id'])
    
    # 2. Modifications sur branches
    with op.batch_alter_table('branches') as batch_op:
        batch_op.add_column(sa.Column('subscription_config', postgresql.JSON(astext_type=sa.Text()), nullable=True))
        batch_op.add_column(sa.Column('operational_config', postgresql.JSON(astext_type=sa.Text()), nullable=True))
        batch_op.add_column(sa.Column('subscription_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('subscription_id', sa.UUID(), nullable=True))
        batch_op.create_foreign_key(None, 'branch_subscriptions', ['subscription_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Restaurer branches
    with op.batch_alter_table('branches') as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_column('subscription_id')
        batch_op.drop_column('subscription_status')
        batch_op.drop_column('operational_config')
        batch_op.drop_column('subscription_config')
    
    # 2. Restaurer branch_subscriptions
    with op.batch_alter_table('branch_subscriptions') as batch_op:
        # Supprimer les nouveaux index
        batch_op.drop_index('ix_branch_subscriptions_tenant_id')
        batch_op.drop_index('ix_branch_subscriptions_pharmacy_id')
        batch_op.drop_index('ix_branch_subscriptions_branch_id')
        
        # Supprimer les nouvelles foreign keys
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_constraint(None, type_='foreignkey')
        
        # Restaurer les anciennes foreign keys
        batch_op.create_foreign_key('fk_branch_subscriptions_pharmacy', 'pharmacies', ['pharmacy_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_branch_subscriptions_tenant', 'tenants', ['tenant_id'], ['id'], ondelete='CASCADE')
        
        # Restaurer les anciens index
        batch_op.create_index('idx_branch_subscriptions_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_index('idx_branch_subscriptions_status', ['status'], unique=False)
        batch_op.create_index('idx_branch_subscriptions_pharmacy_id', ['pharmacy_id'], unique=False)
        batch_op.create_index('idx_branch_subscriptions_end_date', ['end_date'], unique=False)
        batch_op.create_index('idx_branch_subscriptions_branch_id', ['branch_id'], unique=False)
        
        # Restaurer la contrainte unique
        batch_op.create_unique_constraint('branch_subscriptions_branch_id_key', ['branch_id'])
        
        # Restaurer les valeurs par défaut
        batch_op.alter_column('updated_at', server_default=sa.text('now()'))
        batch_op.alter_column('created_at', server_default=sa.text('now()'))
        batch_op.alter_column('max_storage_mb', server_default=sa.text('100'))
        batch_op.alter_column('max_users', server_default=sa.text('5'))
        batch_op.alter_column('max_products', server_default=sa.text('100'))
        batch_op.alter_column('auto_renew', server_default=sa.text('true'))
        batch_op.alter_column('currency', server_default=sa.text("'EUR'"))
        batch_op.alter_column('price', server_default=sa.text('0.0'))
        batch_op.alter_column('billing_cycle', server_default=sa.text("'monthly'"))
        batch_op.alter_column('status', server_default=sa.text("'trial'"))
        batch_op.alter_column('start_date', server_default=sa.text('now()'))
        batch_op.alter_column('plan', server_default=sa.text("'trial'"))
        batch_op.alter_column('id', server_default=sa.text('gen_random_uuid()'))