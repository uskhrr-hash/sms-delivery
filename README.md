# SMS Delivery

Отдельный сервис доставки СМС через Android-телефоны ([SMS Gateway for Android](https://docs.sms-gate.app/)).

Сайты (Python, позже PHP) **сами решают когда и кому слать** — этот сервис только **принимает в очередь и доставляет** с паузами «как человек», чередуя 2–3 телефона.

## Схема

```
[Python-сайт] ──POST /api/v1/messages──┐
                                      ├──► sms-delivery (VPS) ──► SMS Gateway Private Server
[PHP-сайт]    ──POST /api/v1/messages──┘              │                    │
                                                      │                    ▼
                                              очередь + пауза          [Телефон 1]
                                              round-robin              [Телефон 2]
                                                                       [Телефон 3]
```

## Возможности

- REST API для постановки **одной** СМС в очередь (без пакетной рассылки)
- Пауза **3–4 сек** между отправками (настраивается)
- **Статусы доставки** с телефона (`delivered` / `failed`) и **callback** на сайт (booking)
- **Round-robin** по устройствам
- **Idempotency key** — защита от дублей напоминаний
- **Входящие СМС** через webhook → БД
- **Мини-админка** `/admin/` — очередь, устройства, лог

## Быстрый старт (разработка)

```bash
cd sms-delivery
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

Админка: http://localhost:8000/admin/  
Health: http://localhost:8000/health

## API для сайтов

**Полная инструкция для разработчиков (PHP, Python и др.):** [docs/API.md](docs/API.md)

Кратко:

```http
POST /api/v1/messages
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "phone": "79991234567",
  "text": "Напоминаем: запись на шиномонтаж ...",
  "idempotency_key": "booking-4521-reminder-24h",
  "source": "booking",
  "ref_id": "4521"
}
```

```http
GET /api/v1/messages/{id}
Authorization: Bearer <api_key>
```

Ключи задаются в `.env`: `API_KEYS=booking:секрет,shop:секрет`

### Пример (Python)

```python
import httpx

def send_sms(phone: str, text: str, *, idempotency_key: str, ref_id: str):
    httpx.post(
        "https://sms-delivery.example.com/api/v1/messages",
        json={
            "phone": phone,
            "text": text,
            "idempotency_key": idempotency_key,
            "source": "booking",
            "ref_id": ref_id,
        },
        headers={"Authorization": "Bearer YOUR_KEY"},
        timeout=10,
    ).raise_for_status()
```

## Деплой на VPS (рядом с Python-сайтом)

### 1. SMS Gateway Private Server

На том же VPS поднимите [Private Server](https://docs.sms-gate.app/getting-started/private-server/) (Docker).

На каждом телефоне — приложение SMS Gateway, режим Private:
- API URL: `https://ваш-домен/api/mobile/v1`
- Private Token из конфига gateway

### 2. Этот сервис

```bash
cp .env.example .env
# заполнить SMSGATE_*, API_KEYS, ADMIN_*, PUBLIC_BASE_URL
docker compose up -d --build
```

Прокси (nginx/caddy): `https://sms-delivery.ваш-домен` → `localhost:8000`

### 3. Устройства в админке

На каждом телефоне в SMS Gateway (после подключения к Private Server) появятся **свои** Username и Password — скопируйте их.

В `/admin/` для каждого телефона добавьте:
- **Gateway device ID** (из приложения)
- **Логин и пароль Gateway** (сгенерированы приложением, у каждого телефона разные)

Уже добавленные устройства можно дополнить учёткой в таблице на главной странице админки.

### 4. Webhook входящих СМС

Зарегистрируйте в SMS Gateway (один раз):

```bash
curl -X POST -u USER:PASS \
  -H "Content-Type: application/json" \
  -d '{"url":"https://sms-delivery.ваш-домен/api/v1/webhooks/incoming","event":"sms:received"}' \
  https://ваш-домен/api/3rdparty/v1/webhooks
```

`USER:PASS` — логин/пароль **любого** подключённого телефона (из приложения SMS Gateway).

Входящие появятся в `/admin/incoming`. Для автоматической обработки сайтами позже можно добавить API «забрать необработанные».

## Настройки паузы

`.env`:

```
SEND_DELAY_MIN=60
SEND_DELAY_MAX=180
```

При ~200 СМС/день и паузе ~2 мин среднее — укладывается в сутки на 2–3 телефона.

## Что дальше

- [x] API-инструкция для внешних сайтов — [docs/API.md](docs/API.md)
- [ ] API: GET `/api/v1/incoming?processed=false` для заборки входящих ответов
- [ ] Алерты если очередь растёт или все телефоны offline
