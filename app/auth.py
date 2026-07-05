from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.config import get_settings


def verify_api_key(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Нужен заголовок Authorization: Bearer <api_key>')
    token = authorization.removeprefix('Bearer ').strip()
    keys = get_settings().parsed_api_keys()
    for name, key in keys.items():
        if key == token:
            return name
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Неверный API-ключ')
