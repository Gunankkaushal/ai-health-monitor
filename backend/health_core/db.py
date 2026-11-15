"""SQLAlchemy engine/session factory shared by all processes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import resolve_database_url


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=8)
def get_engine(url: str | None = None) -> Engine:
    resolved = resolve_database_url(url)
    engine = create_engine(resolved, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        # WAL + busy timeout: the backend and three MCP server processes share this file.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(url: str | None = None) -> None:
    from . import models, resilience_models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine(url))
