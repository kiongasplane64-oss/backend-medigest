# alembic/env.py
import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# =============================
# Ajouter le chemin du projet
# =============================
# Ceci permet à Alembic de trouver ton module "app"
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# =============================
# Importer Base et modèles
# =============================
from app.db.base import Base
from app.models import *  # Tous les modèles
from app.core.config import settings  # settings avec DATABASE_URL

# =============================
# Config Alembic
# =============================
config = context.config

# Configurer logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Forcer Alembic à utiliser l'URL de la base de données depuis settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Metadata cible pour autogenerate
target_metadata = Base.metadata

# =============================
# Migrations offline
# =============================
def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# =============================
# Migrations online
# =============================
def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# =============================
# Exécution
# =============================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
