"""Correo de Gestión: avisa al asignado si una etapa (entrega) o tarea cambia."""

from __future__ import annotations

import os
import smtplib
import ssl
import threading
from html import escape

from flask import current_app, has_request_context

from common import _nombre_display_trabajador, _usuario_sesion
from extensions import db
from models import EntregaProgramada, Proyecto, TareaEntrega, Trabajador

_EVENTOS = {
    'asignada': 'asignada',
    'modificada': 'modificada',
    'completada': 'completada',
}


def clasificar_evento(antes: dict | None, despues: dict) -> str | None:
    """'asignada', 'modificada', 'completada' o None si no hay que avisar."""
    if despues == antes:
        return None
    status_prev = antes.get('status') if antes else None
    if despues.get('status') == 'Hecho' and status_prev != 'Hecho':
        if despues.get('asignado_id') or (antes and antes.get('asignado_id')):
            return 'completada'
        return None
    if antes is None:
        return 'asignada' if despues.get('asignado_id') else None
    if despues.get('asignado_id') and despues.get('asignado_id') != antes.get('asignado_id'):
        return 'asignada'
    if not despues.get('asignado_id'):
        return None
    return 'modificada'


def destinatario_id(evento: str | None, antes: dict | None, despues: dict) -> int | None:
    if evento == 'completada':
        return despues.get('asignado_id') or (antes or {}).get('asignado_id')
    if evento in ('asignada', 'modificada'):
        return despues.get('asignado_id')
    return None


def snapshot_entrega(entrega: EntregaProgramada) -> dict:
    proyecto = Proyecto.query.get(entrega.proyecto_id) if entrega.proyecto_id else None
    fecha = entrega.fecha_entrega.strftime('%d/%m/%Y') if entrega.fecha_entrega else ''
    return {
        'asignado_id': entrega.asignado_id,
        'titulo': (entrega.descripcion or '').strip(),
        'fecha': fecha,
        'status': entrega.status or '',
        'proyecto': proyecto.nombre if proyecto else '',
        'etapa': '',
    }


def snapshot_tarea(tarea: TareaEntrega) -> dict:
    entrega = EntregaProgramada.query.get(tarea.entrega_id) if tarea.entrega_id else None
    proyecto = None
    if entrega and entrega.proyecto_id:
        proyecto = Proyecto.query.get(entrega.proyecto_id)
    fecha = tarea.fecha_limite.strftime('%d/%m/%Y') if tarea.fecha_limite else ''
    return {
        'asignado_id': tarea.asignado_id,
        'titulo': (tarea.descripcion or '').strip(),
        'fecha': fecha,
        'status': tarea.status or '',
        'proyecto': proyecto.nombre if proyecto else '',
        'etapa': (entrega.descripcion or '').strip() if entrega else '',
    }


def notificar_entrega(antes: dict | None, entrega: EntregaProgramada) -> None:
    _notificar('Etapa', 'la etapa', antes, snapshot_entrega(entrega))


def notificar_tarea(antes: dict | None, tarea: TareaEntrega) -> None:
    _notificar('Tarea', 'la tarea', antes, snapshot_tarea(tarea))


def _notificar(tipo: str, articulo: str, antes: dict | None, despues: dict) -> None:
    try:
        evento = clasificar_evento(antes, despues)
        tid = destinatario_id(evento, antes, despues)
        if not evento or not tid:
            return
        actor = _usuario_sesion() if has_request_context() else None
        if actor and actor.id == tid:
            return
        trabajador = db.session.get(Trabajador, tid)
        if not trabajador:
            return
        email = (trabajador.email or '').strip()
        if not email:
            current_app.logger.info(
                'Notificación de gestión omitida: %s sin email (trabajador %s)',
                tipo, tid,
            )
            return
        asunto, texto, html = _mensaje(
            tipo, articulo, evento, antes, despues, trabajador, actor,
        )
        _encolar(email, asunto, texto, html)
    except Exception:
        current_app.logger.exception('Error al preparar notificación de gestión (%s)', tipo)


