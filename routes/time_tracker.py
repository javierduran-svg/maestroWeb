from datetime import datetime

from flask import Blueprint, jsonify, request

from common import *
from extensions import db
from models import (
    EntregaProgramada, Proyecto, RegistroTiempo, TareaEntrega, Trabajador,
)

bp = Blueprint('time_tracker', __name__)

ESTADOS_REGISTRO_TIEMPO = ('activo', 'pausado', 'finalizado')


def _duracion_efectiva(reg: RegistroTiempo, ahora: datetime | None = None) -> int:
    base = int(reg.duracion_segundos or 0)
    if reg.estado == 'activo' and reg.ultimo_inicio:
        ref = ahora or datetime.utcnow()
        base += max(0, int((ref - reg.ultimo_inicio).total_seconds()))
    return base


def _registro_tiempo_a_dict(reg: RegistroTiempo, ahora: datetime | None = None) -> dict:
    trab = reg.trabajador
    proy = reg.proyecto
    ent = reg.entrega
    tar = reg.tarea
    return {
        'id': reg.id,
        'empresa_id': reg.empresa_id,
        'trabajador_id': reg.trabajador_id,
        'trabajador_nombre': _nombre_completo_trabajador(trab) if trab else '',
        'proyecto_id': reg.proyecto_id,
        'proyecto_nombre': proy.nombre if proy else '',
        'entrega_id': reg.entrega_id,
        'entrega_descripcion': ent.descripcion if ent else '',
        'tarea_id': reg.tarea_id,
        'tarea_descripcion': tar.descripcion if tar else '',
        'inicio': reg.inicio.isoformat() if reg.inicio else None,
        'fin': reg.fin.isoformat() if reg.fin else None,
        'duracion_segundos': _duracion_efectiva(reg, ahora),
        'estado': reg.estado,
        'notas': reg.notas or '',
    }


def _registro_activo_query(eid: int, trabajador_id: int):
    return RegistroTiempo.query.filter(
        RegistroTiempo.empresa_id == eid,
        RegistroTiempo.trabajador_id == trabajador_id,
        RegistroTiempo.estado.in_(('activo', 'pausado')),
    )


def _validar_refs_tiempo(eid: int, proyecto_id, entrega_id, tarea_id):
    if not proyecto_id:
        return None, 'proyecto_id requerido'
    proyecto = Proyecto.query.filter_by(empresa_id=eid, id=int(proyecto_id)).first()
    if not proyecto:
        return None, 'proyecto_id inválido'
    if proyecto.status != 'Activo':
        return None, 'Solo se puede registrar tiempo en proyectos activos'

    entrega = None
    if entrega_id:
        entrega = EntregaProgramada.query.filter_by(
            empresa_id=eid, id=int(entrega_id), proyecto_id=proyecto.id,
        ).first()
        if not entrega:
            return None, 'entrega_id inválida para el proyecto'

    if tarea_id:
        if not entrega_id:
            return None, 'entrega_id requerida cuando se indica tarea_id'
        tarea = TareaEntrega.query.filter_by(
            empresa_id=eid, id=int(tarea_id), entrega_id=entrega.id,
        ).first()
        if not tarea:
            return None, 'tarea_id inválida para la entrega'
    elif entrega_id:
        tarea_id = None

    return {
        'proyecto_id': proyecto.id,
        'entrega_id': int(entrega_id) if entrega_id else None,
        'tarea_id': int(tarea_id) if tarea_id else None,
    }, None


def _finalizar_pausa(reg: RegistroTiempo, ahora: datetime):
    if reg.estado == 'activo' and reg.ultimo_inicio:
        reg.duracion_segundos = _duracion_efectiva(reg, ahora)
        reg.ultimo_inicio = None


