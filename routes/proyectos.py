from datetime import date

from flask import Blueprint, current_app, jsonify, request

from bootstrap import ESTADOS_PROPUESTA
from common import *
from contabilidad import calcular_transaccion, recalcular_proyecto
from extensions import db
from models import (
    Cliente, EntregaProgramada, Movimiento, Propuesta, Proyecto, TareaEntrega,
)

from services.rentabilidad_service import calcular_rentabilidad_proyectos

bp = Blueprint('proyectos', __name__)

@bp.route('/api/proyectos/rentabilidad', methods=['GET'])
@admin_required
def rentabilidad_proyectos():
    eid, err = _requiere_empresa()
    if err:
        return err
    return jsonify(calcular_rentabilidad_proyectos(eid))


@bp.route('/api/clientes', methods=['GET', 'POST'])
def manejar_clientes():
    eid, err = _requiere_empresa()
    if err:
        return err
    if request.method == 'POST':
        data = request.json
        nuevo = Cliente(
            empresa_id=eid,
            razon_social=data['razon_social'],
            rut=data['rut'],
            comentarios=data.get('comentarios'),
        )
        db.session.add(nuevo)
        db.session.commit()
        return jsonify({'id': nuevo.id, 'mensaje': 'Cliente creado'}), 201

    return jsonify([
        {
            'id': c.id,
            'razon_social': c.razon_social,
            'rut': c.rut,
            'comentarios': c.comentarios,
            'num_proyectos': len(c.proyectos),
        }
        for c in Cliente.query.filter_by(empresa_id=eid).all()
    ])


