"""add_system_tenant

Revision ID: 46fa97b4e921
Revises: d22c5349c3b8
Create Date: 2026-03-21 07:04:50.078383

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '46fa97b4e921'
down_revision: Union[str, Sequence[str], None] = 'd22c5349c3b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    system_tenant_id = '00000000-0000-0000-0000-000000000001'
    
    # 1. Vérifier si le tenant existe déjà
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT id FROM tenants WHERE id = CAST(:tid AS UUID)"),
        {"tid": system_tenant_id}
    ).fetchone()
    
    if not result:
        # 2. Insérer le tenant système
        op.execute(sa.text("""
            INSERT INTO tenants (
                id, tenant_code, nom_pharmacie, email_admin, telephone_principal,
                ville, pays, nom_proprietaire, type_pharmacie, nombre_employes,
                status, auto_renew, monthly_rate, config, meta_data, tags,
                max_users, max_products, max_pharmacies, created_at, updated_at
            ) VALUES (
                CAST(:tid AS UUID), 'SYSTEM', 'System Tenant', 'system@medigestpro.net',
                '+243000000000', 'Kinshasa', 'RDC', 'System Owner', 'officinale',
                1, 'active', false, 0.00, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb,
                999, 99999, 999, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """).bindparams(tid=system_tenant_id))
        print(f"System tenant created successfully with ID: {system_tenant_id}")
    else:
        print("System tenant already exists, checking audit logs...")

    # 3. Créer un log d'initialisation (Correction de l'ID manquant)
    # On utilise gen_random_uuid() pour la colonne 'id' de audit_logs
    op.execute(sa.text("""
        INSERT INTO audit_logs (
            id,
            tenant_id, user_id, action, action_type, action_category,
            action_level, severity, entity_type, description, ip_address,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(),
            CAST(:tid AS UUID), NULL, 'SYSTEM_INITIALIZATION', 'SYSTEM', 'system',
            'INFO', 'info', 'system', 'System tenant created for global audit logs',
            '127.0.0.1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
    """).bindparams(tid=system_tenant_id))
    print("System initialization audit log created.")


def downgrade():
    system_tenant_id = '00000000-0000-0000-0000-000000000001'
    
    # Nettoyage des logs (optionnel mais propre)
    op.execute(sa.text("""
        DELETE FROM audit_logs WHERE tenant_id = CAST(:tid AS UUID)
    """).bindparams(tid=system_tenant_id))

    # Désactivation du tenant
    op.execute(sa.text("""
        UPDATE tenants 
        SET status = 'inactive',
            nom_pharmacie = 'DELETED_SYSTEM_TENANT',
            email_admin = 'deleted_system@medigestpro.net'
        WHERE id = CAST(:tid AS UUID)
    """).bindparams(tid=system_tenant_id))