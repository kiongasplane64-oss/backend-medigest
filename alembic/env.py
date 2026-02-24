from sqlalchemy import engine_from_config, pool
from alembic import context
import sys
import os
from dotenv import load_dotenv 
from logging.config import fileConfig
# =============================
# Ajouter le chemin du projet
# =============================
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- CHARGEMENT DU .ENV ---
# Cela chargera les variables du fichier .env dans os.environ
load_dotenv() 

# =============================
# Importer Base et modèles
# =============================
from app.db.base import Base  # noqa: E402
from app import models  # noqa: F401, E402

# =============================
# Config Alembic
# =============================
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    # Maintenant os.getenv ira chercher dans votre fichier .env local
    url = os.getenv("DATABASE_URL")
    
    if not url:
        raise RuntimeError(
            "DATABASE_URL manquante pour Alembic. "
            "Vérifiez votre fichier .env en local ou l'Environment sur Render."
        )
    
    # Correction pour SQLAlchemy 2.0+ (PostgreSQL)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        
    return url


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = Base.metadata


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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()