from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gateway.client import SmsGateClient, SmsGateError
from app.models import DeliveryStatus, Device, MessageStatus, OutboundMessage
from app.services.delivery_callbacks import mark_delivery_delivered, mark_delivery_failed

logger = logging.getLogger(__name__)


def _pending_sent_messages(db: Session, *, limit: int) -> list[OutboundMessage]:
    return list(
        db.scalars(
            select(OutboundMessage)
            .where(
                OutboundMessage.status == MessageStatus.SENT,
                OutboundMessage.delivery_status == DeliveryStatus.PENDING,
            )
            .order_by(OutboundMessage.sent_at.desc(), OutboundMessage.created_at.desc())
            .limit(limit)
        ).all()
    )


async def sync_pending_from_gateway(db: Session, *, limit: int = 150) -> int:
    """
    Подтягивает delivery_status из Private Server для сообщений без webhook
    (например, если webhook не был зарегистрирован для учётки телефона).
    """
    messages = _pending_sent_messages(db, limit=limit)
    if not messages:
        return 0

    device_cache: dict[int, Device | None] = {}
    updated = 0

    for msg in messages:
        if msg.device_id is None:
            continue
        if msg.device_id not in device_cache:
            device_cache[msg.device_id] = db.get(Device, msg.device_id)
        device = device_cache[msg.device_id]
        if device is None or not device.gateway_username or not device.gateway_password:
            continue

        client = SmsGateClient(
            username=device.gateway_username,
            password=device.gateway_password,
        )
        try:
            data = await client.get_message(msg.id)
        except SmsGateError as exc:
            logger.debug('Gateway get_message failed id=%s: %s', msg.id, exc)
            continue
        if not data:
            continue

        state = str(data.get('state') or '')
        if state == 'Delivered':
            mark_delivery_delivered(db, msg)
            updated += 1
        elif state == 'Failed':
            mark_delivery_failed(db, msg, error='Gateway: Failed')
            updated += 1

        await asyncio.sleep(0.05)

    if updated:
        logger.info('Gateway delivery sync: updated %s message(s)', updated)
    return updated
