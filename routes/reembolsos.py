"""API de gastos reembolsables."""

from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from werkzeug.utils import secure_filename

from common import *
from extensions import db
from models import Cuenta, Movimiento, Proyecto, Reembolso, Trabajador

bp = Blueprint('reembolsos', __name__)

STATUS_REEMBOLSO = ('Por reembolsar', 'Reembolsado')
NOMBRE_CUENTA_RENDICIONES = 'Rendiciones de gastos'
REEMBOLSOS_DIR = Path(__file__).resolve().parent.parent / 'uploads' / 'reembolsos'
REEMBOLSO_MAX_BYTES = 8 * 1024 * 1024
REEMBOLSO_ALLOWED_EXT = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.heic'}


def _asegurar_reembolsos_dir():
    REEMBOLSOS_DIR.mkdir(parents=True, exist_ok=True)


def _mime_adjunto(ext: str) -> str:
    return {
        '.pdf': 'application/pdf',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
        '.heic': 'image/heic',
    }.get(ext.lower(), 'application/octet-stream')


def _adjunto_abs(rel: str | None) -> Path | None:
    if not rel:
        return None
    path = REEMBOLSOS_DIR / rel
    return path if path.is_file() else None


def _eliminar_adjunto_disco(r: Reembolso) -> None:
    path = _adjunto_abs(r.adjunto_path)
    if path and path.is_file():
        path.unlink(missing_ok=True)
    r.adjunto_path = None
    r.adjunto_nombre = None


def _guardar_adjunto(r: Reembolso, archivo) -> str | None:
    """Guarda el respaldo; retorna mensaje de error o None."""
    if not archivo or not getattr(archivo, 'filename', None):
        return 'Archivo de respaldo requerido'
    original = secure_filename(archivo.filename) or 'respaldo'
    ext = Path(original).suffix.lower()
    if ext not in REEMBOLSO_ALLOWED_EXT:
        return f'Formato no permitido. Use: {", ".join(sorted(REEMBOLSO_ALLOWED_EXT))}'
    raw = archivo.read()
    if not raw:
        return 'Archivo vacío'
    if len(raw) > REEMBOLSO_MAX_BYTES:
        return f'El respaldo no puede superar {REEMBOLSO_MAX_BYTES // (1024 * 1024)} MB'

    _asegurar_reembolsos_dir()
    empresa_dir = REEMBOLSOS_DIR / str(r.empresa_id)
    empresa_dir.mkdir(parents=True, exist_ok=True)

    if r.adjunto_path:
        prev = _adjunto_abs(r.adjunto_path)
        if prev and prev.is_file():
            prev.unlink(missing_ok=True)

    rel = f'{r.empresa_id}/{r.id}{ext}'
    dest = REEMBOLSOS_DIR / rel
    dest.write_bytes(raw)
    r.adjunto_path = rel
    r.adjunto_nombre = original[:255]
    return None


def _cuenta_rendiciones(empresa_id: int) -> Cuenta:
    cuenta = Cuenta.query.filter_by(
        empresa_id=empresa_id, nombre=NOMBRE_CUENTA_RENDICIONES,
    ).first()
    if cuenta:
        return cuenta
    cuenta = Cuenta(
        empresa_id=empresa_id,
        nombre=NOMBRE_CUENTA_RENDICIONES,
        categoria='gasto',
        moneda='CLP',
    )
    db.session.add(cuenta)
    db.session.flush()
    return cuenta


def _descripcion_movimiento_reembolso(r: Reembolso) -> str:
    trab = r.trabajador
    nombre = _nombre_completo_trabajador(trab) if trab else f'Trabajador {r.trabajador_id}'
    detalle = (r.descripcion or '').strip()
    base = f'Reembolso #{r.id} — {nombre}'
    if detalle:
        base = f'{base} — {detalle}'
    return base[:255]


