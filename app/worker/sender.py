from __future__ import annotations

import itertools
import logging
import random
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.gateway.client import SmsGateClient, SmsGateError
from app.models import Device, MessageStatus, OutboundMessage
from app.services.delivery_callbacks import mark_delivery_failed

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


def _device_ready_at(last_sent_at: datetime | None, min_interval_sec: float) -> datetime:
    if last_sent_at is None:
        return datetime.now(UTC)
    return last_sent_at + timedelta(seconds=min_interval_sec)


def seconds_until_device_ready(last_sent_at: datetime | None, min_interval_sec: float) -> float:
    ready_at = _device_ready_at(last_sent_at, min_interval_sec)
    return max(0.0, (ready_at - datetime.now(UTC)).total_seconds())


def pick_ready_device(db: Session, min_interval_sec: float) -> Device | None:
    """Round-robin среди устройств, готовых к отправке (не чаще min_interval_sec с last_sent_at)."""
    device_picker.refresh(db)
    ids = device_picker._device_ids
    if not ids:
        return None
    for _ in ids:
        device_id = device_picker.next_device_id()
        if device_id is None:
            return None
        device = db.get(Device, device_id)
        if not device or not device.enabled:
            continue
        if seconds_until_device_ready(device.last_sent_at, min_interval_sec) <= 0:
            return device
    return None


def seconds_until_any_device_ready(db: Session, min_interval_sec: float) -> float | None:
    """
    Секунды до ближайшего готового устройства.
    None — нет включённых устройств; 0 — хотя бы одно готово сейчас.
    """
    rows = db.scalars(
        select(Device).where(Device.enabled.is_(True)).order_by(Device.sort_order, Device.id)
    ).all()
    if not rows:
        return None
    waits = [seconds_until_device_ready(d.last_sent_at, min_interval_sec) for d in rows]
    return min(waits)


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
    """
    Обработать одно сообщение. True — взяли из очереди и отправили/зафиксировали ошибку.
    False — очередь пуста или все устройства в паузе (сообщения остаются queued).
    """
    settings = get_settings()
    min_interval = float(settings.device_send_min_interval_sec)
    db = SessionLocal()
    try:
        device = pick_ready_device(db, min_interval)
        if device is None:
            return False

        msg = claim_next_message(db)
        if not msg:
            return False

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
            logger.info('SMS sent id=%s phone=%s device=%s', msg.id, msg.phone, device.name)
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
