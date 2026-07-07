from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from common import *
from extensions import db
from models import EntregaProgramada, Movimiento, Proyecto, TareaEntrega

bp = Blueprint('gestion', __name__)

@bp.route('/api/gestion/dashboard', methods=['GET'])
def get_gestion_dashboard():
    """Dashboard operacional: proyectos activos, entregas, tareas y alertas."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err

        hoy = date.today()
        dias_alerta_entrega = int(request.args.get('dias_alerta', 14))
        lunes = _lunes_semana(hoy)
        domingo = lunes + timedelta(days=6)
        primer_mes = _primer_dia_mes(hoy)
        ultimo_mes = _ultimo_dia_mes(hoy)

        proyectos = Proyecto.query.filter_by(empresa_id=eid, status='Activo').order_by(Proyecto.nombre).all()
        proyecto_ids = [p.id for p in proyectos]

        entregas: list[EntregaProgramada] = []
        if proyecto_ids:
            entregas = EntregaProgramada.query.filter(
                EntregaProgramada.empresa_id == eid,
                EntregaProgramada.proyecto_id.in_(proyecto_ids),
            ).order_by(EntregaProgramada.fecha_entrega).all()

        entrega_ids = [e.id for e in entregas]
        tareas: list[TareaEntrega] = []
        if entrega_ids:
            tareas = TareaEntrega.query.filter(
                TareaEntrega.empresa_id == eid,
                TareaEntrega.entrega_id.in_(entrega_ids),
            ).all()

        entregas_por_proyecto: dict[int, list] = {}
        for e in entregas:
            entregas_por_proyecto.setdefault(e.proyecto_id, []).append(e)

        tareas_por_entrega: dict[int, list] = {}
        for t in tareas:
            tareas_por_entrega.setdefault(t.entrega_id, []).append(t)

        proyectos_resumen = []
        for p in proyectos:
            p_entregas = entregas_por_proyecto.get(p.id, [])
            p_tareas = [t for e in p_entregas for t in tareas_por_entrega.get(e.id, [])]
            pendientes_ent = [e for e in p_entregas if e.status == 'Por Hacer']
            proxima = min(pendientes_ent, key=lambda e: e.fecha_entrega) if pendientes_ent else None
            proyectos_resumen.append({
                'id': p.id,
                'nombre': p.nombre,
                'cliente': p.cliente_rel.razon_social if p.cliente_rel else '',
                'servicio': p.servicio,
                'entregas_total': len(p_entregas),
                'entregas_por_hacer': sum(1 for e in p_entregas if e.status == 'Por Hacer'),
                'entregas_hechas': sum(1 for e in p_entregas if e.status == 'Hecho'),
                'tareas_pendientes': sum(1 for t in p_tareas if t.status != 'Hecho'),
                'tareas_hechas': sum(1 for t in p_tareas if t.status == 'Hecho'),
                'proxima_entrega': proxima.fecha_entrega.strftime('%Y-%m-%d') if proxima else None,
            })

        alertas = []
        for e in entregas:
            if e.status != 'Por Hacer':
                continue
            dias = (e.fecha_entrega - hoy).days
            if dias > dias_alerta_entrega:
                continue
            alertas.append({
                'tipo': 'entrega',
                'nivel': _nivel_alerta_dias(dias),
                'proyecto_id': e.proyecto_id,
                'proyecto': e.proyecto_rel.nombre if e.proyecto_rel else '',
                'descripcion': e.descripcion or 'Entrega programada',
                'fecha': e.fecha_entrega.strftime('%Y-%m-%d'),
                'dias_restantes': dias,
            })

        proximas_tareas = []
        for t in tareas:
            if t.status == 'Hecho' or not t.fecha_limite:
                continue
            entrega = next((e for e in entregas if e.id == t.entrega_id), None)
            dias = (t.fecha_limite - hoy).days
            proximas_tareas.append({
                **_tarea_a_dict(t),
                'proyecto_id': entrega.proyecto_id if entrega else None,
                'proyecto': entrega.proyecto_rel.nombre if entrega and entrega.proyecto_rel else '',
                'entrega_descripcion': entrega.descripcion if entrega else '',
                'dias_restantes': dias,
                'nivel': _nivel_alerta_dias(dias),
            })
        proximas_tareas.sort(key=lambda x: (x['fecha_limite'] or '9999-12-31', x['id']))

        dias_proximas_entregas = int(request.args.get('dias_entregas', 60))
        limite_entregas = hoy + timedelta(days=dias_proximas_entregas)
        proximas_entregas = []
        for e in entregas:
            dias = (e.fecha_entrega - hoy).days
            if e.fecha_entrega > limite_entregas:
                continue
            entrega_tareas = []
            for t in sorted(
                tareas_por_entrega.get(e.id, []),
                key=lambda x: (x.fecha_limite or date.max, x.id),
            ):
                td = _tarea_a_dict(t)
                if t.fecha_limite:
                    td['dias_restantes'] = (t.fecha_limite - hoy).days
                else:
                    td['dias_restantes'] = None
                entrega_tareas.append(td)
            proximas_entregas.append({
                'id': e.id,
                'proyecto_id': e.proyecto_id,
                'proyecto': e.proyecto_rel.nombre if e.proyecto_rel else '',
                'fecha_entrega': e.fecha_entrega.strftime('%Y-%m-%d'),
                'descripcion': e.descripcion or '',
                'status': e.status,
                'dias_restantes': dias,
                'nivel': _nivel_alerta_dias(dias),
                'tareas': entrega_tareas,
            })
        proximas_entregas.sort(key=lambda x: (x['fecha_entrega'], x['id']))

        for t in proximas_tareas:
            if t['dias_restantes'] <= dias_alerta_entrega and t['status'] != 'Hecho':
                alertas.append({
                    'tipo': 'tarea',
                    'nivel': t['nivel'],
                    'proyecto_id': t['proyecto_id'],
                    'proyecto': t['proyecto'],
                    'descripcion': t['descripcion'] or 'Tarea pendiente',
                    'fecha': t['fecha_limite'],
                    'dias_restantes': t['dias_restantes'],
                    'asignado_nombre': t.get('asignado_nombre', ''),
                })

        actividades_semana = []
        actividades_mes = []

        for e in entregas:
            act = _actividad_gestion(
                'entrega', e.fecha_entrega,
                e.descripcion or 'Entrega programada',
                e.proyecto_rel.nombre if e.proyecto_rel else '',
                status=e.status, entrega_id=e.id, proyecto_id=e.proyecto_id,
            )
            if primer_mes <= e.fecha_entrega <= ultimo_mes:
                actividades_mes.append(act)
            if lunes <= e.fecha_entrega <= domingo:
                actividades_semana.append(act)

        for t in tareas:
            if not t.fecha_limite:
                continue
            entrega = next((e for e in entregas if e.id == t.entrega_id), None)
            act = _actividad_gestion(
                'tarea', t.fecha_limite,
                t.descripcion or 'Tarea',
                entrega.proyecto_rel.nombre if entrega and entrega.proyecto_rel else '',
                status=t.status, tarea_id=t.id, entrega_id=t.entrega_id,
                proyecto_id=entrega.proyecto_id if entrega else None,
                asignado_nombre=_tarea_a_dict(t).get('asignado_nombre', ''),
            )
            if primer_mes <= t.fecha_limite <= ultimo_mes:
                actividades_mes.append(act)
            if lunes <= t.fecha_limite <= domingo:
                actividades_semana.append(act)

        if proyecto_ids:
            eps = Movimiento.query.filter(
                Movimiento.empresa_id == eid,
                Movimiento.clase == 'estado_pago',
                Movimiento.estado == 'Activo',
                Movimiento.proyecto_id.in_(proyecto_ids),
                Movimiento.status_pago.in_(('Por enviar', 'Enviado', 'Facturado')),
            ).all()
            for m in eps:
                f_cobro = _fecha_estimada_ep(m)
                act = _actividad_gestion(
                    'cobro', f_cobro,
                    m.descripcion or 'Estado de pago',
                    m.proyecto_rel.nombre if m.proyecto_rel else '',
                    monto=m.monto_pesos,
                    status_pago=m.status_pago,
                    movimiento_id=m.id,
                    proyecto_id=m.proyecto_id,
                )
                if primer_mes <= f_cobro <= ultimo_mes:
                    actividades_mes.append(act)
                if lunes <= f_cobro <= domingo:
                    actividades_semana.append(act)
                dias = (f_cobro - hoy).days
                if _pago_ya_cobrado(m.status_pago):
                    continue
                if m.status_pago == 'Por enviar' and m.fecha_movimiento:
                    dias_atraso = (hoy - m.fecha_movimiento).days
                    if dias_atraso > 0:
                        alertas.append({
                            'tipo': 'cobro',
                            'nivel': _nivel_alerta_dias(-dias_atraso),
                            'proyecto_id': m.proyecto_id,
                            'proyecto': m.proyecto_rel.nombre if m.proyecto_rel else '',
                            'descripcion': f'{m.descripcion or "Estado de pago"} — Por enviar vencido',
                            'fecha': m.fecha_movimiento.strftime('%Y-%m-%d'),
                            'dias_restantes': -dias_atraso,
                            'monto': m.monto_pesos,
                        })
                        continue
                if m.fecha_facturacion and not _pago_ya_cobrado(m.status_pago):
                    dias_pago = m.condicion_pago_dias or 30
                    vencimiento = m.fecha_facturacion + timedelta(days=dias_pago)
                    if vencimiento < hoy:
                        dias_venc = (vencimiento - hoy).days
                        alertas.append({
                            'tipo': 'cobro',
                            'nivel': _nivel_alerta_dias(dias_venc),
                            'proyecto_id': m.proyecto_id,
                            'proyecto': m.proyecto_rel.nombre if m.proyecto_rel else '',
                            'descripcion': f'{m.descripcion or "Cobro pendiente"} — Pago vencido',
                            'fecha': vencimiento.strftime('%Y-%m-%d'),
                            'dias_restantes': dias_venc,
                            'monto': m.monto_pesos,
                        })
                        continue
                if dias <= dias_alerta_entrega and m.status_pago in ('Por enviar', 'Enviado'):
                    alertas.append({
                        'tipo': 'cobro',
                        'nivel': _nivel_alerta_dias(dias),
                        'proyecto_id': m.proyecto_id,
                        'proyecto': m.proyecto_rel.nombre if m.proyecto_rel else '',
                        'descripcion': m.descripcion or 'Cobro pendiente',
                        'fecha': f_cobro.strftime('%Y-%m-%d'),
                        'dias_restantes': dias,
                        'monto': m.monto_pesos,
                    })

        gastos_vencidos = Movimiento.query.filter(
            Movimiento.empresa_id == eid,
            Movimiento.clase == 'gasto',
            Movimiento.estado == 'Activo',
            Movimiento.status_pago == 'Gasto programado',
            Movimiento.fecha_movimiento.isnot(None),
            Movimiento.fecha_movimiento < hoy,
        ).all()
        for m in gastos_vencidos:
            dias_atraso = (hoy - m.fecha_movimiento).days
            alertas.append({
                'tipo': 'gasto',
                'nivel': _nivel_alerta_dias(-dias_atraso),
                'proyecto_id': m.proyecto_id,
                'proyecto': m.proyecto_rel.nombre if m.proyecto_rel else '',
                'descripcion': f'{m.descripcion or "Gasto programado"} — Vencido',
                'fecha': m.fecha_movimiento.strftime('%Y-%m-%d'),
                'dias_restantes': -dias_atraso,
                'monto': m.monto_pesos,
            })

        alertas.sort(key=lambda a: (a['dias_restantes'], a.get('tipo', '')))
        actividades_semana.sort(key=lambda a: a['fecha'])
        actividades_mes.sort(key=lambda a: a['fecha'])

        return jsonify({
            'proyectos': proyectos_resumen,
            'alertas': alertas,
            'proximas_tareas': proximas_tareas[:30],
            'proximas_entregas': proximas_entregas[:30],
            'actividades_semana': actividades_semana,
            'actividades_mes': actividades_mes,
            'resumen': {
                'proyectos_activos': len(proyectos),
                'alertas_total': len(alertas),
                'tareas_vencidas': sum(1 for t in proximas_tareas if t['dias_restantes'] < 0),
                'entregas_semana': sum(1 for a in actividades_semana if a['tipo'] == 'entrega'),
                'tareas_semana': sum(1 for a in actividades_semana if a['tipo'] == 'tarea'),
            },
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


