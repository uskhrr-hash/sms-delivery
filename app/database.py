from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


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
        ts_type = 'TIMESTAMPTZ' if not get_settings().database_url.startswith('sqlite') else 'DATETIME'
        is_pg = not get_settings().database_url.startswith('sqlite')
        added_delivery_status = 'delivery_status' not in cols
        statements = []
        if added_delivery_status:
            statements.append(
                "ALTER TABLE outbound_messages ADD COLUMN delivery_status VARCHAR(32) NOT NULL DEFAULT 'pending'"
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
        if is_pg and (added_delivery_status or 'delivery_status' in cols):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        'ALTER TABLE outbound_messages '
                        'ALTER COLUMN delivery_status TYPE VARCHAR(32) '
                        'USING lower(delivery_status::text)'
                    )
                )
                conn.execute(
                    text(
                        "UPDATE outbound_messages SET delivery_status = 'pending' "
                        "WHERE delivery_status IS NULL OR delivery_status = ''"
                    )
                )
            with engine.begin() as conn:
                conn.execute(text('DROP TYPE IF EXISTS deliverystatus'))


def init_db() -> None:
    from app import models  # noqa: F401

    if get_settings().database_url.startswith('sqlite'):
        from pathlib import Path

        Path('data').mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    try:
        _migrate_schema()
    except Exception:
        logger.exception('DB schema migration failed')
        raise
