import os
import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings

logger = logging.getLogger(__name__)

db_url = settings.SYNC_DATABASE_URL
is_sqlite = db_url.startswith("sqlite")

# Synchronous SQLAlchemy 2.x engine
engine = create_engine(
    db_url,
    echo=False,
    future=True,
    **({"connect_args": {"check_same_thread": False}} if is_sqlite else {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 5}
    })
)

# SessionLocal sessionmaker
SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_sync_db() -> Session:
    """Helper for obtaining a database session (e.g. in Celery tasks)."""
    return SessionLocal()
