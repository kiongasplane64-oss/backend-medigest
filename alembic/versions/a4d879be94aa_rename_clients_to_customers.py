"""rename_clients_to_customers

Revision ID: a4d879be94aa
Revises: e831d13c8e3e
Create Date: 2026-04-16 11:40:21.619860

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4d879be94aa'
down_revision: Union[str, Sequence[str], None] = 'e831d13c8e3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    # =============================================
    # 1. D'abord, supprimer les contraintes de clés étrangères qui pointent vers clients
    # =============================================
    
    # Supprimer les contraintes sur sales
    op.drop_constraint('sales_client_id_fkey', 'sales', type_='foreignkey')
    
    # Supprimer les contraintes sur debts
    op.drop_constraint('debts_client_id_fkey', 'debts', type_='foreignkey')
    
    # Supprimer les contraintes sur projects
    op.drop_constraint('projects_client_id_fkey', 'projects', type_='foreignkey')
    
    # Supprimer les contraintes sur debt_payments
    op.drop_constraint('debt_payments_client_id_fkey', 'debt_payments', type_='foreignkey')
    
    # =============================================
    # 2. Supprimer les index de la table clients
    # =============================================
    op.drop_index('ix_clients_credit_status', table_name='clients')
    op.drop_index('ix_clients_last_purchase', table_name='clients')
    op.drop_index('ix_clients_tenant_email', table_name='clients')
    op.drop_index('ix_clients_tenant_nom', table_name='clients')
    op.drop_index('ix_clients_tenant_phone', table_name='clients')
    op.drop_index('ix_clients_type', table_name='clients')
    
    # =============================================
    # 3. Supprimer la table clients (maintenant sans dépendances)
    # =============================================
    op.drop_table('clients')
    
    # =============================================
    # 4. Supprimer la table invoices_backup si elle existe
    # =============================================
    op.drop_table('invoices_backup')
    
    # =============================================
    # 5. Ajouter les nouvelles colonnes à customers
    # =============================================
    op.add_column('customers', sa.Column('nom', sa.String(length=100), nullable=False))
    op.add_column('customers', sa.Column('prenom', sa.String(length=100), nullable=True))
    op.add_column('customers', sa.Column('telephone', sa.String(length=20), nullable=False))
    op.add_column('customers', sa.Column('adresse', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('ville', sa.String(length=100), nullable=True))
    op.add_column('customers', sa.Column('code_postal', sa.String(length=20), nullable=True))
    op.add_column('customers', sa.Column('pays', sa.String(length=100), nullable=True))
    op.add_column('customers', sa.Column('type_client', sa.String(length=20), nullable=True, comment='particulier, professionnel, assureur, etat, hopital, clinique'))
    op.add_column('customers', sa.Column('category', sa.String(length=20), nullable=True, comment='standard, premium, vip'))
    op.add_column('customers', sa.Column('entreprise', sa.String(length=100), nullable=True))
    op.add_column('customers', sa.Column('num_contribuable', sa.String(length=50), nullable=True))
    op.add_column('customers', sa.Column('rccm', sa.String(length=50), nullable=True))
    op.add_column('customers', sa.Column('id_nat', sa.String(length=50), nullable=True))
    op.add_column('customers', sa.Column('credit_limit', sa.DECIMAL(precision=15, scale=2), nullable=True))
    op.add_column('customers', sa.Column('eligible_credit', sa.Boolean(), nullable=True))
    op.add_column('customers', sa.Column('dette_actuelle', sa.DECIMAL(precision=15, scale=2), nullable=True))
    op.add_column('customers', sa.Column('credit_score', sa.Integer(), nullable=True))
    op.add_column('customers', sa.Column('total_achats', sa.DECIMAL(precision=15, scale=2), nullable=True))
    op.add_column('customers', sa.Column('nombre_achats', sa.Integer(), nullable=True))
    op.add_column('customers', sa.Column('moyenne_achat', sa.DECIMAL(precision=15, scale=2), nullable=True))
    op.add_column('customers', sa.Column('date_inscription', sa.DateTime(), nullable=True))
    op.add_column('customers', sa.Column('dernier_achat', sa.DateTime(), nullable=True))
    op.add_column('customers', sa.Column('date_dernier_paiement', sa.DateTime(), nullable=True))
    op.add_column('customers', sa.Column('last_visit', sa.DateTime(), nullable=True))
    op.add_column('customers', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('preferences', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('customers', sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True))
    op.add_column('customers', sa.Column('blacklisted', sa.Boolean(), nullable=True))
    op.add_column('customers', sa.Column('blacklist_reason', sa.Text(), nullable=True))
    
    # =============================================
    # 6. Modifier les colonnes existantes
    # =============================================
    op.alter_column('customers', 'pharmacy_id',
               existing_type=sa.UUID(),
               nullable=True)
    
    # Supprimer l'ancienne contrainte unique sur email
    op.drop_constraint('customers_email_key', 'customers', type_='unique')
    
    # =============================================
    # 7. Créer les nouveaux index
    # =============================================
    op.create_index('ix_customers_category', 'customers', ['tenant_id', 'category'], unique=False)
    op.create_index('ix_customers_credit_status', 'customers', ['tenant_id', 'eligible_credit', 'blacklisted'], unique=False)
    op.create_index('ix_customers_insurance', 'customers', ['tenant_id', 'insurance_provider'], unique=False)
    op.create_index('ix_customers_last_purchase', 'customers', ['tenant_id', 'dernier_achat'], unique=False)
    op.create_index('ix_customers_tenant_email', 'customers', ['tenant_id', 'email'], unique=False)
    op.create_index('ix_customers_tenant_nom', 'customers', ['tenant_id', 'nom'], unique=False)
    op.create_index('ix_customers_tenant_phone', 'customers', ['tenant_id', 'telephone'], unique=False)
    op.create_index('ix_customers_type', 'customers', ['tenant_id', 'type_client'], unique=False)
    
    # =============================================
    # 8. Supprimer les anciennes colonnes de customers
    # =============================================
    op.drop_column('customers', 'is_vip')
    op.drop_column('customers', 'address')
    op.drop_column('customers', 'phone')
    op.drop_column('customers', 'first_name')
    op.drop_column('customers', 'city')
    op.drop_column('customers', 'last_name')
    
    # =============================================
    # 9. Mettre à jour la table debt_payments
    # =============================================
    op.add_column('debt_payments', sa.Column('customer_id', sa.UUID(), nullable=True))
    
    # Migrer les données si nécessaire (optionnel)
    # op.execute("UPDATE debt_payments SET customer_id = client_id WHERE client_id IS NOT NULL")
    
    op.drop_index('ix_debt_payments_client', table_name='debt_payments')
    op.create_index('ix_debt_payments_customer', 'debt_payments', ['tenant_id', 'customer_id'], unique=False)
    op.drop_column('debt_payments', 'client_id')
    
    # Rendre customer_id NOT NULL après migration
    op.alter_column('debt_payments', 'customer_id', nullable=False)
    
    # Recréer la foreign key
    op.create_foreign_key(None, 'debt_payments', 'customers', ['customer_id'], ['id'])
    
    # =============================================
    # 10. Mettre à jour la table debts
    # =============================================
    op.add_column('debts', sa.Column('customer_id', sa.UUID(), nullable=True))
    
    # Migrer les données
    # op.execute("UPDATE debts SET customer_id = client_id WHERE client_id IS NOT NULL")
    
    op.drop_index('ix_debts_tenant_client', table_name='debts')
    op.create_index('ix_debts_tenant_customer', 'debts', ['tenant_id', 'customer_id'], unique=False)
    op.drop_column('debts', 'client_id')
    
    # Rendre customer_id NOT NULL
    op.alter_column('debts', 'customer_id', nullable=False)
    
    # Recréer la foreign key
    op.create_foreign_key(None, 'debts', 'customers', ['customer_id'], ['id'])
    
    # =============================================
    # 11. Mettre à jour la table projects
    # =============================================
    op.add_column('projects', sa.Column('customer_id', sa.UUID(), nullable=True))
    
    # Migrer les données
    # op.execute("UPDATE projects SET customer_id = client_id WHERE client_id IS NOT NULL")
    
    op.drop_index('ix_projects_tenant_client', table_name='projects')
    op.create_index('ix_projects_tenant_customer', 'projects', ['tenant_id', 'customer_id'], unique=False)
    op.drop_column('projects', 'client_id')
    
    # Recréer la foreign key
    op.create_foreign_key(None, 'projects', 'customers', ['customer_id'], ['id'])
    
    # =============================================
    # 12. Mettre à jour la table sales
    # =============================================
    # Ajouter les nouvelles colonnes
    op.add_column('sales', sa.Column('customer_name', sa.String(length=100), nullable=True))
    op.add_column('sales', sa.Column('customer_phone', sa.String(length=20), nullable=True))
    
    # Migrer les données depuis les anciennes colonnes
    op.execute("UPDATE sales SET customer_name = client_name WHERE client_name IS NOT NULL")
    op.execute("UPDATE sales SET customer_phone = client_phone WHERE client_phone IS NOT NULL")
    op.execute("UPDATE sales SET customer_id = client_id WHERE client_id IS NOT NULL")
    
    # Rendre customer_name NOT NULL après migration
    op.alter_column('sales', 'customer_name', nullable=False)
    
    # Supprimer les anciennes colonnes et index
    op.drop_index('ix_sales_tenant_client', table_name='sales')
    op.drop_column('sales', 'client_phone')
    op.drop_column('sales', 'client_name')
    op.drop_column('sales', 'client_id')
    
    # Créer le nouvel index
    op.create_index('ix_sales_tenant_customer', 'sales', ['tenant_id', 'customer_id'], unique=False)
    
    # Recréer la foreign key
    op.create_foreign_key(None, 'sales', 'customers', ['customer_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    
    # =============================================
    # DOWNDGRADE - Revenir à l'ancienne structure
    # =============================================
    
    # 1. Revenir sur sales
    op.drop_constraint(None, 'sales', type_='foreignkey')
    op.drop_index('ix_sales_tenant_customer', table_name='sales')
    op.add_column('sales', sa.Column('client_id', sa.UUID(), nullable=True))
    op.add_column('sales', sa.Column('client_name', sa.VARCHAR(length=100), nullable=False, server_default="Client Générique"))
    op.add_column('sales', sa.Column('client_phone', sa.VARCHAR(length=20), nullable=True))
    
    # Migrer les données
    op.execute("UPDATE sales SET client_id = customer_id WHERE customer_id IS NOT NULL")
    op.execute("UPDATE sales SET client_name = customer_name WHERE customer_name IS NOT NULL")
    op.execute("UPDATE sales SET client_phone = customer_phone WHERE customer_phone IS NOT NULL")
    
    op.create_index('ix_sales_tenant_client', 'sales', ['tenant_id', 'client_id'], unique=False)
    op.create_foreign_key('sales_client_id_fkey', 'sales', 'clients', ['client_id'], ['id'])
    op.drop_column('sales', 'customer_phone')
    op.drop_column('sales', 'customer_name')
    
    # 2. Revenir sur projects
    op.drop_constraint(None, 'projects', type_='foreignkey')
    op.drop_index('ix_projects_tenant_customer', table_name='projects')
    op.add_column('projects', sa.Column('client_id', sa.UUID(), nullable=True))
    op.execute("UPDATE projects SET client_id = customer_id WHERE customer_id IS NOT NULL")
    op.create_index('ix_projects_tenant_client', 'projects', ['tenant_id', 'client_id'], unique=False)
    op.create_foreign_key('projects_client_id_fkey', 'projects', 'clients', ['client_id'], ['id'])
    op.drop_column('projects', 'customer_id')
    
    # 3. Revenir sur debts
    op.drop_constraint(None, 'debts', type_='foreignkey')
    op.drop_index('ix_debts_tenant_customer', table_name='debts')
    op.add_column('debts', sa.Column('client_id', sa.UUID(), nullable=False))
    op.execute("UPDATE debts SET client_id = customer_id WHERE customer_id IS NOT NULL")
    op.create_index('ix_debts_tenant_client', 'debts', ['tenant_id', 'client_id'], unique=False)
    op.create_foreign_key('debts_client_id_fkey', 'debts', 'clients', ['client_id'], ['id'])
    op.drop_column('debts', 'customer_id')
    
    # 4. Revenir sur debt_payments
    op.drop_constraint(None, 'debt_payments', type_='foreignkey')
    op.drop_index('ix_debt_payments_customer', table_name='debt_payments')
    op.add_column('debt_payments', sa.Column('client_id', sa.UUID(), nullable=False))
    op.execute("UPDATE debt_payments SET client_id = customer_id WHERE customer_id IS NOT NULL")
    op.create_index('ix_debt_payments_client', 'debt_payments', ['tenant_id', 'client_id'], unique=False)
    op.create_foreign_key('debt_payments_client_id_fkey', 'debt_payments', 'clients', ['client_id'], ['id'])
    op.drop_column('debt_payments', 'customer_id')
    
    # 5. Recréer la table clients
    op.create_table('clients',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('nom', sa.VARCHAR(length=100), nullable=False),
        sa.Column('telephone', sa.VARCHAR(length=20), nullable=True),
        sa.Column('email', sa.VARCHAR(length=100), nullable=True),
        sa.Column('adresse', sa.VARCHAR(length=200), nullable=True),
        sa.Column('type_client', sa.VARCHAR(length=20), nullable=True),
        sa.Column('entreprise', sa.VARCHAR(length=100), nullable=True),
        sa.Column('num_contribuable', sa.VARCHAR(length=50), nullable=True),
        sa.Column('rccm', sa.VARCHAR(length=50), nullable=True),
        sa.Column('id_nat', sa.VARCHAR(length=50), nullable=True),
        sa.Column('credit_limit', sa.NUMERIC(precision=15, scale=2), nullable=True),
        sa.Column('eligible_credit', sa.BOOLEAN(), nullable=True),
        sa.Column('dette_actuelle', sa.NUMERIC(precision=15, scale=2), nullable=True),
        sa.Column('credit_score', sa.INTEGER(), nullable=True),
        sa.Column('total_achats', sa.NUMERIC(precision=15, scale=2), nullable=True),
        sa.Column('nombre_achats', sa.INTEGER(), nullable=True),
        sa.Column('moyenne_achat', sa.NUMERIC(precision=15, scale=2), nullable=True),
        sa.Column('date_inscription', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('dernier_achat', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('date_dernier_paiement', postgresql.TIMESTAMP(), nullable=True),
        sa.Column('notes', sa.TEXT(), nullable=True),
        sa.Column('preferences', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('is_active', sa.BOOLEAN(), nullable=True),
        sa.Column('blacklisted', sa.BOOLEAN(), nullable=True),
        sa.Column('blacklist_reason', sa.TEXT(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='clients_tenant_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='clients_pkey')
    )
    
    # 6. Recréer les index de clients
    op.create_index('ix_clients_credit_status', 'clients', ['tenant_id', 'eligible_credit', 'blacklisted'], unique=False)
    op.create_index('ix_clients_last_purchase', 'clients', ['tenant_id', 'dernier_achat'], unique=False)
    op.create_index('ix_clients_tenant_email', 'clients', ['tenant_id', 'email'], unique=False)
    op.create_index('ix_clients_tenant_nom', 'clients', ['tenant_id', 'nom'], unique=False)
    op.create_index('ix_clients_tenant_phone', 'clients', ['tenant_id', 'telephone'], unique=False)
    op.create_index('ix_clients_type', 'clients', ['tenant_id', 'type_client'], unique=False)
    
    # 7. Restaurer les anciennes colonnes de customers
    op.add_column('customers', sa.Column('last_name', sa.VARCHAR(length=100), nullable=False, server_default=''))
    op.add_column('customers', sa.Column('city', sa.VARCHAR(length=100), nullable=True))
    op.add_column('customers', sa.Column('first_name', sa.VARCHAR(length=100), nullable=False, server_default=''))
    op.add_column('customers', sa.Column('phone', sa.VARCHAR(length=50), nullable=True))
    op.add_column('customers', sa.Column('address', sa.TEXT(), nullable=True))
    op.add_column('customers', sa.Column('is_vip', sa.BOOLEAN(), nullable=True))
    
    # 8. Supprimer les index et colonnes ajoutées
    op.drop_index('ix_customers_type', table_name='customers')
    op.drop_index('ix_customers_tenant_phone', table_name='customers')
    op.drop_index('ix_customers_tenant_nom', table_name='customers')
    op.drop_index('ix_customers_tenant_email', table_name='customers')
    op.drop_index('ix_customers_last_purchase', table_name='customers')
    op.drop_index('ix_customers_insurance', table_name='customers')
    op.drop_index('ix_customers_credit_status', table_name='customers')
    op.drop_index('ix_customers_category', table_name='customers')
    
    # 9. Supprimer les colonnes ajoutées à customers
    op.drop_column('customers', 'blacklist_reason')
    op.drop_column('customers', 'blacklisted')
    op.drop_column('customers', 'metadata')
    op.drop_column('customers', 'preferences')
    op.drop_column('customers', 'notes')
    op.drop_column('customers', 'last_visit')
    op.drop_column('customers', 'date_dernier_paiement')
    op.drop_column('customers', 'dernier_achat')
    op.drop_column('customers', 'date_inscription')
    op.drop_column('customers', 'moyenne_achat')
    op.drop_column('customers', 'nombre_achats')
    op.drop_column('customers', 'total_achats')
    op.drop_column('customers', 'credit_score')
    op.drop_column('customers', 'dette_actuelle')
    op.drop_column('customers', 'eligible_credit')
    op.drop_column('customers', 'credit_limit')
    op.drop_column('customers', 'id_nat')
    op.drop_column('customers', 'rccm')
    op.drop_column('customers', 'num_contribuable')
    op.drop_column('customers', 'entreprise')
    op.drop_column('customers', 'category')
    op.drop_column('customers', 'type_client')
    op.drop_column('customers', 'pays')
    op.drop_column('customers', 'code_postal')
    op.drop_column('customers', 'ville')
    op.drop_column('customers', 'adresse')
    op.drop_column('customers', 'telephone')
    op.drop_column('customers', 'prenom')
    op.drop_column('customers', 'nom')
    
    # 10. Recréer la contrainte unique sur email
    op.create_unique_constraint('customers_email_key', 'customers', ['email'])