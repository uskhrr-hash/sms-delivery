from __future__ import annotations

import itertools
import logging
import random
import threading
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.gateway.client import SmsGateClient, SmsGateError
from app.models import Device, MessageStatus, OutboundMessage

logger = logging.getLogger(__name__)


class DevicePicker:
    """Round-robin по включённым устройствам."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cycle: itertools.cycle[int] | None = None
        self._device_ids: list[int] = []

    def refresh(self, db: Session) -> None:
        rows = db.scalars(
            select(Device.id).where(Device.enabled.is_(True)).order_by(Device.sort_order, Device.id)
        ).all()
        with self._lock:
            if rows != self._device_ids:
                self._device_ids = list(rows)
                self._cycle = itertools.cycle(self._device_ids) if self._device_ids else None

    def next_device_id(self) -> int | None:
        with self._lock:
            if not self._cycle:
                return None
            return next(self._cycle)


device_picker = DevicePicker()


def claim_next_message(db: Session) -> OutboundMessage | None:
    msg = db.scalar(
        select(OutboundMessage)
        .where(OutboundMessage.status == MessageStatus.QUEUED)
        .order_by(OutboundMessage.created_at)
        .limit(1)
        .with_for_update()
    )
    if not msg:
        return None
    msg.status = MessageStatus.SENDING
    msg.attempts += 1
    db.commit()
    db.refresh(msg)
    return msg


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
    """Обработать одно сообщение. True — если что-то отправляли/пытались."""
    db = SessionLocal()
    try:
        device_picker.refresh(db)
        msg = claim_next_message(db)
        if not msg:
            return False

        device_db_id = device_picker.next_device_id()
        if device_db_id is None:
            msg.status = MessageStatus.QUEUED
            msg.last_error = 'Нет активных устройств'
            db.commit()
            return True

        device = db.get(Device, device_db_id)
        if not device:
            msg.status = MessageStatus.QUEUED
            msg.last_error = 'Устройство не найдено'
            db.commit()
            return True

        client = SmsGateClient()
        try:
            result = await client.send_text(
                phone=msg.phone,
                text=msg.text,
                device_id=device.gateway_device_id,
                message_id=msg.id,
            )
            msg.status = MessageStatus.SENT
            msg.device_id = device.id
            msg.sent_at = datetime.now(UTC)
            msg.last_error = None
            msg.gateway_message_id = str(result.get('id') or result.get('messageId') or '')
            device.last_sent_at = msg.sent_at
            logger.info('SMS sent id=%s phone=%s device=%s', msg.id, msg.phone, device.name)
        except SmsGateError as e:
            settings = get_settings()
            if msg.attempts >= settings.max_send_attempts:
                msg.status = MessageStatus.FAILED
            else:
                msg.status = MessageStatus.QUEUED
            msg.last_error = str(e)
            logger.warning('SMS send failed id=%s: %s', msg.id, e)
        db.commit()
        return True
    finally:
        db.close()


def human_delay_seconds() -> float:
    settings = get_settings()
    lo = min(settings.send_delay_min, settings.send_delay_max)
    hi = max(settings.send_delay_min, settings.send_delay_max)
    return random.uniform(lo, hi)
