from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Device, IncomingMessage, MessageStatus, OutboundMessage

router = APIRouter(tags=['health'])


@router.get('/health')
def health() -> dict:
    db = SessionLocal()
    try:
        queued = db.scalar(
            select(func.count()).select_from(OutboundMessage).where(
                OutboundMessage.status == MessageStatus.QUEUED
            )
        )
        devices = db.scalar(select(func.count()).select_from(Device).where(Device.enabled.is_(True)))
        unprocessed_in = db.scalar(
            select(func.count()).select_from(IncomingMessage).where(IncomingMessage.processed.is_(False))
        )
        return {
            'status': 'ok',
            'queued': queued or 0,
            'active_devices': devices or 0,
            'incoming_unprocessed': unprocessed_in or 0,
        }
    finally:
        db.close()
