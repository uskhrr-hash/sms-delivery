from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Device, IncomingMessage, MessageStatus, OutboundMessage

router = APIRouter(prefix='/admin', tags=['admin'])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / 'templates'))


def require_admin(request: Request) -> None:
    if not request.session.get('admin'):
        raise HTTPException(status_code=302, headers={'Location': '/admin/login'})


@router.get('/login', response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'login.html', {'error': None})


@router.post('/login')
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> RedirectResponse:
    settings = get_settings()
    if secrets.compare_digest(username, settings.admin_user) and secrets.compare_digest(
        password, settings.admin_password
    ):
        request.session['admin'] = True
        return RedirectResponse('/admin/', status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        'login.html',
        {'error': 'Неверный логин или пароль'},
        status_code=401,
    )


@router.get('/logout')
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse('/admin/login', status.HTTP_303_SEE_OTHER)


@router.get('/', response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    stats = {
        'queued': db.scalar(
            select(func.count()).select_from(OutboundMessage).where(
                OutboundMessage.status == MessageStatus.QUEUED
            )
        ),
        'sent_today': db.scalar(
            select(func.count()).select_from(OutboundMessage).where(
                OutboundMessage.status == MessageStatus.SENT
            )
        ),
        'failed': db.scalar(
            select(func.count()).select_from(OutboundMessage).where(
                OutboundMessage.status == MessageStatus.FAILED
            )
        ),
        'incoming_new': db.scalar(
            select(func.count()).select_from(IncomingMessage).where(IncomingMessage.processed.is_(False))
        ),
    }
    devices = db.scalars(select(Device).order_by(Device.sort_order, Device.id)).all()
    return templates.TemplateResponse(
        request,
        'dashboard.html',
        {'stats': stats, 'devices': devices, 'settings': get_settings()},
    )


@router.post('/devices', dependencies=[Depends(require_admin)])
def add_device(
    db: Session = Depends(get_db),
    name: str = Form(...),
    gateway_device_id: str = Form(...),
    phone_label: str = Form(''),
    sort_order: int = Form(0),
) -> RedirectResponse:
    db.add(
        Device(
            name=name.strip(),
            gateway_device_id=gateway_device_id.strip(),
            phone_label=phone_label.strip(),
            sort_order=sort_order,
        )
    )
    db.commit()
    return RedirectResponse('/admin/', status.HTTP_303_SEE_OTHER)


@router.post('/devices/{device_id}/toggle', dependencies=[Depends(require_admin)])
def toggle_device(device_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    device = db.get(Device, device_id)
    if device:
        device.enabled = not device.enabled
        db.commit()
    return RedirectResponse('/admin/', status.HTTP_303_SEE_OTHER)


@router.get('/messages', response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def messages_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(select(OutboundMessage).order_by(desc(OutboundMessage.created_at)).limit(200)).all()
    return templates.TemplateResponse(request, 'messages.html', {'messages': rows})


@router.get('/incoming', response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def incoming_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(select(IncomingMessage).order_by(desc(IncomingMessage.created_at)).limit(200)).all()
    return templates.TemplateResponse(request, 'incoming.html', {'messages': rows})
