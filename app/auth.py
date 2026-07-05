from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.api_clients import resolve_client_name


def verify_api_key(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Нужен заголовок Authorization: Bearer <api_key>')
    token = authorization.removeprefix('Bearer ').strip()
    name = resolve_client_name(db, token)
    if name:
        return name
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Неверный API-ключ')
