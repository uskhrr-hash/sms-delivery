# SMS Delivery — инструкция для разработчиков

Документ для подключения **внешних сайтов** (PHP-магазин, другие сервисы) к сервису доставки СМС.

Сервис **не решает, когда слать** — только принимает сообщение в очередь и доставляет через Android-телефоны.

---

## Базовые данные

| Параметр | Значение |
|----------|----------|
| **Базовый URL (prod)** | `https://sms-api.shinaufa.ru` |
| **Протокол** | HTTPS only |
| **Формат** | JSON, UTF-8 |
| **Авторизация** | Bearer token (отдельный ключ на каждый сайт) |

Ключ API выдаёт администратор через **админку** → [API-клиенты](https://sms-api.shinaufa.ru/admin/clients) (или из `.env` при первом запуске).

---

## Как устроена доставка

1. Ваш сайт вызывает `POST /api/v1/messages` — сообщение попадает в очередь (`status: queued`).
2. Фоновый воркер забирает очередь и отправляет через SMS Gateway на телефон.
3. **Первая СМС после простоя** уходит **сразу**.
4. **Следующие подряд** в очереди — с паузой **60–180 сек** между ними (имитация ручной отправки).
5. Несколько телефонов — **round-robin** (по очереди).

**Пакетной отправки нет** — один HTTP-запрос = одна СМС в очередь.

---

## Авторизация

В каждый запрос добавляйте заголовок:

```http
Authorization: Bearer ВАШ_API_КЛЮЧ
```

Пример:

```http
Authorization: Bearer xK9mP2qR7vN4wL8sT1uY3zA6bC0dE5fG
```

При неверном ключе: **401 Unauthorized**.

---

## POST /api/v1/messages — поставить СМС в очередь

### Запрос

```http
POST /api/v1/messages
Authorization: Bearer ВАШ_API_КЛЮЧ
Content-Type: application/json
```

```json
{
  "phone": "79991234567",
  "text": "Ваш заказ №123 готов к выдаче. Адрес: ...",
  "idempotency_key": "order-123-ready",
  "source": "shop",
  "ref_id": "123"
}
```

### Поля

| Поле | Обязательно | Описание |
|------|-------------|----------|
| `phone` | да | Номер РФ: `79XXXXXXXXX`, `+79...`, `89...` — нормализуется к `79XXXXXXXXX` |
| `text` | да | Текст СМС, 1–1000 символов |
| `idempotency_key` | рекомендуется | Уникальный ключ операции (см. ниже) |
| `source` | нет | Имя источника (`shop`, `booking`). Если пусто — берётся из имени API-ключа |
| `ref_id` | нет | ID сущности на вашем сайте (заказ, запись) — для логов |

### Успешный ответ — 200 OK

```json
{
  "id": "e10ec282-d079-42d8-852c-5edd55c805c7",
  "status": "queued",
  "phone": "79991234567",
  "source": "shop",
  "ref_id": "123",
  "device_id": null,
  "created_at": "2026-07-05T12:09:30+00:00",
  "sent_at": null,
  "last_error": null
}
```

**Важно:** `200 OK` означает «принято в очередь», а не «доставлено на телефон абонента». Доставка асинхронная.

### Idempotency (защита от дублей)

Если передан `idempotency_key` и сообщение с таким ключом **уже есть**, API вернёт **существующую** запись (без новой СМС в очередь).

**Примеры ключей:**

| Сценарий | idempotency_key |
|----------|-----------------|
| Заказ готов | `order-{id}-ready` |
| Заказ принят | `order-{id}-created` |
| Напоминание о записи | `booking-{id}-reminder-24h` |

Один и тот же ключ при повторном cron/клике **не создаст вторую СМС** — это ожидаемое поведение.

Для **новой** СМС по тому же заказу — **другой** ключ (например `order-123-ready` и `order-123-reminder`).

### Ошибки

| Код | Причина |
|-----|---------|
| 401 | Нет или неверный `Authorization: Bearer` |
| 422 | Невалидный JSON, пустой `text`, неверный `phone` |

Пример 422:

```json
{
  "detail": [
    {
      "loc": ["body", "phone"],
      "msg": "Номер должен быть в формате 79XXXXXXXXX",
      "type": "value_error"
    }
  ]
}
```

---

## GET /api/v1/messages/{id} — статус сообщения

```http
GET /api/v1/messages/e10ec282-d079-42d8-852c-5edd55c805c7
Authorization: Bearer ВАШ_API_КЛЮЧ
```

### Ответ — 200 OK

```json
{
  "id": "e10ec282-d079-42d8-852c-5edd55c805c7",
  "status": "sent",
  "delivery_status": "delivered",
  "phone": "79991234567",
  "source": "shop",
  "ref_id": "123",
  "device_id": 1,
  "created_at": "2026-07-05T12:09:30+00:00",
  "sent_at": "2026-07-05T12:09:39+00:00",
  "delivered_at": "2026-07-05T12:09:45+00:00",
  "last_error": null,
  "delivery_error": null,
  "callback_at": "2026-07-05T12:09:45+00:00"
}
```

### Статусы `status` (очередь / API)

| status | Значение |
|--------|----------|
| `queued` | В очереди, ждёт отправки |
| `sending` | В процессе отправки |
| `sent` | Задача передана на телефон через SMS Gateway |
| `failed` | Не удалось отправить (API, нет телефона и т.д.) |

### Статусы `delivery_status` (телефон / оператор)

| delivery_status | Значение |
|-----------------|----------|
| `pending` | Ждём отчёт с телефона |
| `sent_to_carrier` | Телефон передал оператору (`sms:sent`) |
| `delivered` | Доставлено абоненту (`sms:delivered`) |
| `failed` | Сбой на телефоне или модеме (`sms:failed`) — смотрите `delivery_error` |

Ошибки: **404** — неверный `id`.

---

## Callback на ваш сайт (booking и др.)

В админке sms-delivery для клиента укажите **Callback URL**.  
При финальном исходе sms-delivery сделает **POST** на этот адрес (один раз на сообщение):

| event | Когда |
|-------|--------|
| `delivery.delivered` | Абонент получил СМС |
| `delivery.failed` | Сбой на телефоне, модеме или при отправке в Gateway |

Заголовок (если задан `CALLBACK_SECRET` на сервере):

```http
X-SMS-Delivery-Secret: ваш-секрет
Content-Type: application/json
```

Тело запроса:

```json
{
  "event": "delivery.failed",
  "message_id": "e10ec282-d079-42d8-852c-5edd55c805c7",
  "phone": "79991234567",
  "source": "booking",
  "ref_id": "4521",
  "idempotency_key": "booking-4521-confirmed",
  "status": "failed",
  "delivery_status": "failed",
  "last_error": null,
  "delivery_error": "RESULT_RIL_MODEM_ERR",
  "device_id": 2,
  "sent_at": "2026-07-06T08:00:00+00:00",
  "delivered_at": null
}
```

Ваш сайт должен ответить **HTTP 200**. Тогда booking может, например, отправить СМС через SMS-центр при `delivery.failed`.

---

## GET /health — проверка сервиса

Без авторизации.

```http
GET /health
```

```json
{
  "status": "ok",
  "queued": 2,
  "active_devices": 1,
  "incoming_unprocessed": 0
}
```

---

## Примеры интеграции

### PHP (cURL)

```php
<?php

function sms_enqueue(string $phone, string $text, string $idempotencyKey, string $refId = ''): array
{
    $url = 'https://sms-api.shinaufa.ru/api/v1/messages';
    $apiKey = getenv('SMS_DELIVERY_API_KEY'); // ключ выдаёт администратор

    $payload = json_encode([
        'phone' => $phone,
        'text' => $text,
        'idempotency_key' => $idempotencyKey,
        'source' => 'shop',
        'ref_id' => $refId,
    ], JSON_UNESCAPED_UNICODE);

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => [
            'Content-Type: application/json',
            'Authorization: Bearer ' . $apiKey,
        ],
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_TIMEOUT => 15,
    ]);

    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($code < 200 || $code >= 300) {
        throw new RuntimeException("SMS API HTTP $code: $body");
    }

    return json_decode($body, true, 512, JSON_THROW_ON_ERROR);
}

// Пример: заказ готов
sms_enqueue(
    phone: '79991234567',
    text: 'Заказ №123 готов к выдаче. ул. Примерная, 1',
    idempotencyKey: 'order-123-ready',
    refId: '123'
);
```

### PHP (Guzzle)

```php
use GuzzleHttp\Client;

$client = new Client([
    'base_uri' => 'https://sms-api.shinaufa.ru',
    'timeout' => 15,
]);

$response = $client->post('/api/v1/messages', [
    'headers' => [
        'Authorization' => 'Bearer ' . $_ENV['SMS_DELIVERY_API_KEY'],
        'Content-Type' => 'application/json',
    ],
    'json' => [
        'phone' => '79991234567',
        'text' => 'Заказ №123 готов к выдаче.',
        'idempotency_key' => 'order-123-ready',
        'source' => 'shop',
        'ref_id' => '123',
    ],
]);

$data = json_decode($response->getBody()->getContents(), true);
```

### Python (httpx)

```python
import httpx

SMS_URL = "https://sms-api.shinaufa.ru"
SMS_KEY = "ВАШ_API_КЛЮЧ"


def send_sms(phone: str, text: str, *, idempotency_key: str, ref_id: str = "") -> dict:
    r = httpx.post(
        f"{SMS_URL}/api/v1/messages",
        json={
            "phone": phone,
            "text": text,
            "idempotency_key": idempotency_key,
            "source": "booking",
            "ref_id": ref_id,
        },
        headers={"Authorization": f"Bearer {SMS_KEY}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()
```

### curl (отладка)

```bash
curl -X POST "https://sms-api.shinaufa.ru/api/v1/messages" \
  -H "Authorization: Bearer ВАШ_API_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -d '{
    "phone": "79991234567",
    "text": "Тест",
    "idempotency_key": "test-001",
    "source": "shop",
    "ref_id": "test"
  }'
```

---

## Рекомендации для разработчиков

### Когда вызывать API

Вызывайте **в момент события** на вашем сайте (статус заказа изменился, cron напоминания), а не пакетом.

### Не ждите доставки в HTTP-запросе

После `POST` достаточно сохранить `id` сообщения. Статус можно опросить через `GET /api/v1/messages/{id}` или не опрашивать (fire-and-forget), если достаточно факта постановки в очередь.

### Таймауты

- `POST` — timeout **15 сек** на стороне клиента.
- Доставка на телефон абонента — от секунд до нескольких минут (очередь + паузы).

### Согласие на СМС

На сайте должно быть согласие пользователя на сервисные СМС (заказ, запись, напоминание).

### Типовые сценарии (магазин)

| Событие | idempotency_key | Пример текста |
|---------|-----------------|---------------|
| Заказ оформлен | `order-{id}-created` | «Заказ №{id} принят» |
| Готов к выдаче | `order-{id}-ready` | «Заказ №{id} готов, адрес: ...» |
| Не забрали N дней | `order-{id}-pickup-reminder` | «Заказ №{id} ждёт вас до ...» |

---

## Что не входит в API (пока)

- Пакетная отправка (`/batch`) — не поддерживается.
- Входящие СМС — сохраняются в админке; API для заборки ответов планируется отдельно.
- Изменение/отмена уже поставленной в очередь СМС — не поддерживается.

---

## Контакты и доступ

1. **API-ключ** — админка → **API-клиенты** (`/admin/clients`): создать клиент `shop`, скопировать ключ разработчику.
2. **Проверка очереди** — `/admin/messages`.
3. **Проблемы с доставкой** — `status` и `last_error` у сообщения.

---

## OpenAPI

Интерактивная документация (если включена на сервере):

- `https://sms-api.shinaufa.ru/docs`
