from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models import MessageStatus, DeliveryStatus


def normalize_phone(phone: str) -> str:
    digits = re.sub(r'\D', '', phone.strip())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if len(digits) == 10:
        digits = '7' + digits
    if len(digits) != 11 or not digits.startswith('7'):
        raise ValueError('Номер должен быть в формате 79XXXXXXXXX')
    return digits


class SendMessageRequest(BaseModel):
    phone: str
    text: str = Field(min_length=1, max_length=1000)
    idempotency_key: str | None = Field(default=None, max_length=200)
    source: str = Field(default='', max_length=50)
    ref_id: str = Field(default='', max_length=100)
    center_id: str | None = Field(
        default=None,
        max_length=32,
        description='ID центра/магазина у клиента; маршрутизация на привязанный телефон',
    )

    @field_validator('center_id')
    @classmethod
    def validate_center_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text if text else None

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator('text')
    @classmethod
    def validate_text(cls, v: str) -> str:
        text = v.strip()
        if not text:
            raise ValueError('Текст не может быть пустым')
        return text


class MessageResponse(BaseModel):
    id: str
    status: MessageStatus
    delivery_status: DeliveryStatus
    phone: str
    source: str
    ref_id: str
    center_id: str
    device_id: int | None
    created_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    last_error: str | None
    delivery_error: str | None
    callback_at: datetime | None

    model_config = {'from_attributes': True}


class IncomingWebhookPayload(BaseModel):
    deviceId: str | None = None
    event: str
    id: str | None = None
    payload: dict