def _eliminar_movimiento_reembolso(r: Reembolso) -> None:
    """Elimina el movimiento contable vinculado y recalcula el proyecto si aplica."""
    mov_id = r.movimiento_id
    proyecto_id = r.proyecto_id
    empresa_id = r.empresa_id
    r.movimiento_id = None
    if not mov_id:
        return
    mov = Movimiento.query.filter_by(empresa_id=empresa_id, id=mov_id).first()
    if mov:
        db.session.delete(mov)
        db.session.flush()
    if proyecto_id:
        proy = Proyecto.query.filter_by(empresa_id=empresa_id, id=proyecto_id).first()
        if proy:
            movs = Movimiento.query.filter_by(empresa_id=empresa_id, proyecto_id=proyecto_id).all()
            recalcular_proyecto(proy, movs)


def _crear_movimiento_reembolso(r: Reembolso, fecha_pago: date) -> Movimiento:
    """Banco → Rendiciones de gastos; asocia a proyecto o Administración."""
    origen = _obtener_cuenta_banco_santander(r.empresa_id)
    destino = _cuenta_rendiciones(r.empresa_id)
    tipo = calcular_transaccion(origen.categoria, destino.categoria)
    if r.proyecto_id and r.proyecto:
        centro = (r.proyecto.nombre or 'Proyecto')[:50]
    else:
        centro = 'Administración'
    mov = Movimiento(
        empresa_id=r.empresa_id,
        fecha_movimiento=fecha_pago,
        monto_pesos=float(r.monto or 0),
        centro_costo=centro,
        estado='Activo',
        clase='gasto',
        cta_origen_id=origen.id,
        cta_destino_id=destino.id,
        transaccion=tipo,
        descripcion=_descripcion_movimiento_reembolso(r),
        proyecto_id=r.proyecto_id,
        status_pago=None,
    )
    db.session.add(mov)
    db.session.flush()
    r.movimiento_id = mov.id
    if r.proyecto_id:
        proy = Proyecto.query.filter_by(empresa_id=r.empresa_id, id=r.proyecto_id).first()
        if proy:
            movs = Movimiento.query.filter_by(
                empresa_id=r.empresa_id, proyecto_id=r.proyecto_id,
            ).all()
            recalcular_proyecto(proy, movs)
    return mov


def _reembolso_a_dict(r: Reembolso) -> dict:
    trab = r.trabajador
    proy = r.proyecto
    return {
        'id': r.id,
        'empresa_id': r.empresa_id,
        'trabajador_id': r.trabajador_id,
        'trabajador_nombre': _nombre_completo_trabajador(trab) if trab else '',
        'proyecto_id': r.proyecto_id,
        'proyecto_nombre': proy.nombre if proy else 'Administración',
        'es_admin': r.proyecto_id is None,
        'fecha_gasto': r.fecha_gasto.strftime('%Y-%m-%d') if r.fecha_gasto else None,
        'fecha_reembolso': (
            r.fecha_reembolso.strftime('%Y-%m-%d') if r.fecha_reembolso else None
        ),
        'monto': r.monto,
        'descripcion': r.descripcion or '',
        'status': r.status,
        'tiene_adjunto': bool(r.adjunto_path),
        'adjunto_nombre': r.adjunto_nombre or '',
        'adjunto_url': f'/api/reembolsos/{r.id}/adjunto' if r.adjunto_path else None,
        'created_at': r.created_at.isoformat() if r.created_at else None,
        'reembolsado_at': r.reembolsado_at.isoformat() if r.reembolsado_at else None,
        'reembolsado_por_id': r.reembolsado_por_id,
        'reembolsado_por_nombre': (
            _nombre_completo_trabajador(r.reembolsado_por) if r.reembolsado_por else ''
        ),
        'movimiento_id': r.movimiento_id,
    }


def _obtener_reembolso(eid: int, rid: int) -> Reembolso | None:
    return Reembolso.query.filter_by(empresa_id=eid, id=rid).first()


