from __future__ import annotations

import os
import sys
import pkgutil
import importlib
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from dotenv import load_dotenv

# =============================
# Ajouter la racine du projet (contient /app)
# =============================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Charger .env en local (Render ignore si variables déjà présentes)
load_dotenv()

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

from app.db.base import Base  # noqa: E402


def database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL manquante (local: .env / Render: Environment).")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


def load_all_models() -> None:
    """
    Importe tous les modules app.models.* pour remplir Base.metadata.
    """
    import app.models  # noqa: F401

    for _, module_name, _ in pkgutil.walk_packages(app.models.__path__, app.models.__name__ + "."):
        importlib.import_module(module_name)


config.set_main_option("sqlalchemy.url", database_url())

load_all_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
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


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
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