"""
Synchronous SQLAlchemy session for use in Celery tasks.

Celery workers run in a prefork process pool — asyncpg's event loop is not
compatible across fork boundaries. We use psycopg2 (sync) here, exactly as
Alembic does for migrations.
"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.settings import settings

_engine = None
_SessionLocal = None


def _sync_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def _get_factory():
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = create_engine(_sync_url(), pool_pre_ping=True, pool_size=5, max_overflow=10)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal


@contextmanager
def get_sync_session() -> Session:
    """Context manager that yields a sync session, commits on success, rolls back on error."""
    factory = _get_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