@bp.route('/api/clientes/<int:cliente_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_cliente(cliente_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    cliente = Cliente.query.filter_by(empresa_id=eid, id=cliente_id).first_or_404()

    if request.method == 'GET':
        return jsonify({
            'id': cliente.id,
            'razon_social': cliente.razon_social,
            'rut': cliente.rut,
            'comentarios': cliente.comentarios,
            'num_proyectos': len(cliente.proyectos),
        })

    if request.method == 'DELETE':
        if cliente.proyectos:
            return jsonify({'error': 'No se puede eliminar: tiene proyectos asociados'}), 400
        db.session.delete(cliente)
        db.session.commit()
        return jsonify({'mensaje': 'Cliente eliminado'})

    data = request.json
    cliente.razon_social = data.get('razon_social', cliente.razon_social)[:150]
    cliente.rut = data.get('rut', cliente.rut)[:20]
    cliente.comentarios = data.get('comentarios', cliente.comentarios)
    db.session.commit()
    return jsonify({'mensaje': 'Cliente actualizado', 'id': cliente.id})


@bp.route('/api/proyectos', methods=['GET', 'POST'])
def manejar_proyectos():
    eid, err = _requiere_empresa()
    if err:
        return err

    if request.method == 'POST':
        campos, error = _validar_datos_proyecto(request.json or {}, eid)
        if error:
            return jsonify({'error': error}), 400
        nuevo_p = Proyecto(empresa_id=eid, **campos)
        db.session.add(nuevo_p)
        db.session.commit()
        return jsonify({'mensaje': 'Proyecto creado con éxito', 'id': nuevo_p.id}), 201

    try:
        movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
        for p in Proyecto.query.filter_by(empresa_id=eid).all():
            recalcular_proyecto(p, movimientos)
        db.session.commit()

        query = Proyecto.query.filter_by(empresa_id=eid)
        query = _filtrar_proyectos_query(query, request.args)
        query = _ordenar_proyectos(query, request.args.get('sort'), request.args.get('order'))
        page, per_page = _parse_pagination_args()
        page_items, total, page = _paginate_query(query, page, per_page)
        return _paginated_json(
            [_proyecto_a_dict(p, movimientos) for p in page_items],
            total,
            page,
            per_page,
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Error en GET /api/proyectos empresa_id=%s', eid)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/proyectos/<int:proyecto_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_proyecto(proyecto_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    proyecto = Proyecto.query.filter_by(empresa_id=eid, id=proyecto_id).first_or_404()
    movimientos = Movimiento.query.filter_by(empresa_id=eid).all()

    if request.method == 'GET':
        return jsonify(_proyecto_a_dict(proyecto, movimientos))

    if request.method == 'DELETE':
        Movimiento.query.filter_by(empresa_id=eid, proyecto_id=proyecto_id).delete()
        entrega_ids = [
            e.id for e in EntregaProgramada.query.filter_by(
                empresa_id=eid, proyecto_id=proyecto_id,
            ).all()
        ]
        if entrega_ids:
            TareaEntrega.query.filter(TareaEntrega.entrega_id.in_(entrega_ids)).delete(
                synchronize_session=False,
            )
        EntregaProgramada.query.filter_by(empresa_id=eid, proyecto_id=proyecto_id).delete()
        db.session.delete(proyecto)
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify({'mensaje': 'Proyecto eliminado'})

    data = request.json or {}
    if 'nombre' in data:
        nombre = (data.get('nombre') or '').strip()
        if not nombre:
            return jsonify({'error': 'nombre requerido'}), 400
        proyecto.nombre = nombre[:150]
    if 'superficie' in data:
        try:
            superficie = float(data['superficie'])
        except (TypeError, ValueError):
            return jsonify({'error': 'superficie inválida'}), 400
        if superficie < 0:
            return jsonify({'error': 'superficie debe ser mayor o igual a 0'}), 400
        proyecto.superficie = superficie
    if 'servicio' in data:
        servicio = (data.get('servicio') or '').strip()
        if not servicio:
            return jsonify({'error': 'servicio requerido'}), 400
        proyecto.servicio = servicio[:100]
    if 'cliente_id' in data:
        try:
            cliente_id = int(data['cliente_id'])
        except (TypeError, ValueError):
            return jsonify({'error': 'cliente_id inválido'}), 400
        cliente = Cliente.query.filter_by(empresa_id=eid, id=cliente_id).first()
        if not cliente:
            return jsonify({'error': 'Cliente no pertenece a la empresa activa'}), 400
        proyecto.cliente_id = cliente.id
    if 'status' in data:
        proyecto.status = data['status']
    db.session.commit()
    _recalcular_todos_proyectos(eid)
    return jsonify({'mensaje': 'Proyecto actualizado', 'id': proyecto.id})


@bp.route('/api/proyectos/<int:proyecto_id>/movimientos', methods=['GET'])
def movimientos_proyecto(proyecto_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    Proyecto.query.filter_by(empresa_id=eid, id=proyecto_id).first_or_404()
    clase = request.args.get('clase')
    query = Movimiento.query.filter_by(empresa_id=eid, proyecto_id=proyecto_id, estado='Activo')
    if clase:
        query = query.filter_by(clase=clase)
    movimientos = query.order_by(Movimiento.fecha_movimiento.desc()).all()
    return jsonify([_movimiento_a_dict(m) for m in movimientos])


@bp.route('/api/proyectos/<int:proyecto_id>/estados-pago', methods=['POST'])
def crear_estado_pago(proyecto_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    mov = _crear_estado_pago(proyecto_id, request.json, eid)
    db.session.commit()
    _recalcular_todos_proyectos(eid)
    return jsonify({'mensaje': 'Estado de pago registrado', 'id': mov.id}), 201


@bp.route('/api/proyectos/<int:proyecto_id>/gastos', methods=['POST'])
def crear_gasto(proyecto_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    mov = _crear_gasto(proyecto_id, request.json, eid)
    db.session.commit()
    _recalcular_todos_proyectos(eid)
    return jsonify({'mensaje': 'Gasto registrado', 'id': mov.id}), 201


@bp.route('/api/proyectos/<int:proyecto_id>/entregas', methods=['GET', 'POST'])
def manejar_entregas_proyecto(proyecto_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    proyecto = Proyecto.query.filter_by(empresa_id=eid, id=proyecto_id).first_or_404()

    if request.method == 'GET':
        entregas = EntregaProgramada.query.filter_by(
            empresa_id=eid, proyecto_id=proyecto.id,
        ).order_by(EntregaProgramada.fecha_entrega).all()
        return jsonify([_entrega_a_dict(e) for e in entregas])

    data = request.json or {}
    fecha = _parse_fecha(data.get('fecha_entrega'))
    if not fecha:
        return jsonify({'error': 'fecha_entrega requerida'}), 400
    status = data.get('status', 'Por Hacer')
    if status not in ESTADOS_ENTREGA:
        return jsonify({'error': f'status debe ser uno de: {", ".join(ESTADOS_ENTREGA)}'}), 400
    entrega = EntregaProgramada(
        empresa_id=eid,
        proyecto_id=proyecto.id,
        fecha_entrega=fecha,
        descripcion=(data.get('descripcion') or '')[:255] or None,
        status=status,
    )
    db.session.add(entrega)
    db.session.commit()
    return jsonify({'mensaje': 'Entrega programada', 'entrega': _entrega_a_dict(entrega)}), 201


@bp.route('/api/entregas', methods=['POST'])
def crear_entrega():
    eid, err = _requiere_empresa()
    if err:
        return err
    data = request.json or {}
    proyecto_id = data.get('proyecto_id')
    if not proyecto_id:
        return jsonify({'error': 'proyecto_id requerido'}), 400
    proyecto = Proyecto.query.filter_by(empresa_id=eid, id=int(proyecto_id)).first_or_404()
    fecha = _parse_fecha(data.get('fecha_entrega'))
    if not fecha:
        return jsonify({'error': 'fecha_entrega requerida'}), 400
    status = data.get('status', 'Por Hacer')
    if status not in ESTADOS_ENTREGA:
        return jsonify({'error': f'status debe ser uno de: {", ".join(ESTADOS_ENTREGA)}'}), 400
    entrega = EntregaProgramada(
        empresa_id=eid,
        proyecto_id=proyecto.id,
        fecha_entrega=fecha,
        descripcion=(data.get('descripcion') or '')[:255] or None,
        status=status,
    )
    db.session.add(entrega)
    db.session.commit()
    return jsonify({'mensaje': 'Entrega programada', 'entrega': _entrega_a_dict(entrega)}), 201


@bp.route('/api/entregas/<int:entrega_id>', methods=['GET', 'PUT', 'PATCH', 'DELETE'])
def manejar_entrega(entrega_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    entrega = EntregaProgramada.query.filter_by(empresa_id=eid, id=entrega_id).first_or_404()

    if request.method == 'GET':
        return jsonify(_entrega_a_dict(entrega))

    if request.method == 'DELETE':
        db.session.delete(entrega)
        db.session.commit()
        return jsonify({'mensaje': 'Entrega eliminada'})

    data = request.json or {}
    if 'fecha_entrega' in data:
        fecha = _parse_fecha(data.get('fecha_entrega'))
        if not fecha:
            return jsonify({'error': 'fecha_entrega inválida'}), 400
        entrega.fecha_entrega = fecha
    if 'descripcion' in data:
        entrega.descripcion = (data.get('descripcion') or '')[:255] or None
    if 'status' in data:
        status = data.get('status')
        if status not in ESTADOS_ENTREGA:
            return jsonify({'error': f'status debe ser uno de: {", ".join(ESTADOS_ENTREGA)}'}), 400
        entrega.status = status
    if 'proyecto_id' in data and data.get('proyecto_id'):
        proyecto = Proyecto.query.filter_by(empresa_id=eid, id=int(data['proyecto_id'])).first_or_404()
        entrega.proyecto_id = proyecto.id
    db.session.commit()
    return jsonify({'mensaje': 'Entrega actualizada', 'entrega': _entrega_a_dict(entrega)})


@bp.route('/api/entregas/<int:entrega_id>/tareas', methods=['GET', 'POST'])
def manejar_tareas_entrega(entrega_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    entrega = EntregaProgramada.query.filter_by(empresa_id=eid, id=entrega_id).first_or_404()

    if request.method == 'GET':
        tareas = TareaEntrega.query.filter_by(
            empresa_id=eid, entrega_id=entrega.id,
        ).order_by(TareaEntrega.id).all()
        return jsonify([_tarea_a_dict(t) for t in tareas])

    data = request.json or {}
    asignado_id, asig_err = _validar_asignado_id_trabajador(data.get('asignado_id'), eid)
    if asig_err:
        return jsonify({'error': asig_err}), 400
    status = data.get('status', 'Pendiente')
    if status not in ESTADOS_TAREA_ENTREGA:
        return jsonify({'error': f'status debe ser uno de: {", ".join(ESTADOS_TAREA_ENTREGA)}'}), 400
    tarea = TareaEntrega(
        empresa_id=eid,
        entrega_id=entrega.id,
        descripcion=(data.get('descripcion') or '')[:500] or None,
        asignado_id=asignado_id,
        fecha_limite=_parse_fecha(data.get('fecha_limite')),
        status=status,
    )
    db.session.add(tarea)
    db.session.commit()
    return jsonify({'mensaje': 'Tarea creada', 'tarea': _tarea_a_dict(tarea)}), 201


@bp.route('/api/tareas/<int:tarea_id>', methods=['PUT', 'PATCH', 'DELETE'])
def manejar_tarea(tarea_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    tarea = TareaEntrega.query.filter_by(empresa_id=eid, id=tarea_id).first_or_404()

    if request.method == 'DELETE':
        db.session.delete(tarea)
        db.session.commit()
        return jsonify({'mensaje': 'Tarea eliminada'})

    data = request.json or {}
    if 'descripcion' in data:
        tarea.descripcion = (data.get('descripcion') or '')[:500] or None
    if 'asignado_id' in data:
        asignado_id, asig_err = _validar_asignado_id_trabajador(data.get('asignado_id'), eid)
        if asig_err:
            return jsonify({'error': asig_err}), 400
        tarea.asignado_id = asignado_id
    if 'fecha_limite' in data:
        tarea.fecha_limite = _parse_fecha(data.get('fecha_limite'))
    if 'status' in data:
        status = data.get('status')
        if status not in ESTADOS_TAREA_ENTREGA:
            return jsonify({'error': f'status debe ser uno de: {", ".join(ESTADOS_TAREA_ENTREGA)}'}), 400
        tarea.status = status
    db.session.commit()
    return jsonify({'mensaje': 'Tarea actualizada', 'tarea': _tarea_a_dict(tarea)})


@bp.route('/api/estados-pago', methods=['GET', 'POST'])
def manejar_estados_pago_gantt():
    """Estados de pago para Gantt — respaldados por Movimiento (clase=estado_pago)."""
    eid, err = _requiere_empresa()
    if err:
        return err

    if request.method == 'POST':
        data = request.json or {}
        proyecto_id = data.get('proyecto_id')
        if not proyecto_id:
            return jsonify({'error': 'proyecto_id requerido'}), 400
        payload = {
            'fecha': data.get('fecha_estimada') or data.get('fecha') or date.today().isoformat(),
            'monto': data.get('monto', 0),
            'descripcion': data.get('descripcion'),
            'status_pago': _estado_gantt_a_status_pago(data.get('estado', 'Pendiente')),
        }
        mov = _crear_estado_pago(int(proyecto_id), payload, eid)
        if data.get('estado') == 'Pagado' and data.get('fecha_pago_real'):
            mov.fecha_estado_pago = _parse_fecha(data.get('fecha_pago_real'))
        elif data.get('estado') == 'Facturado' and data.get('fecha_facturacion'):
            mov.fecha_facturacion = _parse_fecha(data.get('fecha_facturacion'))
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify({'mensaje': 'Estado de pago creado', 'estado_pago': _estado_pago_gantt_dict(mov)}), 201

    query = Movimiento.query.filter_by(empresa_id=eid, clase='estado_pago', estado='Activo')
    query = query.filter(Movimiento.proyecto_id.isnot(None))
    movs = query.order_by(Movimiento.fecha_movimiento).all()
    return jsonify([_estado_pago_gantt_dict(m) for m in movs])


@bp.route('/api/estados-pago/<int:ep_id>', methods=['GET', 'PATCH', 'PUT'])
def manejar_estado_pago_gantt(ep_id):
    """Actualiza fecha estimada (drag) o estado de un estado de pago Gantt."""
    eid, err = _requiere_empresa()
    if err:
        return err
    mov = Movimiento.query.filter_by(
        empresa_id=eid, id=ep_id, clase='estado_pago', estado='Activo',
    ).first_or_404()

    if request.method == 'GET':
        return jsonify(_estado_pago_gantt_dict(mov))

    data = request.json or {}
    if 'fecha_estimada' in data:
        fecha = _parse_fecha(data.get('fecha_estimada'))
        if fecha:
            mov.fecha_movimiento = fecha
    if 'estado' in data:
        estado = data.get('estado')
        if estado not in ESTADOS_EP_GANTT:
            return jsonify({'error': f'estado debe ser uno de: {", ".join(ESTADOS_EP_GANTT)}'}), 400
        mov.status_pago = _estado_gantt_a_status_pago(estado)
        if estado == 'Pagado':
            fecha_pago = _parse_fecha(data.get('fecha_pago_real')) or mov.fecha_estado_pago or date.today()
            mov.fecha_estado_pago = fecha_pago
        elif estado == 'Facturado':
            if data.get('fecha_facturacion'):
                mov.fecha_facturacion = _parse_fecha(data.get('fecha_facturacion'))
        else:
            mov.fecha_estado_pago = None
    if 'descripcion' in data:
        mov.descripcion = data.get('descripcion')
    if 'monto' in data:
        mov.monto_pesos = float(data['monto'])
    db.session.commit()
    _recalcular_todos_proyectos(eid)
    return jsonify({'mensaje': 'Estado de pago actualizado', 'estado_pago': _estado_pago_gantt_dict(mov)})


@bp.route('/api/gantt/datos', methods=['GET'])
def gantt_datos():
    """Proyectos activos, estados de pago y entregas para la carta Gantt."""
    eid, err = _requiere_empresa()
    if err:
        return err

    movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
    proyectos = Proyecto.query.filter_by(empresa_id=eid, status='Activo').order_by(Proyecto.nombre).all()
    for p in proyectos:
        recalcular_proyecto(p, movimientos)

    estados_pago = Movimiento.query.filter_by(
        empresa_id=eid, clase='estado_pago', estado='Activo',
    ).filter(Movimiento.proyecto_id.isnot(None)).order_by(Movimiento.fecha_movimiento).all()

    proyecto_ids = {p.id for p in proyectos}
    entregas = EntregaProgramada.query.filter_by(empresa_id=eid).filter(
        EntregaProgramada.proyecto_id.in_(proyecto_ids) if proyecto_ids else False,
    ).order_by(EntregaProgramada.fecha_entrega).all() if proyecto_ids else []

    desde_param = _parse_fecha(request.args.get('desde'))
    hasta_param = _parse_fecha(request.args.get('hasta'))
    granularidad = request.args.get('granularidad', 'week')
    if granularidad not in ('day', 'week', 'month'):
        granularidad = 'week'
    desde, hasta = _gantt_timeline_rango(
        desde_param, hasta_param, estados_pago, entregas, granularidad,
    )

    db.session.commit()
    return jsonify({
        'proyectos': [_proyecto_gantt_dict(p) for p in proyectos],
        'estados_pago': [_estado_pago_gantt_dict(m) for m in estados_pago if m.proyecto_id in proyecto_ids],
        'entregas': [_entrega_a_dict(e, include_tareas=True) for e in entregas],
        'timeline': {
            'desde': desde.isoformat(),
            'hasta': hasta.isoformat(),
            'granularidad': granularidad,
        },
        'estados_ep': ESTADOS_EP_GANTT,
        'estados_entrega': ESTADOS_ENTREGA,
        'estados_tarea': ESTADOS_TAREA_ENTREGA,
    })


@bp.route('/api/propuestas/estados', methods=['GET'])
def get_estados_propuesta():
    return jsonify(ESTADOS_PROPUESTA)


@bp.route('/api/propuestas', methods=['GET', 'POST'])
def manejar_propuestas():
    eid, err = _requiere_empresa()
    if err:
        return err

    if request.method == 'POST':
        data = request.json or {}
        campos, error = _validar_datos_propuesta(data, eid)
        if error:
            return jsonify({'error': error}), 400
        nueva = Propuesta(empresa_id=eid, **campos)
        db.session.add(nueva)
        db.session.commit()
        return jsonify({'mensaje': 'Propuesta creada', 'propuesta': _propuesta_a_dict(nueva)}), 201

    query = Propuesta.query.filter_by(empresa_id=eid)
    status = request.args.get('status')
    if status:
        query = query.filter_by(status=status)
    propuestas = query.order_by(Propuesta.numero.desc()).all()
    return jsonify([_propuesta_a_dict(p) for p in propuestas])


@bp.route('/api/propuestas/<int:propuesta_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_propuesta(propuesta_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    propuesta = Propuesta.query.filter_by(empresa_id=eid, id=propuesta_id).first_or_404()

    if request.method == 'GET':
        return jsonify(_propuesta_a_dict(propuesta))

    if request.method == 'DELETE':
        db.session.delete(propuesta)
        db.session.commit()
        return jsonify({'mensaje': 'Propuesta eliminada'})

    data = request.json or {}
    campos, error = _validar_datos_propuesta(data, eid, propuesta_id=propuesta_id)
    if error:
        return jsonify({'error': error}), 400
    for clave, valor in campos.items():
        setattr(propuesta, clave, valor)
    db.session.commit()
    return jsonify({'mensaje': 'Propuesta actualizada', 'propuesta': _propuesta_a_dict(propuesta)})


@bp.route('/api/propuestas/importar', methods=['POST'])
def importar_propuestas_excel():
    """Importa propuestas desde hoja PROPUESTAS del Excel maestro."""
    from importar_excel import importar_propuestas_desde_excel, DEFAULT_XLSX
    eid, err = _requiere_empresa()
    if err:
        return err
    actualizar = request.args.get('actualizar', '1') == '1'
    path = request.json.get('archivo') if request.is_json and request.json else str(DEFAULT_XLSX)
    try:
        stats = importar_propuestas_desde_excel(path, actualizar=actualizar, empresa_id=eid)
        return jsonify(stats)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


