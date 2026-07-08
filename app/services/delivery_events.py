from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeliveryStatus, MessageStatus, OutboundMessage
from app.schemas import IncomingWebhookPayload
from app.services.delivery_callbacks import mark_delivery_delivered, mark_delivery_failed

logger = logging.getLogger(__name__)

DELIVERY_EVENTS = frozenset({'sms:sent', 'sms:delivered', 'sms:failed'})


def _find_outbound_message(db: Session, payload: IncomingWebhookPayload) -> OutboundMessage | None:
    inner = payload.payload or {}
    candidates: list[str] = []
    for key in ('messageId', 'id'):
        val = inner.get(key)
        if val:
            candidates.append(str(val))
    if payload.id:
        candidates.append(str(payload.id))

    for candidate in candidates:
        msg = db.get(OutboundMessage, candidate)
        if msg:
            return msg
        msg = db.scalar(
            select(OutboundMessage).where(OutboundMessage.gateway_message_id == candidate)
        )
        if msg:
            return msg
    return None


def _extract_failure_reason(inner: dict) -> str:
    for key in ('reason', 'error', 'errorMessage', 'message', 'details'):
        val = inner.get(key)
        if val:
            return str(val)[:2000]
    return 'Сбой отправки на телефоне (sms:failed)'


def handle_gateway_delivery_event(db: Session, data: dict) -> dict:
    payload = IncomingWebhookPayload.model_validate(data)
    event = payload.event
    if event not in DELIVERY_EVENTS:
        return {'ok': True, 'ignored': event}

    msg = _find_outbound_message(db, payload)
    if not msg:
        logger.info('Delivery webhook without matching message: event=%s', event)
        return {'ok': True, 'unknown_message': True}

    inner = payload.payload or {}

    if event == 'sms:sent':
        msg.delivery_status = DeliveryStatus.SENT_TO_CARRIER
        db.commit()
        return {'ok': True, 'message_id': msg.id, 'delivery_status': msg.delivery_status.value}

    if event == 'sms:delivered':
        if msg.delivery_status != DeliveryStatus.DELIVERED:
            mark_delivery_delivered(db, msg)
        return {'ok': True, 'message_id': msg.id, 'delivery_status': DeliveryStatus.DELIVERED.value}

    if event == 'sms:failed':
        if msg.delivery_status != DeliveryStatus.DELIVERED:
            mark_delivery_failed(db, msg, error=_extract_failure_reason(inner))
        return {'ok': True, 'message_id': msg.id, 'delivery_status': DeliveryStatus.FAILED.value}

    return {'ok': True, 'ignored': event}
