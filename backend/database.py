"""SQLAlchemy 2 engine and session. SQLite locally, Postgres (e.g. Neon) in production: both come from DATABASE_URL."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    # SQLite connections are used from FastAPI's worker threads, so the same-thread check is switched off for it.
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = make_engine(get_settings().sqlalchemy_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind: Engine | None = None) -> None:
    """Create the tables if they do not exist (Phase 1 has no migrations)."""
    from backend import models  # noqa: F401  (registers the tables on Base)
    Base.metadata.create_all(bind or engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with SessionLocal() as session:
        yield session
