# app/db/session.py
from __future__ import annotations

import logging
import os
from typing import Generator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _get_database_url() -> str:
    """
    Récupère DATABASE_URL depuis l'environnement.
    Supporte l'URL Render: postgresql://... (convertie en postgresql+psycopg2://...).
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL est manquante. Définis-la dans les variables d'environnement "
            "(Render > Service > Environment) ou dans un fichier .env en local."
        )

    # Render fournit souvent: postgresql://...
    # SQLAlchemy (sync) marche très bien avec: postgresql+psycopg2://...
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


DATABASE_URL: str = _get_database_url()

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
    echo=os.getenv("SQL_ECHO", "0") == "1",
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """
    Dépendance DB (1 session / requête)
    - commit auto si succès
    - rollback garanti
    """
    db: Session = SessionLocal()
    try:
        yield db
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("Erreur SQLAlchemy")
        raise HTTPException(status_code=500, detail="Erreur interne de base de données") from e
    except Exception:
        db.rollback()
        logger.exception("Erreur inattendue")
        raise
    finally:
        db.close()