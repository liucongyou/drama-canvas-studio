from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings


def _engine() -> Engine:
    settings = get_settings()
    engine = create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=1800,
        pool_use_lifo=True,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def configure_connection(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(
                f"SET statement_timeout = {int(settings.db_statement_timeout_ms)}"
            )
            cursor.execute(
                f"SET lock_timeout = {int(settings.db_lock_timeout_ms)}"
            )

    return engine


engine = _engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def warm_connection_pool() -> int:
    """Open the configured base pool before the first user request.

    On Windows each new PostgreSQL socket can take hundreds of milliseconds.
    Letting that happen in parallel request handlers creates a false p95 spike
    even when the SQL itself is sub-millisecond.
    """
    target = int(get_settings().db_pool_size)
    connections = []
    try:
        for _ in range(target):
            connections.append(engine.connect())
    finally:
        for connection in connections:
            connection.close()
    return target


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def transactional_session() -> Iterator[Session]:
    """Commit on success and roll back every partial write on failure."""
    session = SessionLocal()
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def set_tenant_scope(session: Session, tenant_id: str) -> None:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
