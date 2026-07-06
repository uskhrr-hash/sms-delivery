from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.gateway.client import SmsGateClient, SmsGateError
from app.models import ApiClient, Device, MessageStatus, OutboundMessage


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def collect_checks(db: Session) -> list[CheckResult]:
    settings = get_settings()
    results: list[CheckResult] = []

    results.append(CheckResult('База данных', True, 'Подключение OK'))

    queued = db.scalar(
        select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.status == MessageStatus.QUEUED
        )
    ) or 0
    devices_on = db.scalar(
        select(func.count()).select_from(Device).where(Device.enabled.is_(True))
    ) or 0
    clients_on = db.scalar(
        select(func.count()).select_from(ApiClient).where(ApiClient.enabled.is_(True))
    ) or 0

    results.append(
        CheckResult(
            'Очередь СМС',
            True,
            f'В очереди: {queued}; активных телефонов: {devices_on}; API-клиентов: {clients_on}',
        )
    )

    if devices_on == 0:
        results.append(
            CheckResult(
                'Телефоны',
                False,
                'Нет включённых устройств в админке — отправка невозможна',
            )
        )
    else:
        without_creds = db.scalars(
            select(Device).where(
                Device.enabled.is_(True),
                (Device.gateway_username == '') | (Device.gateway_password == ''),
            )
        ).all()
        if without_creds:
            names = ', '.join(d.name for d in without_creds)
            results.append(
                CheckResult(
                    'Телефоны',
                    False,
                    f'Без логина/пароля Gateway: {names}',
                )
            )
        else:
            results.append(CheckResult('Телефоны', True, f'Настроено устройств: {devices_on}'))

    gw_url = settings.smsgate_base_url.rstrip('/') + '/health'
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(gw_url)
        if resp.status_code == 200 and '"pass"' in resp.text:
            results.append(CheckResult('SMS Gateway', True, f'{gw_url} — OK'))
        else:
            results.append(
                CheckResult('SMS Gateway', False, f'HTTP {resp.status_code}: {resp.text[:120]}')
            )
    except Exception as e:
        results.append(CheckResult('SMS Gateway', False, str(e)))

    try:
        probe = db.scalar(
            select(Device)
            .where(
                Device.enabled.is_(True),
                Device.gateway_username != '',
                Device.gateway_password != '',
            )
            .order_by(Device.sort_order, Device.id)
            .limit(1)
        )
        if probe:
            gate = SmsGateClient(username=probe.gateway_username, password=probe.gateway_password)
        else:
            gate = SmsGateClient()
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f'{gate._api_base}/devices', auth=gate._auth)
        if resp.status_code >= 400:
            raise SmsGateError(f'HTTP {resp.status_code}')
        data = resp.json()
        items = data if isinstance(data, list) else data.get('items') or data.get('devices') or []
        results.append(CheckResult('Gateway API (устройства)', True, f'Ответ: {len(items)} устройств'))
    except Exception as e:
        results.append(CheckResult('Gateway API (устройства)', False, str(e)))

    return results


def enqueue_test_sms(db: Session, phone: str, text: str) -> OutboundMessage:
    import uuid

    from app.schemas import normalize_phone

    normalized = normalize_phone(phone)
    msg = OutboundMessage(
        phone=normalized,
        text=text.strip(),
        source='admin-test',
        ref_id='test',
        idempotency_key=f'admin-test-{uuid.uuid4()}',
        status=MessageStatus.QUEUED,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg
