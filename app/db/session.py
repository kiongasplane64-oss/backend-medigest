#/app/db/session.py
from typing import Generator
import logging

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/pharma_saas"

logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

def get_db() -> Generator[Session, None, None]:
    """
    Dépendance DB SaaS professionnelle
    - 1 session / requête
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
        raise HTTPException(
            status_code=500,
            detail="Erreur interne de base de données"
        ) from e
    except Exception:
        db.rollback()
        logger.exception("Erreur inattendue")
        raise
    finally:
        db.close()
