from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiClient


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def mask_api_key(key: str) -> str:
    if len(key) <= 12:
        return key[:3] + '…'
    return f'{key[:6]}…{key[-4:]}'


def resolve_client_name(db: Session, token: str) -> str | None:
    row = db.scalar(
        select(ApiClient).where(ApiClient.api_key == token, ApiClient.enabled.is_(True))
    )
    if row:
        return row.name

    for name, key in get_settings().parsed_api_keys().items():
        if key == token:
            return name
    return None


def seed_clients_from_env(db: Session) -> None:
    """Один раз импортировать ключи из .env, если таблица пустая."""
    count = db.scalar(select(func.count()).select_from(ApiClient)) or 0
    if count > 0:
        return
    for name, key in get_settings().parsed_api_keys().items():
        if not name or not key:
            continue
        db.add(ApiClient(name=name, api_key=key, enabled=True))
    db.commit()


def create_client(db: Session, name: str) -> ApiClient:
    name = name.strip().lower()
    if not name:
        raise ValueError('Имя не может быть пустым')
    if db.scalar(select(ApiClient).where(ApiClient.name == name)):
        raise ValueError(f'Клиент «{name}» уже существует')
    client = ApiClient(name=name, api_key=generate_api_key(), enabled=True)
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def regenerate_key(db: Session, client_id: int) -> ApiClient | None:
    client = db.get(ApiClient, client_id)
    if not client:
        return None
    client.api_key = generate_api_key()
    db.commit()
    db.refresh(client)
    return client
