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


def _pick_device_credentials(db: Session) -> tuple[str, str] | None:
    device = db.scalar(
        select(Device)
        .where(
            Device.enabled.is_(True),
            Device.gateway_username != '',
            Device.gateway_password != '',
        )
        .order_by(Device.sort_order, Device.id)
        .limit(1)
    )
    if not device:
        return None
    return device.gateway_username, device.gateway_password


async def ensure_delivery_webhooks_registered(db: Session) -> None:
    creds = _pick_device_credentials(db)
    if not creds:
        logger.warning('Delivery webhooks: нет устройства с учёткой Gateway для регистрации')
        return

    username, password = creds
    client = SmsGateClient(username=username, password=password)
    url = _delivery_webhook_url()

    for event in DELIVERY_EVENTS:
        webhook_id = f'sms-delivery-{event.replace(":", "-")}'
        try:
            await client.register_webhook(url=url, event=event, webhook_id=webhook_id)
            logger.info('Gateway webhook registered: %s -> %s', event, url)
        except SmsGateError as exc:
            logger.warning('Gateway webhook register failed %s: %s', event, exc)
