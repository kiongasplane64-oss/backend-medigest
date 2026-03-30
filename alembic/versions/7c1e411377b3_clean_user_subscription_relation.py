from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = '7c1e411377b3'
down_revision: Union[str, Sequence[str], None] = 'e73834b829ea'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 🔹 Ajouter user_id à subscriptions
    op.add_column('subscriptions',
        sa.Column('user_id', sa.UUID(), nullable=True)
    )

    # 🔹 Créer index
    op.create_index(
        'ix_subscriptions_user_id',
        'subscriptions',
        ['user_id']
    )

    # 🔹 Ajouter clé étrangère
    op.create_foreign_key(
        'fk_subscriptions_user_id',
        'subscriptions',
        'users',
        ['user_id'],
        ['id'],
        ondelete='CASCADE'  # 🔥 important
    )


def downgrade() -> None:
    # 🔹 Supprimer FK
    op.drop_constraint(
        'fk_subscriptions_user_id',
        'subscriptions',
        type_='foreignkey'
    )

    # 🔹 Supprimer index
    op.drop_index(
        'ix_subscriptions_user_id',
        table_name='subscriptions'
    )

    # 🔹 Supprimer colonne
    op.drop_column('subscriptions', 'user_id')