def _puede_ver(r: Reembolso, usuario: Trabajador) -> bool:
    return _es_admin() or r.trabajador_id == usuario.id


def _puede_editar_campos(r: Reembolso, usuario: Trabajador) -> bool:
    if r.status == 'Reembolsado' and not _es_admin():
        return False
    return _es_admin() or r.trabajador_id == usuario.id


def _parse_proyecto_id(eid: int, raw):
    """Retorna (proyecto_id|None, error). None = Administración."""
    if raw in (None, '', '__admin__', 'admin', 'Administración'):
        return None, None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        return None, 'proyecto_id inválido'
    proy = Proyecto.query.filter_by(empresa_id=eid, id=pid).first()
    if not proy:
        return None, 'Proyecto no encontrado'
    return proy.id, None


def _parse_status(raw, default='Por reembolsar'):
    if raw is None or raw == '':
        return default
    s = str(raw).strip()
    if s not in STATUS_REEMBOLSO:
        return None
    return s


def _aplicar_status(
    r: Reembolso,
    nuevo: str,
    usuario: Trabajador,
    fecha_reembolso: date | None = None,
) -> str | None:
    """Cambia status; al reembolsar crea movimiento contable. Retorna error o None."""
    if nuevo not in STATUS_REEMBOLSO:
        return 'Status inválido'
    if nuevo == r.status:
        if nuevo == 'Reembolsado' and fecha_reembolso and _es_admin():
            r.fecha_reembolso = fecha_reembolso
            if r.movimiento_id:
                mov = Movimiento.query.filter_by(
                    empresa_id=r.empresa_id, id=r.movimiento_id,
                ).first()
                if mov:
                    mov.fecha_movimiento = fecha_reembolso
                    mov.descripcion = _descripcion_movimiento_reembolso(r)
                    mov.monto_pesos = float(r.monto or 0)
            else:
                try:
                    _crear_movimiento_reembolso(r, fecha_reembolso)
                except ValueError as exc:
                    return str(exc)
        return None
    if nuevo == 'Reembolsado' and not _es_admin():
        return 'Solo un administrador puede marcar como reembolsado'
    if nuevo == 'Por reembolsar' and not _es_admin():
        return 'Solo un administrador puede revertir el status'

    if nuevo == 'Reembolsado':
        fecha_pago = fecha_reembolso or date.today()
        r.status = nuevo
        r.fecha_reembolso = fecha_pago
        r.reembolsado_at = datetime.utcnow()
        r.reembolsado_por_id = usuario.id
        if not r.movimiento_id:
            try:
                _crear_movimiento_reembolso(r, fecha_pago)
            except ValueError as exc:
                return str(exc)
        return None

    _eliminar_movimiento_reembolso(r)
    r.status = nuevo
    r.fecha_reembolso = None
    r.reembolsado_at = None
    r.reembolsado_por_id = None
    return None


def _fecha_reembolso_desde_data(data: dict) -> date | None:
    raw = data.get('fecha_reembolso')
    if not raw:
        return None
    try:
        return _parse_fecha(raw)
    except (TypeError, ValueError):
        return None


