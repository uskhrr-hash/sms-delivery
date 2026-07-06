from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith('sqlite'):
        connect_args['check_same_thread'] = False
    return create_engine(settings.database_url, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_devices_gateway_credentials() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if 'devices' not in insp.get_table_names():
        return
    cols = {c['name'] for c in insp.get_columns('devices')}
    statements: list[str] = []
    if 'gateway_username' not in cols:
        statements.append("ALTER TABLE devices ADD COLUMN gateway_username VARCHAR(64) DEFAULT ''")
    if 'gateway_password' not in cols:
        statements.append("ALTER TABLE devices ADD COLUMN gateway_password VARCHAR(128) DEFAULT ''")
    if not statements:
        return
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def init_db() -> None:
    from app import models  # noqa: F401

    if get_settings().database_url.startswith('sqlite'):
        from pathlib import Path

        Path('data').mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate_devices_gateway_credentials()
