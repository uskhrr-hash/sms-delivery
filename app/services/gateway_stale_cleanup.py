from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gateway.client import SmsGateClient, SmsGateError
from app.models import DeliveryStatus, Device, OutboundMessage
from app.services.delivery_callbacks import mark_delivery_failed

logger = logging.getLogger(__name__)

# Шаблоны booking: «Дата: 08.11.2026, время: 17:30»
_APPOINTMENT_RE = re.compile(
    r'Дата:\s*(\d{2})\.(\d{2})\.(\d{4}),\s*время:\s*(\d{1,2}):(\d{2})',
    re.IGNORECASE,
)

LOCAL_TZ = ZoneInfo('Asia/Yekaterinburg')


def _parse_appointment_end(text: str) -> datetime | None:
    match = _APPOINTMENT_RE.search(text or '')
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    try:
        local_dt = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            tzinfo=LOCAL_TZ,
        )
    except ValueError:
        return None
    return local_dt.astimezone(UTC)


def _gateway_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _is_stale_gateway_item(
    item: dict,
    *,
    now: datetime,
    min_age: timedelta,
    only_past_appointments: bool,
) -> bool:
    text = (item.get('textMessage') or {}).get('text') or ''
    appt = _parse_appointment_end(text)
    if only_past_appointments:
        if appt is None:
            return False
        return appt + timedelta(hours=2) < now

    created = _gateway_created_at(item.get('createdAt'))
    if created and now - created >= min_age:
        return True
    if appt and appt + timedelta(hours=2) < now:
        return True
    return False


def _unique_gateway_credentials(db: Session, *, include_disabled: bool = True) -> list[tuple[str, str]]:
    devices = db.scalars(
        select(Device)
        .where(
            Device.gateway_username != '',
            Device.gateway_password != '',
        )
        .order_by(Device.sort_order, Device.id)
    ).all()
    seen: set[tuple[str, str]] = set()
    creds: list[tuple[str, str]] = []
    for device in devices:
        if not include_disabled and not device.enabled:
            continue
        key = (device.gateway_username.strip(), device.gateway_password)
        if key in seen:
            continue
        seen.add(key)
        creds.append(key)
    return creds


async def cancel_stale_gateway_pending(
    db: Session,
    *,
    min_age_hours: float = 6.0,
    only_past_appointments: bool = False,
    max_cancel: int = 200,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Снимает с очереди Private Server сообщения в Pending, которые уже не нужно слать.
    """
    now = datetime.now(UTC)
    min_age = timedelta(hours=min_age_hours)
    stats = {'scanned': 0, 'stale': 0, 'cancelled': 0, 'errors': 0, 'skipped_dry_run': 0}

    for username, password in _unique_gateway_credentials(db):
        client = SmsGateClient(username=username, password=password)
        offset = 0
        page_size = 50
        while stats['cancelled'] + stats['skipped_dry_run'] < max_cancel:
            try:
                batch = await client.list_messages(state='Pending', limit=page_size, offset=offset)
            except SmsGateError as exc:
                logger.warning('list_messages failed user=%s: %s', username, exc)
                stats['errors'] += 1
                break
            if not batch:
                break
            for item in batch:
                if stats['cancelled'] + stats['skipped_dry_run'] >= max_cancel:
                    break
                stats['scanned'] += 1
                if str(item.get('state')) != 'Pending':
                    continue
                if not _is_stale_gateway_item(
                    item,
                    now=now,
                    min_age=min_age,
                    only_past_appointments=only_past_appointments,
                ):
                    continue
                stats['stale'] += 1
                msg_id = str(item.get('id') or '')
                if not msg_id:
                    continue
                if dry_run:
                    stats['skipped_dry_run'] += 1
                    continue
                try:
                    await client.cancel_message(msg_id)
                    stats['cancelled'] += 1
                except SmsGateError as exc:
                    logger.info('cancel failed id=%s user=%s: %s', msg_id, username, exc)
                    stats['errors'] += 1
                    continue

                if outbound and outbound.delivery_status == DeliveryStatus.PENDING:
                    mark_delivery_failed(
                        db,
                        outbound,
                        error='Отменено: устарело в очереди Gateway',
                        callback_event='delivery.failed',
                    )

                await asyncio.sleep(0.05)

            if len(batch) < page_size:
                break
            offset += page_size

    logger.info(
        'Gateway stale cleanup: scanned=%s stale=%s cancelled=%s dry=%s errors=%s',
        stats['scanned'],
        stats['stale'],
        stats['cancelled'],
        stats['skipped_dry_run'],
        stats['errors'],
    )
    return stats