@bp.route('/api/reembolsos', methods=['GET'])
def listar_reembolsos():
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401

    q = Reembolso.query.filter_by(empresa_id=eid)
    if not _es_admin():
        q = q.filter_by(trabajador_id=usuario.id)
    else:
        trab_f = request.args.get('trabajador_id')
        if trab_f:
            try:
                q = q.filter_by(trabajador_id=int(trab_f))
            except (TypeError, ValueError):
                return jsonify({'error': 'trabajador_id inválido'}), 400

    status = (request.args.get('status') or '').strip()
    if status:
        if status not in STATUS_REEMBOLSO:
            return jsonify({'error': f'Status inválido. Use: {", ".join(STATUS_REEMBOLSO)}'}), 400
        q = q.filter_by(status=status)

    proy_raw = request.args.get('proyecto_id')
    if proy_raw is not None and proy_raw != '':
        if proy_raw in ('__admin__', 'admin'):
            q = q.filter(Reembolso.proyecto_id.is_(None))
        else:
            try:
                q = q.filter_by(proyecto_id=int(proy_raw))
            except (TypeError, ValueError):
                return jsonify({'error': 'proyecto_id inválido'}), 400

    desde = None
    hasta = None
    try:
        if request.args.get('desde'):
            desde = _parse_fecha(request.args.get('desde'))
        if request.args.get('hasta'):
            hasta = _parse_fecha(request.args.get('hasta'))
    except (TypeError, ValueError):
        return jsonify({'error': 'desde/hasta inválido (YYYY-MM-DD)'}), 400
    if desde:
        q = q.filter(Reembolso.fecha_gasto >= desde)
    if hasta:
        q = q.filter(Reembolso.fecha_gasto <= hasta)

    filas = q.order_by(Reembolso.fecha_gasto.desc(), Reembolso.id.desc()).all()
    total = sum(float(r.monto or 0) for r in filas)
    por_reembolsar = sum(
        float(r.monto or 0) for r in filas if r.status == 'Por reembolsar'
    )
    return jsonify({
        'items': [_reembolso_a_dict(r) for r in filas],
        'resumen': {
            'cantidad': len(filas),
            'total': total,
            'por_reembolsar': por_reembolsar,
        },
    })


@bp.route('/api/reembolsos', methods=['POST'])
def crear_reembolso():
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401

    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    fecha = _parse_fecha(data.get('fecha_gasto'))
    if not fecha:
        return jsonify({'error': 'fecha_gasto requerida (YYYY-MM-DD)'}), 400
    try:
        monto = float(data.get('monto'))
    except (TypeError, ValueError):
        return jsonify({'error': 'monto inválido'}), 400
    if monto <= 0:
        return jsonify({'error': 'El monto debe ser mayor a 0'}), 400

    proyecto_id, perr = _parse_proyecto_id(eid, data.get('proyecto_id'))
    if perr:
        return jsonify({'error': perr}), 400

    status = _parse_status(data.get('status'), 'Por reembolsar')
    if status is None:
        return jsonify({'error': f'Status inválido. Use: {", ".join(STATUS_REEMBOLSO)}'}), 400
    if status == 'Reembolsado' and not _es_admin():
        return jsonify({'error': 'Solo un administrador puede crear como reembolsado'}), 403

    trabajador_id = usuario.id
    if _es_admin() and data.get('trabajador_id'):
        try:
            tid = int(data['trabajador_id'])
        except (TypeError, ValueError):
            return jsonify({'error': 'trabajador_id inválido'}), 400
        if not Trabajador.query.filter_by(empresa_id=eid, id=tid).first():
            return jsonify({'error': 'trabajador_id no encontrado'}), 400
        trabajador_id = tid

    r = Reembolso(
        empresa_id=eid,
        trabajador_id=trabajador_id,
        proyecto_id=proyecto_id,
        fecha_gasto=fecha,
        monto=monto,
        descripcion=(data.get('descripcion') or '').strip()[:500] or None,
        status='Por reembolsar',
        created_at=datetime.utcnow(),
    )
    db.session.add(r)
    db.session.flush()

    archivo = request.files.get('adjunto') or request.files.get('respaldo')
    if archivo and archivo.filename:
        aerr = _guardar_adjunto(r, archivo)
        if aerr:
            db.session.rollback()
            return jsonify({'error': aerr}), 400

    if status == 'Reembolsado':
        fecha_pago = _fecha_reembolso_desde_data(data) or date.today()
        err_st = _aplicar_status(r, 'Reembolsado', usuario, fecha_pago)
        if err_st:
            db.session.rollback()
            return jsonify({'error': err_st}), 400

    db.session.commit()
    return jsonify(_reembolso_a_dict(r)), 201


