from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.gateway.client import SmsGateClient, SmsGateError
from app.models import Device

logger = logging.getLogger(__name__)

DELIVERY_EVENTS = ('sms:sent', 'sms:delivered', 'sms:failed')


def _delivery_webhook_url() -> str:
    base = get_settings().public_base_url.rstrip('/')
    return f'{base}/api/v1/webhooks/delivery'


def _unique_gateway_credentials(db: Session) -> list[tuple[str, str]]:
    """У каждого телефона в Gateway своя учётка — webhooks нужны для каждой."""
    devices = db.scalars(
        select(Device)
        .where(
            Device.enabled.is_(True),
            Device.gateway_username != '',
            Device.gateway_password != '',
        )
        .order_by(Device.sort_order, Device.id)
    ).all()
    seen: set[tuple[str, str]] = set()
    creds: list[tuple[str, str]] = []
    for device in devices:
        key = (device.gateway_username.strip(), device.gateway_password)
        if key in seen:
            continue
        seen.add(key)
        creds.append(key)
    return creds


async def ensure_delivery_webhooks_registered(db: Session) -> None:
    creds_list = _unique_gateway_credentials(db)
    if not creds_list:
        logger.warning('Delivery webhooks: нет устройства с учёткой Gateway для регистрации')
        return

    url = _delivery_webhook_url()

    for username, password in creds_list:
        client = SmsGateClient(username=username, password=password)
        user_slug = username.strip().lower()
        for event in DELIVERY_EVENTS:
            webhook_id = f'sms-delivery-{user_slug}-{event.replace(":", "-")}'
            try:
                await client.register_webhook(url=url, event=event, webhook_id=webhook_id)
                logger.info('Gateway webhook registered: user=%s event=%s -> %s', username, event, url)
            except SmsGateError as exc:
                logger.warning(
                    'Gateway webhook register failed user=%s event=%s: %s',
                    username,
                    event,
                    exc,
                )
