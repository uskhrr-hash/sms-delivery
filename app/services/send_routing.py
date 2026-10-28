"""Выбор устройства для исходящей СМС по center_id (Booking / магазин)."""
from __future__ import annotations

import itertools
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Device


def normalize_center_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _unbound_filter():
    return or_(Device.bind_center_id.is_(None), Device.bind_center_id == '')


def center_has_dedicated_devices(db: Session, center_id: str) -> bool:
    cid = normalize_center_id(center_id)
    if not cid:
        return False
    found = db.scalar(
        select(Device.id)
        .where(Device.enabled.is_(True), Device.bind_center_id == cid)
        .limit(1)
    )
    return found is not None


def list_eligible_devices(db: Session, center_id: str | None) -> list[Device]:
    """Устройства, с которых разрешено отправить сообщение для этого center_id."""
    cid = normalize_center_id(center_id)
    if cid and center_has_dedicated_devices(db, cid):
        return list(
            db.scalars(
                select(Device)
                .where(Device.enabled.is_(True), Device.bind_center_id == cid)
                .order_by(Device.sort_order, Device.id)
            ).all()
        )
    return list(
        db.scalars(
            select(Device)
            .where(Device.enabled.is_(True), _unbound_filter())
            .order_by(Device.sort_order, Device.id)
        ).all()
    )


def _device_ready_at(last_sent_at: datetime | None, min_interval_sec: float) -> datetime:
    if last_sent_at is None:
        return datetime.now(UTC)
    return last_sent_at + timedelta(seconds=min_interval_sec)


def seconds_until_device_ready(last_sent_at: datetime | None, min_interval_sec: float) -> float:
    ready_at = _device_ready_at(last_sent_at, min_interval_sec)
    return max(0.0, (ready_at - datetime.now(UTC)).total_seconds())


class RouteDevicePicker:
    """Round-robin отдельно для каждого пула (центр или общий)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cycles: dict[str, itertools.cycle[int] | None] = {}
        self._device_ids: dict[str, list[int]] = {}

    def _pool_key(self, db: Session, center_id: str | None) -> str:
        cid = normalize_center_id(center_id)
        if cid and center_has_dedicated_devices(db, cid):
            return f'center:{cid}'
        return 'shared'

    def _refresh(self, db: Session, pool_key: str, devices: list[Device]) -> list[int]:
        ids = [d.id for d in devices]
        with self._lock:
            if ids != self._device_ids.get(pool_key):
                self._device_ids[pool_key] = ids
                self._cycles[pool_key] = itertools.cycle(ids) if ids else None
        return ids

    def pick_ready(self, db: Session, center_id: str | None, min_interval_sec: float) -> Device | None:
        devices = list_eligible_devices(db, center_id)
        pool_key = self._pool_key(db, center_id)
        ids = self._refresh(db, pool_key, devices)
        if not ids:
            return None
        with self._lock:
            cycle = self._cycles.get(pool_key)
            if not cycle:
                return None
            for _ in ids:
                device_id = next(cycle)
                device = db.get(Device, device_id)
                if not device or not device.enabled:
                    continue
                if seconds_until_device_ready(device.last_sent_at, min_interval_sec) <= 0:
                    return device
        return None


route_device_picker = RouteDevicePicker()


def seconds_until_message_sendable(
    db: Session, center_id: str | None, min_interval_sec: float
) -> float | None:
    """Секунды до готовности пула для center_id; None — нет устройств; 0 — можно сейчас."""
    devices = list_eligible_devices(db, center_id)
    if not devices:
        return None
    waits = [seconds_until_device_ready(d.last_sent_at, min_interval_sec) for d in devices]
    return min(waits)