@bp.route('/api/reembolsos/<int:rid>', methods=['GET'])
def obtener_reembolso(rid):
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401
    r = _obtener_reembolso(eid, rid)
    if not r:
        return jsonify({'error': 'Reembolso no encontrado'}), 404
    if not _puede_ver(r, usuario):
        return jsonify({'error': 'Sin permiso'}), 403
    return jsonify(_reembolso_a_dict(r))


@bp.route('/api/reembolsos/<int:rid>', methods=['PUT', 'PATCH'])
def actualizar_reembolso(rid):
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401
    r = _obtener_reembolso(eid, rid)
    if not r:
        return jsonify({'error': 'Reembolso no encontrado'}), 404
    if not _puede_editar_campos(r, usuario):
        return jsonify({'error': 'Sin permiso para editar este reembolso'}), 403

    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    fecha_pago = _fecha_reembolso_desde_data(data)

    keys = set(data.keys())
    if keys <= {'status', 'fecha_reembolso'} and 'status' in data:
        nuevo = _parse_status(data.get('status'), None)
        if nuevo is None:
            return jsonify({'error': f'Status inválido. Use: {", ".join(STATUS_REEMBOLSO)}'}), 400
        if nuevo == 'Reembolsado' and data.get('fecha_reembolso') and fecha_pago is None:
            return jsonify({'error': 'fecha_reembolso inválida (YYYY-MM-DD)'}), 400
        serr = _aplicar_status(r, nuevo, usuario, fecha_pago)
        if serr:
            return jsonify({'error': serr}), 403 if 'administrador' in serr.lower() else 400
        db.session.commit()
        return jsonify(_reembolso_a_dict(r))

    if r.status == 'Reembolsado' and not _es_admin():
        return jsonify({'error': 'No se puede editar un reembolso ya pagado'}), 403

    if 'fecha_gasto' in data:
        fecha = _parse_fecha(data.get('fecha_gasto'))
        if not fecha:
            return jsonify({'error': 'fecha_gasto inválida'}), 400
        r.fecha_gasto = fecha
    if 'monto' in data:
        try:
            monto = float(data.get('monto'))
        except (TypeError, ValueError):
            return jsonify({'error': 'monto inválido'}), 400
        if monto <= 0:
            return jsonify({'error': 'El monto debe ser mayor a 0'}), 400
        r.monto = monto
    if 'descripcion' in data:
        r.descripcion = (data.get('descripcion') or '').strip()[:500] or None
    if 'proyecto_id' in data:
        proyecto_id, perr = _parse_proyecto_id(eid, data.get('proyecto_id'))
        if perr:
            return jsonify({'error': perr}), 400
        r.proyecto_id = proyecto_id
    if 'status' in data:
        nuevo = _parse_status(data.get('status'), None)
        if nuevo is None:
            return jsonify({'error': f'Status inválido. Use: {", ".join(STATUS_REEMBOLSO)}'}), 400
        if nuevo == 'Reembolsado' and data.get('fecha_reembolso') and fecha_pago is None:
            return jsonify({'error': 'fecha_reembolso inválida (YYYY-MM-DD)'}), 400
        serr = _aplicar_status(r, nuevo, usuario, fecha_pago)
        if serr:
            return jsonify({'error': serr}), 403 if 'administrador' in serr.lower() else 400
    elif 'fecha_reembolso' in data and r.status == 'Reembolsado' and _es_admin():
        if data.get('fecha_reembolso') and fecha_pago is None:
            return jsonify({'error': 'fecha_reembolso inválida (YYYY-MM-DD)'}), 400
        if fecha_pago:
            r.fecha_reembolso = fecha_pago
            if r.movimiento_id:
                mov = Movimiento.query.filter_by(
                    empresa_id=r.empresa_id, id=r.movimiento_id,
                ).first()
                if mov:
                    mov.fecha_movimiento = fecha_pago

    archivo = request.files.get('adjunto') or request.files.get('respaldo')
    if archivo and archivo.filename:
        aerr = _guardar_adjunto(r, archivo)
        if aerr:
            return jsonify({'error': aerr}), 400

    if r.status == 'Reembolsado' and r.movimiento_id and _es_admin():
        mov = Movimiento.query.filter_by(empresa_id=r.empresa_id, id=r.movimiento_id).first()
        if mov:
            mov.monto_pesos = float(r.monto or 0)
            mov.descripcion = _descripcion_movimiento_reembolso(r)
            mov.proyecto_id = r.proyecto_id
            if r.proyecto_id and r.proyecto:
                mov.centro_costo = (r.proyecto.nombre or 'Proyecto')[:50]
            else:
                mov.centro_costo = 'Administración'
            if r.fecha_reembolso:
                mov.fecha_movimiento = r.fecha_reembolso

    db.session.commit()
    return jsonify(_reembolso_a_dict(r))


