from __future__ import annotations

import logging
import random
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.gateway.client import SmsGateClient, SmsGateError
from app.models import Device, MessageStatus, OutboundMessage
from app.services.delivery_callbacks import mark_delivery_failed
from app.services.send_routing import (
    route_device_picker,
    seconds_until_device_ready,
    seconds_until_message_sendable,
)

logger = logging.getLogger(__name__)


def claim_next_sendable_pair(
    db: Session, min_interval_sec: float
) -> tuple[OutboundMessage, Device] | None:
    """
    Старейшее сообщение в очереди, для которого есть готовое устройство по правилам center_id.
    Не блокирует очередь других центров, если у первого сообщения пауза rate-limit.
    """
    queued = db.scalars(
        select(OutboundMessage)
        .where(OutboundMessage.status == MessageStatus.QUEUED)
        .order_by(OutboundMessage.created_at)
        .limit(100)
    ).all()
    for candidate in queued:
        device = route_device_picker.pick_ready(db, candidate.center_id or None, min_interval_sec)
        if device is None:
            continue
        msg = db.scalar(
            select(OutboundMessage)
            .where(
                OutboundMessage.id == candidate.id,
                OutboundMessage.status == MessageStatus.QUEUED,
            )
            .with_for_update()
        )
        if not msg:
            continue
        msg.status = MessageStatus.SENDING
        msg.attempts += 1
        db.commit()
        db.refresh(msg)
        return msg, device
    return None


def seconds_until_any_device_ready(db: Session, min_interval_sec: float) -> float | None:
    """
    Секунды до ближайшей возможной отправки с учётом маршрутизации.
    None — нет подходящих устройств для текущей очереди.
    """
    queued = db.scalars(
        select(OutboundMessage)
        .where(OutboundMessage.status == MessageStatus.QUEUED)
        .order_by(OutboundMessage.created_at)
        .limit(100)
    ).all()
    if not queued:
        return None
    waits: list[float] = []
    for msg in queued:
        w = seconds_until_message_sendable(db, msg.center_id or None, min_interval_sec)
        if w is None:
            continue
        if w <= 0:
            return 0.0
        waits.append(w)
    return min(waits) if waits else None


def has_queued_messages() -> bool:
    db = SessionLocal()
    try:
        found = db.scalar(
            select(OutboundMessage.id)
            .where(OutboundMessage.status == MessageStatus.QUEUED)
            .limit(1)
        )
        return found is not None
    finally:
        db.close()


async def process_one_message() -> bool:
    """
    Обработать одно сообщение. True — взяли из очереди и отправили/зафиксировали ошибку.
    False — очередь пуста или нет готовых устройств (сообщения остаются queued).
    """
    settings = get_settings()
    min_interval = float(settings.device_send_min_interval_sec)
    db = SessionLocal()
    try:
        pair = claim_next_sendable_pair(db, min_interval)
        if pair is None:
            return False
        msg, device = pair

        if not device.gateway_username or not device.gateway_password:
            msg.status = MessageStatus.QUEUED
            msg.last_error = f'У устройства «{device.name}» не заданы логин/пароль Gateway (админка)'
            db.commit()
            return True

        client = SmsGateClient(
            username=device.gateway_username,
            password=device.gateway_password,
        )
        try:
            result = await client.send_text(
                phone=msg.phone,
                text=msg.text,
                device_id=device.gateway_device_id,
                message_id=msg.id,
                sim_number=device.sim_number,
            )
            msg.status = MessageStatus.SENT
            msg.device_id = device.id
            msg.sent_at = datetime.now(UTC)
            msg.last_error = None
            msg.gateway_message_id = str(result.get('id') or result.get('messageId') or msg.id)
            device.last_sent_at = msg.sent_at
            db.commit()
            logger.info(
                'SMS sent id=%s phone=%s device=%s center=%s',
                msg.id,
                msg.phone,
                device.name,
                msg.center_id or '-',
            )
        except SmsGateError as e:
            err = str(e)
            if msg.attempts >= settings.max_send_attempts:
                msg.status = MessageStatus.FAILED
                msg.last_error = err
                db.commit()
                mark_delivery_failed(db, msg, error=err, callback_event='delivery.failed')
            else:
                msg.status = MessageStatus.QUEUED
                msg.last_error = err
                db.commit()
            logger.warning('SMS send failed id=%s: %s', msg.id, e)
        return True
    finally:
        db.close()


def human_delay_seconds() -> float:
    settings = get_settings()
    lo = min(settings.send_delay_min, settings.send_delay_max)
    hi = max(settings.send_delay_min, settings.send_delay_max)
    return random.uniform(lo, hi)
