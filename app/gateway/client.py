from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings


class SmsGateError(Exception):
    pass


class SmsGateClient:
    """Клиент SMS Gateway for Android (Private Server)."""

    def __init__(self) -> None:
        settings = get_settings()
        base = settings.smsgate_base_url.rstrip('/')
        self._api_base = f'{base}/api/3rdparty/v1'
        self._auth = (settings.smsgate_username, settings.smsgate_password)
        self._timeout = 30.0

    async def send_text(
        self,
        *,
        phone: str,
        text: str,
        device_id: str,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        phone_e164 = f'+{phone}' if not phone.startswith('+') else phone
        body: dict[str, Any] = {
            'phoneNumbers': [phone_e164],
            'textMessage': {'text': text},
            'deviceId': device_id,
            'withDeliveryReport': True,
        }
        if message_id:
            body['id'] = message_id

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f'{self._api_base}/messages',
                json=body,
                auth=self._auth,
            )
        if resp.status_code >= 400:
            raise SmsGateError(f'Gateway HTTP {resp.status_code}: {resp.text[:500]}')
        return resp.json()

    async def list_devices(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f'{self._api_base}/devices', auth=self._auth)
        if resp.status_code >= 400:
            raise SmsGateError(f'Gateway HTTP {resp.status_code}: {resp.text[:500]}')
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get('items') or data.get('devices') or []

    async def register_incoming_webhook(self, *, url: str, device_id: str | None = None) -> None:
        body: dict[str, Any] = {
            'url': url,
            'event': 'sms:received',
        }
        if device_id:
            body['device_id'] = device_id
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f'{self._api_base}/webhooks',
                json=body,
                auth=self._auth,
            )
        if resp.status_code >= 400:
            raise SmsGateError(f'Webhook register HTTP {resp.status_code}: {resp.text[:500]}')
