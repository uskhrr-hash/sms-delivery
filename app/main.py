from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.admin.routes import router as admin_router
from app.api.health import router as health_router
from app.api.messages import router as messages_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.database import init_db
from app.database import SessionLocal
from app.services.api_clients import seed_clients_from_env
from app.services.gateway_webhooks import ensure_delivery_webhooks_registered
from app.worker import worker_loop

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        seed_clients_from_env(db)
        await ensure_delivery_webhooks_registered(db)
    finally:
        db.close()
    stop_event = asyncio.Event()
    task = asyncio.create_task(worker_loop(stop_event))
    yield
    stop_event.set()
    await task


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.admin_password + '-session')
    app.include_router(health_router)
    app.include_router(messages_router)
    app.include_router(webhooks_router)
    app.include_router(admin_router)
    return app


app = create_app()
