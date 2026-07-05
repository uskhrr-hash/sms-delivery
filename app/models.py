from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageStatus(str, enum.Enum):
    QUEUED = 'queued'
    SENDING = 'sending'
    SENT = 'sent'
    FAILED = 'failed'


class Device(Base):
    __tablename__ = 'devices'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    gateway_device_id: Mapped[str] = mapped_column(String(128), unique=True)
    phone_label: Mapped[str] = mapped_column(String(32), default='')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboundMessage(Base):
    __tablename__ = 'outbound_messages'
    __table_args__ = (UniqueConstraint('idempotency_key', name='uq_idempotency_key'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, native_enum=False), default=MessageStatus.QUEUED, index=True
    )
    source: Mapped[str] = mapped_column(String(50), default='')
    ref_id: Mapped[str] = mapped_column(String(100), default='')
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    device_id: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    gateway_message_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncomingMessage(Base):
    __tablename__ = 'incoming_messages'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    gateway_event_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    device_gateway_id: Mapped[str] = mapped_column(String(128), default='', index=True)
    sender: Mapped[str] = mapped_column(String(32), index=True)
    recipient: Mapped[str] = mapped_column(String(32), default='')
    text: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    raw_payload: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
