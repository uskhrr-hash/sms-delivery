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


def _migrate_schema() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if 'devices' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('devices')}
        statements: list[str] = []
        if 'gateway_username' not in cols:
            statements.append("ALTER TABLE devices ADD COLUMN gateway_username VARCHAR(64) DEFAULT ''")
        if 'gateway_password' not in cols:
            statements.append("ALTER TABLE devices ADD COLUMN gateway_password VARCHAR(128) DEFAULT ''")
        for stmt in statements:
            with engine.begin() as conn:
                conn.execute(text(stmt))

    if 'api_clients' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('api_clients')}
        if 'callback_url' not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE api_clients ADD COLUMN callback_url VARCHAR(500) DEFAULT ''"))

    if 'outbound_messages' in insp.get_table_names():
        cols = {c['name'] for c in insp.get_columns('outbound_messages')}
        ts_type = 'TIMESTAMP WITH TIME ZONE'
        if get_settings().database_url.startswith('sqlite'):
            ts_type = 'DATETIME'
        statements = []
        if 'delivery_status' not in cols:
            statements.append(
                "ALTER TABLE outbound_messages ADD COLUMN delivery_status VARCHAR(32) DEFAULT 'pending'"
            )
        if 'delivery_error' not in cols:
            statements.append('ALTER TABLE outbound_messages ADD COLUMN delivery_error TEXT')
        if 'delivered_at' not in cols:
            statements.append(f'ALTER TABLE outbound_messages ADD COLUMN delivered_at {ts_type}')
        if 'callback_at' not in cols:
            statements.append(f'ALTER TABLE outbound_messages ADD COLUMN callback_at {ts_type}')
        for stmt in statements:
            with engine.begin() as conn:
                conn.execute(text(stmt))


def init_db() -> None:
    from app import models  # noqa: F401

    if get_settings().database_url.startswith('sqlite'):
        from pathlib import Path

        Path('data').mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