@bp.route('/api/reembolsos/<int:rid>', methods=['DELETE'])
def eliminar_reembolso(rid):
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401
    r = _obtener_reembolso(eid, rid)
    if not r:
        return jsonify({'error': 'Reembolso no encontrado'}), 404
    if not (_es_admin() or r.trabajador_id == usuario.id):
        return jsonify({'error': 'Sin permiso'}), 403
    if r.status == 'Reembolsado' and not _es_admin():
        return jsonify({'error': 'No se puede eliminar un reembolso ya pagado'}), 403
    _eliminar_movimiento_reembolso(r)
    _eliminar_adjunto_disco(r)
    db.session.delete(r)
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/api/reembolsos/<int:rid>/adjunto', methods=['GET'])
def descargar_adjunto(rid):
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401
    r = _obtener_reembolso(eid, rid)
    if not r:
        return jsonify({'error': 'Reembolso no encontrado'}), 404
    if not _puede_ver(r, usuario):
        return jsonify({'error': 'Sin permiso'}), 403
    path = _adjunto_abs(r.adjunto_path)
    if not path:
        return jsonify({'error': 'Sin respaldo'}), 404
    ext = path.suffix.lower()
    return send_file(
        path,
        mimetype=_mime_adjunto(ext),
        as_attachment=False,
        download_name=r.adjunto_nombre or path.name,
    )


@bp.route('/api/reembolsos/<int:rid>/adjunto', methods=['POST'])
def subir_adjunto(rid):
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401
    r = _obtener_reembolso(eid, rid)
    if not r:
        return jsonify({'error': 'Reembolso no encontrado'}), 404
    if not _puede_editar_campos(r, usuario):
        return jsonify({'error': 'Sin permiso'}), 403
    if r.status == 'Reembolsado' and not _es_admin():
        return jsonify({'error': 'No se puede cambiar el respaldo de un reembolso pagado'}), 403
    archivo = request.files.get('adjunto') or request.files.get('respaldo')
    aerr = _guardar_adjunto(r, archivo)
    if aerr:
        return jsonify({'error': aerr}), 400
    db.session.commit()
    return jsonify(_reembolso_a_dict(r))


@bp.route('/api/reembolsos/<int:rid>/adjunto', methods=['DELETE'])
def borrar_adjunto(rid):
    eid, err = _requiere_empresa()
    if err:
        return err
    usuario = _usuario_sesion()
    if not usuario:
        return jsonify({'error': 'No autenticado'}), 401
    r = _obtener_reembolso(eid, rid)
    if not r:
        return jsonify({'error': 'Reembolso no encontrado'}), 404
    if not _puede_editar_campos(r, usuario):
        return jsonify({'error': 'Sin permiso'}), 403
    if r.status == 'Reembolsado' and not _es_admin():
        return jsonify({'error': 'No se puede eliminar el respaldo de un reembolso pagado'}), 403
    _eliminar_adjunto_disco(r)
    db.session.commit()
    return jsonify(_reembolso_a_dict(r))