def _parse_datetime(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        s = str(val).strip()
        if s.endswith('Z'):
            s = s[:-1]
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _obtener_registro_tiempo(eid: int, reg_id: int):
    return RegistroTiempo.query.filter_by(empresa_id=eid, id=reg_id).first()


def _validar_trabajador_tiempo(eid: int, trabajador_id):
    if not trabajador_id:
        return None, 'trabajador_id requerido'
    trab = Trabajador.query.filter_by(empresa_id=eid, id=int(trabajador_id)).first()
    if not trab:
        return None, 'trabajador_id inválido'
    return trab, None


def _aplicar_datos_registro_tiempo(reg: RegistroTiempo, data: dict, eid: int, ahora: datetime | None = None):
    """Aplica campos editables a un registro; retorna error str o None."""
    ahora = ahora or datetime.utcnow()

    if 'trabajador_id' in data:
        trab, err = _validar_trabajador_tiempo(eid, data['trabajador_id'])
        if err:
            return err
        reg.trabajador_id = trab.id

    refs_keys = ('proyecto_id', 'entrega_id', 'tarea_id')
    if any(k in data for k in refs_keys):
        proyecto_id = data.get('proyecto_id', reg.proyecto_id)
        entrega_id = data.get('entrega_id', reg.entrega_id)
        tarea_id = data.get('tarea_id', reg.tarea_id)
        if 'entrega_id' in data and data['entrega_id'] in (None, '', 0):
            entrega_id = None
            tarea_id = None
        if 'tarea_id' in data and data['tarea_id'] in (None, '', 0):
            tarea_id = None
        refs, val_err = _validar_refs_tiempo(eid, proyecto_id, entrega_id, tarea_id)
        if val_err:
            return val_err
        reg.proyecto_id = refs['proyecto_id']
        reg.entrega_id = refs['entrega_id']
        reg.tarea_id = refs['tarea_id']

    if 'inicio' in data:
        inicio = _parse_datetime(data['inicio'])
        if not inicio:
            return 'inicio inválido'
        reg.inicio = inicio

    if 'fin' in data:
        fin = _parse_datetime(data['fin']) if data['fin'] else None
        if data['fin'] and not fin:
            return 'fin inválido'
        reg.fin = fin

    if 'duracion_segundos' in data:
        try:
            reg.duracion_segundos = max(0, int(data['duracion_segundos']))
        except (TypeError, ValueError):
            return 'duracion_segundos inválido'
        if reg.estado == 'activo':
            reg.ultimo_inicio = ahora

    if 'estado' in data:
        nuevo_estado = data['estado']
        if nuevo_estado not in ESTADOS_REGISTRO_TIEMPO:
            return f'estado debe ser uno de: {", ".join(ESTADOS_REGISTRO_TIEMPO)}'
        if reg.estado == 'activo' and nuevo_estado != 'activo':
            _finalizar_pausa(reg, ahora)
        if nuevo_estado == 'activo' and reg.estado != 'activo':
            reg.ultimo_inicio = ahora
        if nuevo_estado == 'finalizado' and not reg.fin:
            reg.fin = ahora
        reg.estado = nuevo_estado

    if 'notas' in data:
        reg.notas = (data.get('notas') or '')[:2000] or None

    if reg.fin and reg.inicio and reg.fin < reg.inicio:
        return 'fin no puede ser anterior a inicio'

    return None


@bp.route('/api/time-tracker/activo', methods=['GET'])
def obtener_registro_activo():
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = _usuario_sesion()
    if not trabajador:
        return jsonify({'error': 'No autenticado'}), 401

    reg = _registro_activo_query(eid, trabajador.id).order_by(RegistroTiempo.id.desc()).first()
    if not reg:
        return jsonify(None)
    return jsonify(_registro_tiempo_a_dict(reg))


@bp.route('/api/time-tracker/iniciar', methods=['POST'])
def iniciar_registro_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = _usuario_sesion()
    if not trabajador:
        return jsonify({'error': 'No autenticado'}), 401

    if _registro_activo_query(eid, trabajador.id).first():
        return jsonify({'error': 'Ya tiene un registro de tiempo activo o en pausa'}), 409

    data = request.json or {}
    refs, val_err = _validar_refs_tiempo(
        eid, data.get('proyecto_id'), data.get('entrega_id'), data.get('tarea_id'),
    )
    if val_err:
        return jsonify({'error': val_err}), 400

    ahora = datetime.utcnow()
    reg = RegistroTiempo(
        empresa_id=eid,
        trabajador_id=trabajador.id,
        proyecto_id=refs['proyecto_id'],
        entrega_id=refs['entrega_id'],
        tarea_id=refs['tarea_id'],
        inicio=ahora,
        ultimo_inicio=ahora,
        duracion_segundos=0,
        estado='activo',
        notas=(data.get('notas') or '')[:2000] or None,
    )
    db.session.add(reg)
    db.session.commit()
    return jsonify({'mensaje': 'Temporizador iniciado', 'registro': _registro_tiempo_a_dict(reg)}), 201


@bp.route('/api/time-tracker/pausar', methods=['POST'])
def pausar_registro_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = _usuario_sesion()
    if not trabajador:
        return jsonify({'error': 'No autenticado'}), 401

    reg = _registro_activo_query(eid, trabajador.id).filter_by(estado='activo').first()
    if not reg:
        return jsonify({'error': 'No hay temporizador activo'}), 404

    ahora = datetime.utcnow()
    _finalizar_pausa(reg, ahora)
    reg.estado = 'pausado'
    db.session.commit()
    return jsonify({'mensaje': 'Temporizador pausado', 'registro': _registro_tiempo_a_dict(reg)})


@bp.route('/api/time-tracker/reanudar', methods=['POST'])
def reanudar_registro_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = _usuario_sesion()
    if not trabajador:
        return jsonify({'error': 'No autenticado'}), 401

    reg = _registro_activo_query(eid, trabajador.id).filter_by(estado='pausado').first()
    if not reg:
        return jsonify({'error': 'No hay temporizador en pausa'}), 404

    ahora = datetime.utcnow()
    reg.ultimo_inicio = ahora
    reg.estado = 'activo'
    db.session.commit()
    return jsonify({'mensaje': 'Temporizador reanudado', 'registro': _registro_tiempo_a_dict(reg)})


@bp.route('/api/time-tracker/detener', methods=['POST'])
def detener_registro_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = _usuario_sesion()
    if not trabajador:
        return jsonify({'error': 'No autenticado'}), 401

    reg = _registro_activo_query(eid, trabajador.id).first()
    if not reg:
        return jsonify({'error': 'No hay temporizador activo o en pausa'}), 404

    ahora = datetime.utcnow()
    if reg.estado == 'activo':
        _finalizar_pausa(reg, ahora)
    reg.fin = ahora
    reg.estado = 'finalizado'
    db.session.commit()
    return jsonify({'mensaje': 'Registro finalizado', 'registro': _registro_tiempo_a_dict(reg)})


@bp.route('/api/time-tracker/registros', methods=['GET'])
def listar_registros_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err

    query = RegistroTiempo.query.filter_by(empresa_id=eid)

    proyecto_id = request.args.get('proyecto_id')
    if proyecto_id:
        query = query.filter_by(proyecto_id=int(proyecto_id))

    trabajador_id = request.args.get('trabajador_id')
    if trabajador_id:
        query = query.filter_by(trabajador_id=int(trabajador_id))

    desde = _parse_fecha(request.args.get('desde'))
    if desde:
        query = query.filter(RegistroTiempo.inicio >= datetime.combine(desde, datetime.min.time()))

    hasta = _parse_fecha(request.args.get('hasta'))
    if hasta:
        query = query.filter(RegistroTiempo.inicio <= datetime.combine(hasta, datetime.max.time()))

    estado = request.args.get('estado')
    if estado:
        if estado not in ESTADOS_REGISTRO_TIEMPO:
            return jsonify({'error': f'estado debe ser uno de: {", ".join(ESTADOS_REGISTRO_TIEMPO)}'}), 400
        query = query.filter_by(estado=estado)

    registros = query.order_by(RegistroTiempo.inicio.desc(), RegistroTiempo.id.desc()).limit(500).all()
    ahora = datetime.utcnow()
    return jsonify([_registro_tiempo_a_dict(r, ahora) for r in registros])


@bp.route('/api/time-tracker/registros', methods=['POST'])
def crear_registro_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err

    data = request.json or {}
    trab, val_err = _validar_trabajador_tiempo(eid, data.get('trabajador_id'))
    if val_err:
        return jsonify({'error': val_err}), 400

    refs, val_err = _validar_refs_tiempo(
        eid, data.get('proyecto_id'), data.get('entrega_id'), data.get('tarea_id'),
    )
    if val_err:
        return jsonify({'error': val_err}), 400

    inicio = _parse_datetime(data.get('inicio'))
    if not inicio:
        return jsonify({'error': 'inicio requerido'}), 400

    fin = _parse_datetime(data.get('fin')) if data.get('fin') else None
    if data.get('fin') and not fin:
        return jsonify({'error': 'fin inválido'}), 400

    estado = data.get('estado') or 'finalizado'
    if estado not in ESTADOS_REGISTRO_TIEMPO:
        return jsonify({'error': f'estado debe ser uno de: {", ".join(ESTADOS_REGISTRO_TIEMPO)}'}), 400

    duracion = 0
    if 'duracion_segundos' in data and data['duracion_segundos'] is not None:
        try:
            duracion = max(0, int(data['duracion_segundos']))
        except (TypeError, ValueError):
            return jsonify({'error': 'duracion_segundos inválido'}), 400
    elif fin:
        duracion = max(0, int((fin - inicio).total_seconds()))
    elif data.get('duracion_segundos') is not None:
        duracion = max(0, int(data['duracion_segundos']))

    if fin and fin < inicio:
        return jsonify({'error': 'fin no puede ser anterior a inicio'}), 400

    if estado in ('activo', 'pausado') and _registro_activo_query(eid, trab.id).first():
        return jsonify({'error': 'El trabajador ya tiene un registro activo o en pausa'}), 409

    ahora = datetime.utcnow()
    reg = RegistroTiempo(
        empresa_id=eid,
        trabajador_id=trab.id,
        proyecto_id=refs['proyecto_id'],
        entrega_id=refs['entrega_id'],
        tarea_id=refs['tarea_id'],
        inicio=inicio,
        fin=fin,
        duracion_segundos=duracion,
        estado=estado,
        ultimo_inicio=ahora if estado == 'activo' else None,
        notas=(data.get('notas') or '')[:2000] or None,
    )
    db.session.add(reg)
    db.session.commit()
    return jsonify({'mensaje': 'Registro creado', 'registro': _registro_tiempo_a_dict(reg)}), 201


@bp.route('/api/time-tracker/registros/<int:reg_id>', methods=['PUT'])
def actualizar_registro_tiempo(reg_id):
    eid, err = _requiere_empresa()
    if err:
        return err

    reg = _obtener_registro_tiempo(eid, reg_id)
    if not reg:
        return jsonify({'error': 'Registro no encontrado'}), 404

    data = request.json or {}
    ahora = datetime.utcnow()
    val_err = _aplicar_datos_registro_tiempo(reg, data, eid, ahora)
    if val_err:
        return jsonify({'error': val_err}), 400

    db.session.commit()
    return jsonify({'mensaje': 'Registro actualizado', 'registro': _registro_tiempo_a_dict(reg, ahora)})


@bp.route('/api/time-tracker/registros/<int:reg_id>', methods=['DELETE'])
def eliminar_registro_tiempo(reg_id):
    eid, err = _requiere_empresa()
    if err:
        return err

    reg = _obtener_registro_tiempo(eid, reg_id)
    if not reg:
        return jsonify({'error': 'Registro no encontrado'}), 404

    db.session.delete(reg)
    db.session.commit()
    return jsonify({'mensaje': 'Registro eliminado'})


@bp.route('/api/time-tracker/resumen', methods=['GET'])
def resumen_registros_tiempo():
    eid, err = _requiere_empresa()
    if err:
        return err

    agrupar = (request.args.get('agrupar') or 'proyecto').strip().lower()
    if agrupar not in ('proyecto', 'trabajador'):
        return jsonify({'error': 'agrupar debe ser proyecto o trabajador'}), 400

    query = RegistroTiempo.query.filter_by(empresa_id=eid)

    proyecto_id = request.args.get('proyecto_id')
    if proyecto_id:
        query = query.filter_by(proyecto_id=int(proyecto_id))

    trabajador_id = request.args.get('trabajador_id')
    if trabajador_id:
        query = query.filter_by(trabajador_id=int(trabajador_id))

    desde = _parse_fecha(request.args.get('desde'))
    if desde:
        query = query.filter(RegistroTiempo.inicio >= datetime.combine(desde, datetime.min.time()))

    hasta = _parse_fecha(request.args.get('hasta'))
    if hasta:
        query = query.filter(RegistroTiempo.inicio <= datetime.combine(hasta, datetime.max.time()))

    registros = query.all()
    ahora = datetime.utcnow()
    grupos: dict = {}

    for reg in registros:
        if agrupar == 'proyecto':
            key = reg.proyecto_id
            label = reg.proyecto.nombre if reg.proyecto else f'Proyecto {key}'
        else:
            key = reg.trabajador_id
            label = _nombre_completo_trabajador(reg.trabajador) if reg.trabajador else f'Trabajador {key}'

        if key not in grupos:
            grupos[key] = {
                'id': key,
                'nombre': label,
                'duracion_segundos': 0,
                'registros': 0,
            }
        grupos[key]['duracion_segundos'] += _duracion_efectiva(reg, ahora)
        grupos[key]['registros'] += 1

    items = sorted(grupos.values(), key=lambda x: (-x['duracion_segundos'], x['nombre']))
    return jsonify({'agrupar': agrupar, 'items': items})
