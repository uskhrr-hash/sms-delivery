from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IncomingMessage
from app.schemas import IncomingWebhookPayload

router = APIRouter(prefix='/api/v1/webhooks', tags=['webhooks'])


@router.post('/incoming')
async def incoming_sms(request: Request, db: Session = Depends(get_db)) -> dict:
    """
    Входящие СМС от SMS Gateway (событие sms:received).
    Регистрируется в gateway: POST .../webhooks с event=sms:received.
    """
    data = await request.json()
    payload = IncomingWebhookPayload.model_validate(data)
    if payload.event != 'sms:received':
        return {'ok': True, 'ignored': payload.event}

    inner = payload.payload
    event_id = payload.id or inner.get('messageId')
    if event_id:
        exists = db.scalar(
            select(IncomingMessage).where(IncomingMessage.gateway_event_id == event_id)
        )
        if exists:
            return {'ok': True, 'duplicate': True}

    received_raw = inner.get('receivedAt')
    received_at = None
    if received_raw:
        try:
            received_at = datetime.fromisoformat(received_raw.replace('Z', '+00:00'))
        except ValueError:
            received_at = None

    row = IncomingMessage(
        gateway_event_id=event_id,
        device_gateway_id=payload.deviceId or '',
        sender=str(inner.get('sender', '')),
        recipient=str(inner.get('recipient', '')),
        text=str(inner.get('message', '')),
        received_at=received_at,
        raw_payload=json.dumps(data, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return {'ok': True}
