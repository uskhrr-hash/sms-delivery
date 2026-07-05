from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import verify_api_key
from app.database import get_db
from app.models import MessageStatus, OutboundMessage
from app.schemas import MessageResponse, SendMessageRequest

router = APIRouter(prefix='/api/v1', tags=['messages'])


@router.post('/messages', response_model=MessageResponse)
def enqueue_message(
    body: SendMessageRequest,
    db: Session = Depends(get_db),
    client_name: str = Depends(verify_api_key),
) -> OutboundMessage:
    if body.idempotency_key:
        existing = db.scalar(
            select(OutboundMessage).where(OutboundMessage.idempotency_key == body.idempotency_key)
        )
        if existing:
            return existing

    msg = OutboundMessage(
        phone=body.phone,
        text=body.text,
        source=body.source or client_name,
        ref_id=body.ref_id,
        idempotency_key=body.idempotency_key,
        status=MessageStatus.QUEUED,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


@router.get('/messages/{message_id}', response_model=MessageResponse)
def get_message(
    message_id: str,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> OutboundMessage:
    msg = db.get(OutboundMessage, message_id)
    if not msg:
        raise HTTPException(404, 'Сообщение не найдено')
    return msg
