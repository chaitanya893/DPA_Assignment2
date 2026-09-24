"""Database connection and session factory supporting PostgreSQL and SQLite.

Supports:
- PostgreSQL via DATABASE_URL environment variable (e.g. postgresql://user:pass@localhost:5432/dbname).
- Default zero-configuration embedded SQLite at data/fund_distributions.db.
- Automatic table creation and connection pooling.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.database.models import Base

logger = logging.getLogger(__name__)

DEFAULT_SQLITE_PATH = (
    Path(__file__).parent.parent.parent / "data" / "fund_distributions.db"
)


def get_database_url() -> str:
    """Retrieve database URL from environment or fallback to default SQLite path."""
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return db_url

    # Ensure data directory exists for default SQLite
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


def get_engine(db_url: str | None = None, echo: bool = False) -> Engine:
    """Create and return a configured SQLAlchemy Engine."""
    url = db_url or get_database_url()

    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        engine = create_engine(url, echo=echo, connect_args=connect_args)

        # SQLite enforces foreign keys per connection, so switch them on for every
        # connection the pool opens (not only the first one).
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    else:
        # PostgreSQL connection pooling
        engine = create_engine(
            url, echo=echo, pool_pre_ping=True, pool_size=10, max_overflow=20
        )

    return engine


def init_db(engine: Engine | None = None) -> None:
    """Initialize database schema, creating all 9 tables if they do not already exist."""
    eng = engine or get_engine()
    Base.metadata.create_all(bind=eng)
    logger.info("Database schema initialized successfully on %s", eng.url)


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a sessionmaker factory bound to the engine."""
    eng = engine or get_engine()
    return sessionmaker(bind=eng, autoflush=False, autocommit=False)


def get_session(engine: Engine | None = None) -> Session:
    """Create and return a new database session."""
    factory = get_session_factory(engine)
    return factory()


@contextmanager
def session_scope(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Context manager for atomic transactional session scope."""
    session = get_session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