def _mensaje(tipo, articulo, evento, antes, despues, trabajador, actor):
    nombre = _nombre_display_trabajador(trabajador)
    actor_nombre = _nombre_display_trabajador(actor) if actor else ''
    titulo = despues.get('titulo') or f'{tipo} sin descripción'
    proyecto = despues.get('proyecto') or 'Sin proyecto'
    verbo = _EVENTOS[evento]
    if actor_nombre:
        frases = {
            'asignada': f'{actor_nombre} te asignó {articulo}',
            'modificada': f'{actor_nombre} modificó {articulo}',
            'completada': f'{actor_nombre} completó {articulo}',
        }
    else:
        frases = {
            'asignada': f'Se te asignó {articulo}',
            'modificada': f'Se modificó {articulo}',
            'completada': f'Se completó {articulo}',
        }
    intro = f'{frases[evento]} «{titulo}» en el proyecto {proyecto}.'
    filas = [
        ('Proyecto', proyecto),
        (tipo, titulo),
    ]
    if tipo == 'Tarea' and despues.get('etapa'):
        filas.append(('Etapa', despues['etapa']))
    filas.append(('Fecha', despues.get('fecha') or '—'))
    filas.append(('Estado', despues.get('status') or '—'))
    cambios = _cambios(antes, despues) if evento == 'modificada' and antes else []
    asunto = f'Maestro WEB: {tipo.lower()} {verbo} — {titulo}'
    if len(asunto) > 140:
        asunto = asunto[:137] + '...'

    lineas = [f'Hola {nombre},', '', intro, '']
    for etiqueta, valor in filas:
        lineas.append(f'{etiqueta}: {valor}')
    if cambios:
        lineas.extend(['', 'Cambios:'])
        lineas.extend(f'- {c}' for c in cambios)
    lineas.extend(['', 'Estás asignado en Gestión de Maestro WEB.', ''])
    texto = '\n'.join(lineas)

    filas_html = ''.join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#5c6b66;">{escape(k)}</td>'
        f'<td style="padding:6px 0;">{escape(v)}</td></tr>'
        for k, v in filas
    )
    cambios_html = ''
    if cambios:
        items = ''.join(f'<li>{escape(c)}</li>' for c in cambios)
        cambios_html = (
            f'<p style="margin:16px 0 6px;font-weight:600;">Cambios</p><ul>{items}</ul>'
        )
    html = f'''<!DOCTYPE html>
<html><body style="margin:0;background:#f4f7f6;font-family:Segoe UI,Arial,sans-serif;color:#1c2b27;">
  <div style="max-width:560px;margin:24px auto;background:#fff;border:1px solid #d7e3df;border-radius:8px;overflow:hidden;">
    <div style="background:#0d6e6e;color:#fff;padding:16px 20px;font-size:15px;">Maestro WEB · Gestión</div>
    <div style="padding:20px;">
      <p style="margin:0 0 12px;">Hola {escape(nombre)},</p>
      <p style="margin:0 0 16px;">{escape(intro)}</p>
      <table style="border-collapse:collapse;font-size:14px;">{filas_html}</table>
      {cambios_html}
      <p style="margin:20px 0 0;color:#5c6b66;font-size:13px;">Estás asignado en Gestión de Maestro WEB.</p>
    </div>
  </div>
</body></html>'''
    return asunto, texto, html


def _cambios(antes: dict, despues: dict) -> list[str]:
    etiquetas = (
        ('titulo', 'Descripción'),
        ('fecha', 'Fecha'),
        ('status', 'Estado'),
        ('proyecto', 'Proyecto'),
        ('etapa', 'Etapa'),
    )
    lineas = []
    for key, label in etiquetas:
        prev = antes.get(key) or '—'
        nuevo = despues.get(key) or '—'
        if prev != nuevo and (antes.get(key) or despues.get(key)):
            lineas.append(f'{label}: {prev} → {nuevo}')
    return lineas


def _smtp_listo() -> bool:
    user = os.environ.get('SMTP_USER', '').strip()
    password = os.environ.get('SMTP_PASSWORD', '')
    if os.environ.get('SMTP_ENABLED', '1').strip().lower() in ('0', 'false', 'no'):
        return False
    return bool(user and password)


def _encolar(destinatario: str, asunto: str, texto: str, html: str) -> None:
    if not _smtp_listo():
        current_app.logger.warning('Notificación de gestión omitida: SMTP no configurado')
        return
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            ok = _enviar_smtp(destinatario, asunto, texto, html)
            if ok:
                current_app.logger.info('Notificación de gestión enviada a %s', destinatario)

    threading.Thread(target=_run, name='notif-gestion', daemon=True).start()


def _enviar_smtp(destinatario: str, asunto: str, texto: str, html: str) -> bool:
    from email.message import EmailMessage
    from email.utils import formataddr

    host = os.environ.get('SMTP_HOST', 'smtp.gmail.com').strip() or 'smtp.gmail.com'
    port = int(os.environ.get('SMTP_PORT', '587') or '587')
    user = os.environ.get('SMTP_USER', '').strip()
    password = os.environ.get('SMTP_PASSWORD', '')
    remitente = os.environ.get('SMTP_FROM', user).strip() or user
    usar_tls = os.environ.get('SMTP_USE_TLS', '1').strip().lower() not in ('0', 'false', 'no')

    msg = EmailMessage()
    msg['Subject'] = asunto
    msg['From'] = formataddr(('Notificaciones bgreen', remitente))
    msg['To'] = destinatario
    msg.set_content(texto)
    msg.add_alternative(html, subtype='html')
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                if usar_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception:
        current_app.logger.exception('No se pudo enviar la notificación de gestión a %s', destinatario)
        return False
