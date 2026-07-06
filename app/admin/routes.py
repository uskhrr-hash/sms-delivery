from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import ApiClient, Device, IncomingMessage, MessageStatus, OutboundMessage
from app.services.api_clients import create_client, mask_api_key, regenerate_key
from app.services.diagnostics import collect_checks, enqueue_test_sms

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
    device_error = request.session.pop('device_error', None)
    return templates.TemplateResponse(
        request,
        'dashboard.html',
        {'stats': stats, 'devices': devices, 'settings': get_settings(), 'device_error': device_error},
    )


@router.post('/devices', dependencies=[Depends(require_admin)])
def add_device(
    db: Session = Depends(get_db),
    name: str = Form(...),
    gateway_device_id: str = Form(...),
    gateway_username: str = Form(...),
    gateway_password: str = Form(...),
    phone_label: str = Form(''),
    sort_order: int = Form(0),
) -> RedirectResponse:
    db.add(
        Device(
            name=name.strip(),
            gateway_device_id=gateway_device_id.strip(),
            gateway_username=gateway_username.strip(),
            gateway_password=gateway_password.strip(),
            phone_label=phone_label.strip(),
            sort_order=sort_order,
        )
    )
    db.commit()
    return RedirectResponse('/admin/', status.HTTP_303_SEE_OTHER)


@router.post('/devices/{device_id}/update', dependencies=[Depends(require_admin)])
def update_device(
    request: Request,
    device_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    gateway_device_id: str = Form(...),
    gateway_username: str = Form(...),
    gateway_password: str = Form(''),
    phone_label: str = Form(''),
    sort_order: int = Form(0),
) -> RedirectResponse:
    device = db.get(Device, device_id)
    if not device:
        return RedirectResponse('/admin/', status.HTTP_303_SEE_OTHER)
    device.name = name.strip()
    device.gateway_device_id = gateway_device_id.strip()
    device.gateway_username = gateway_username.strip()
    if gateway_password.strip():
        device.gateway_password = gateway_password.strip()
    device.phone_label = phone_label.strip()
    device.sort_order = sort_order
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        request.session['device_error'] = (
            f'Не удалось сохранить «{name.strip()}»: имя или Gateway ID уже заняты'
        )
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


@router.get('/clients', response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def clients_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    clients = db.scalars(select(ApiClient).order_by(ApiClient.name)).all()
    flash_key = request.session.pop('flash_api_key', None)
    flash_name = request.session.pop('flash_client_name', None)
    error = request.session.pop('clients_error', None)
    return templates.TemplateResponse(
        request,
        'clients.html',
        {
            'clients': clients,
            'flash_key': flash_key,
            'flash_name': flash_name,
            'error': error,
            'mask_api_key': mask_api_key,
        },
    )


@router.post('/clients', dependencies=[Depends(require_admin)])
def add_client(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
) -> RedirectResponse:
    try:
        client = create_client(db, name)
        request.session['flash_api_key'] = client.api_key
        request.session['flash_client_name'] = client.name
    except ValueError as e:
        request.session['clients_error'] = str(e)
    return RedirectResponse('/admin/clients', status.HTTP_303_SEE_OTHER)


@router.post('/clients/{client_id}/regenerate', dependencies=[Depends(require_admin)])
def regenerate_client_key(
    request: Request,
    client_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    client = regenerate_key(db, client_id)
    if client:
        request.session['flash_api_key'] = client.api_key
        request.session['flash_client_name'] = client.name
    return RedirectResponse('/admin/clients', status.HTTP_303_SEE_OTHER)


@router.post('/clients/{client_id}/toggle', dependencies=[Depends(require_admin)])
def toggle_client(client_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    client = db.get(ApiClient, client_id)
    if client:
        client.enabled = not client.enabled
        db.commit()
    return RedirectResponse('/admin/clients', status.HTTP_303_SEE_OTHER)


@router.get('/test', response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def test_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    checks = collect_checks(db)
    flash_msg_id = request.session.pop('test_message_id', None)
    error = request.session.pop('test_error', None)
    return templates.TemplateResponse(
        request,
        'test.html',
        {
            'checks': checks,
            'flash_msg_id': flash_msg_id,
            'error': error,
            'settings': get_settings(),
        },
    )


@router.post('/test', dependencies=[Depends(require_admin)])
def test_send(
    request: Request,
    db: Session = Depends(get_db),
    phone: str = Form(...),
    text: str = Form(...),
) -> RedirectResponse:
    try:
        msg = enqueue_test_sms(db, phone, text)
        request.session['test_message_id'] = msg.id
    except ValueError as e:
        request.session['test_error'] = str(e)
    return RedirectResponse('/admin/test', status.HTTP_303_SEE_OTHER)
