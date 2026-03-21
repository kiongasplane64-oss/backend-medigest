"""add_role_column_to_users

Revision ID: 954814e9b3c3
Revises: f52b042111b7
Create Date: 2026-03-21 10:36:00.372584

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '954814e9b3c3'
down_revision: Union[str, Sequence[str], None] = 'f52b042111b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Vérifier si la colonne role existe déjà
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('users')]
    
    # Ajouter la colonne role seulement si elle n'existe pas
    if 'role' not in columns:
        op.add_column('users', sa.Column('role', sa.String(length=50), nullable=False, server_default='user'))
        op.create_index('ix_users_role', 'users', ['role'])
        print("✅ Colonne 'role' ajoutée avec succès")
    else:
        print("ℹ️ La colonne 'role' existe déjà, aucune action nécessaire")
    
    # Mettre à jour le rôle pour l'utilisateur super admin
    op.execute(
        "UPDATE users SET role = 'super_admin' WHERE email = 'kiongasplane64@gmail.com' AND (role IS NULL OR role = 'user')"
    )


def downgrade() -> None:
    # Vérifier si la colonne existe avant de la supprimer
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('users')]
    
    if 'role' in columns:
        op.drop_index('ix_users_role', table_name='users')
        op.drop_column('users', 'role')
        print("✅ Colonne 'role' supprimée")
    else:
        print("ℹ️ La colonne 'role' n'existe pas, aucune action nécessaire")