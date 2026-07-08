from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiClient, DeliveryStatus, MessageStatus, OutboundMessage

logger = logging.getLogger(__name__)


def _resolve_callback_url(db: Session, source: str) -> str | None:
    name = (source or '').strip().lower()
    if name:
        row = db.scalar(select(ApiClient).where(ApiClient.name == name, ApiClient.enabled.is_(True)))
        if row and (row.callback_url or '').strip():
            return row.callback_url.strip()
    return None


def _callback_payload(msg: OutboundMessage, *, event: str) -> dict:
    return {
        'event': event,
        'message_id': msg.id,
        'phone': msg.phone,
        'source': msg.source,
        'ref_id': msg.ref_id,
        'idempotency_key': msg.idempotency_key,
        'status': msg.status.value,
        'delivery_status': msg.delivery_status.value,
        'last_error': msg.last_error,
        'delivery_error': msg.delivery_error,
        'device_id': msg.device_id,
        'sent_at': msg.sent_at.isoformat() if msg.sent_at else None,
        'delivered_at': msg.delivered_at.isoformat() if msg.delivered_at else None,
    }


def notify_client_if_needed(db: Session, msg: OutboundMessage, *, event: str) -> None:
    if msg.callback_at is not None:
        return
    url = _resolve_callback_url(db, msg.source)
    if not url:
        return

    settings = get_settings()
    headers = {'Content-Type': 'application/json'}
    secret = (settings.callback_secret or '').strip()
    if secret:
        headers['X-SMS-Delivery-Secret'] = secret

    body = _callback_payload(msg, event=event)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                'Callback failed message=%s url=%s HTTP %s: %s',
                msg.id,
                url,
                resp.status_code,
                resp.text[:300],
            )
            return
        msg.callback_at = datetime.now(UTC)
        logger.info('Callback sent message=%s event=%s url=%s', msg.id, event, url)
    except Exception as exc:
        logger.warning('Callback error message=%s url=%s: %s', msg.id, url, exc)


def mark_delivery_failed(
    db: Session,
    msg: OutboundMessage,
    *,
    error: str,
    callback_event: str = 'delivery.failed',
) -> None:
    msg.delivery_status = DeliveryStatus.FAILED
    msg.delivery_error = error[:2000]
    if msg.status != MessageStatus.FAILED:
        msg.status = MessageStatus.FAILED
    if not msg.last_error:
        msg.last_error = error[:2000]
    db.commit()
    db.refresh(msg)
    notify_client_if_needed(db, msg, event=callback_event)


def mark_delivery_delivered(db: Session, msg: OutboundMessage) -> None:
    msg.delivery_status = DeliveryStatus.DELIVERED
    msg.delivery_error = None
    msg.delivered_at = datetime.now(UTC)
    db.commit()
    db.refresh(msg)
    notify_client_if_needed(db, msg, event='delivery.delivered')
