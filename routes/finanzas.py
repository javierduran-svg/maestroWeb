from datetime import date, datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import cast, func, String
from sqlalchemy.orm import aliased

from bootstrap import empresa_default_id as _empresa_default_id
from common import *
from contabilidad import (
    calcular_balance_cuenta,
    calcular_flujo_financiero,
    calcular_kpis,
    calcular_series_dashboard,
    calcular_transaccion,
    generar_eventos_calendario_contable,
    recalcular_proyecto,
    PERIODOS_DASHBOARD,
)
from extensions import db
from models import Cuenta, EmpresaBancoConexion, Movimiento, Proyecto, ValorUF
from sii_integration import SIIIntegrationError
from banco_integration import BancoIntegrationError, mensaje_error_red_fintoc

bp = Blueprint('finanzas', __name__)

# Lecturas mínimas para formularios de proyectos / gastos (trabajadores).
_FINANZAS_TRABAJADOR_PATHS = frozenset({
    '/api/servicios',
    '/api/status-pago',
    '/api/status-gasto',
    '/api/uf/hoy',
    '/api/cuentas/categorias',
})


@bp.before_request
def _finanzas_requiere_admin():
    if request.method == 'OPTIONS':
        return None
    path = request.path
    if path in _FINANZAS_TRABAJADOR_PATHS:
        return None
    if path == '/api/cuentas' and request.method == 'GET':
        return None
    if path.startswith('/api/cuentas/') and request.method == 'GET':
        return None
    if not _es_admin():
        return jsonify({'error': 'Acceso restringido a administradores'}), 403
    return None


def _semestre_actual_por_defecto():
    """Semestre calendario vigente: ene–jun o jul–dic del año actual."""
    hoy = date.today()
    if hoy.month <= 6:
        return date(hoy.year, 1, 1), date(hoy.year, 6, 30)
    return date(hoy.year, 7, 1), date(hoy.year, 12, 31)


def _normalizar_porcentajes_utilidades(pct_base, pct_desempeno):
    pct_base = float(pct_base if pct_base is not None else 60)
    pct_desempeno = float(pct_desempeno if pct_desempeno is not None else 40)
    total = pct_base + pct_desempeno
    if total <= 0:
        return 60.0, 40.0
    if abs(total - 100.0) > 0.01:
        pct_base = round(pct_base / total * 100.0, 2)
        pct_desempeno = round(100.0 - pct_base, 2)
    return pct_base, pct_desempeno

@bp.route('/api/status-pago', methods=['GET'])
def get_status_pago():
    return jsonify(STATUS_PAGO)


@bp.route('/api/status-gasto', methods=['GET'])
def get_status_gasto():
    return jsonify([{'value': s, 'label': 'Gasto real' if not s else s} for s in STATUS_GASTO])


@bp.route('/api/servicios', methods=['GET'])
def get_servicios():
    return jsonify(SERVICIOS)


@bp.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    eid, err = _requiere_empresa()
    if err:
        return err
    cuentas = Cuenta.query.filter_by(empresa_id=eid).all()
    proyectos = Proyecto.query.filter_by(empresa_id=eid).all()
    movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
    for p in proyectos:
        recalcular_proyecto(p, movimientos)
    db.session.commit()
    return jsonify(calcular_kpis(cuentas, proyectos, movimientos))


@bp.route('/api/dashboard/series', methods=['GET'])
def get_dashboard_series():
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        periodo = request.args.get('periodo', 'mensual').lower()
        if periodo not in PERIODOS_DASHBOARD:
            return jsonify({'error': f'Periodo no válido. Use: {", ".join(sorted(PERIODOS_DASHBOARD))}'}), 400
        anio_param = request.args.get('anio')
        anio = int(anio_param) if anio_param else date.today().year
        acumulado = _param_bool(request.args.get('acumulado'))
        proyeccion = _param_bool(request.args.get('proyeccion'))
        movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
        return jsonify(calcular_series_dashboard(
            movimientos,
            periodo=periodo,
            anio=anio,
            acumulado=acumulado,
            proyeccion=proyeccion,
        ))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Error al calcular series: {e}'}), 500


@bp.route('/api/flujo', methods=['GET'])
def get_flujo():
    eid, err = _requiere_empresa()
    if err:
        return err
    cuentas = Cuenta.query.filter_by(empresa_id=eid).order_by(Cuenta.categoria, Cuenta.nombre).all()
    movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
    return jsonify(calcular_flujo_financiero(cuentas, movimientos))


@bp.route('/api/finanzas/calendario', methods=['GET'])
def get_finanzas_calendario():
    """Calendario contable mensual (SII, Previred, plazos típicos Chile)."""
    try:
        _, err = _requiere_empresa()
        if err:
            return err
        hoy = date.today()
        mes = int(request.args.get('mes', hoy.month))
        anio = int(request.args.get('anio', hoy.year))
        if mes < 1 or mes > 12:
            return jsonify({'error': 'Mes inválido (1-12)'}), 400
        return jsonify(generar_eventos_calendario_contable(anio, mes))
    except ValueError:
        return jsonify({'error': 'Parámetros mes/anio inválidos'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/finanzas/simular-utilidades', methods=['POST'])
def simular_utilidades():
    """Simula reparto de utilidades por sueldo base y score de desempeño."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        data = request.json or {}
        pozo_raw = data.get('pozo_repartir', data.get('pozo_total', 0))
        try:
            pozo_total = float(pozo_raw or 0)
        except (TypeError, ValueError):
            return jsonify({'error': 'pozo_repartir debe ser numérico'}), 400
        if pozo_total < 0:
            return jsonify({'error': 'pozo_repartir no puede ser negativo'}), 400

        pct_base, pct_desempeno = _normalizar_porcentajes_utilidades(
            data.get('pct_base', data.get('porcentaje_base')),
            data.get('pct_desempeno', data.get('porcentaje_desempeno')),
        )

        if data.get('semestre_inicio') and data.get('semestre_fin'):
            semestre_inicio = _parse_fecha(data['semestre_inicio'])
            semestre_fin = _parse_fecha(data['semestre_fin'])
            if not semestre_inicio or not semestre_fin:
                return jsonify({'error': 'semestre_inicio y semestre_fin deben ser YYYY-MM-DD'}), 400
            if semestre_fin < semestre_inicio:
                return jsonify({'error': 'semestre_fin debe ser posterior o igual a semestre_inicio'}), 400
        else:
            semestre_inicio, semestre_fin = _semestre_actual_por_defecto()

        from services.reparticion_utilidades_service import simular_reparticion_utilidades
        resultado = simular_reparticion_utilidades(
            pozo_total,
            semestre_inicio,
            semestre_fin,
            eid,
            porcentaje_base=pct_base,
            porcentaje_desempeno=pct_desempeno,
        )
        return jsonify(resultado)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/finanzas/resumen', methods=['GET'])
def get_finanzas_resumen():
    """KPIs financieros calculados desde movimientos en BD."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        cuentas = Cuenta.query.filter_by(empresa_id=eid).all()
        proyectos = Proyecto.query.filter_by(empresa_id=eid).all()
        movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
        for p in proyectos:
            recalcular_proyecto(p, movimientos)
        db.session.commit()
        kpis = calcular_kpis(cuentas, proyectos, movimientos)
        cuentas_banco = _obtener_cuentas_banco_empresa(eid)
        saldos_banco = []
        saldo_total = 0.0
        for cuenta_banco in cuentas_banco:
            saldo = calcular_balance_cuenta(
                cuenta_banco.id, movimientos, cuenta_banco.saldo_inicial or 0.0,
            )
            saldos_banco.append({'cuenta_id': cuenta_banco.id, 'nombre': cuenta_banco.nombre, 'saldo': saldo})
            saldo_total += saldo
        return jsonify({
            **kpis,
            'saldo_banco_santander': saldo_total,
            'saldo_bancos_total': saldo_total,
            'cuenta_banco': cuentas_banco[0].nombre if cuentas_banco else '',
            'saldos_bancos': saldos_banco,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/banco/sincronizar', methods=['POST'])
def sincronizar_banco():
    """Importa movimientos desde la primera conexión bancaria activa (compatibilidad)."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        conn = EmpresaBancoConexion.query.filter_by(empresa_id=eid, activa=True).order_by(
            EmpresaBancoConexion.id,
        ).first()
        if not conn:
            conn_data = _env_fintoc_creds()
            if eid == _empresa_default_id() and any(conn_data.values()):
                cuenta = _obtener_cuenta_banco_santander(eid)
                conn = EmpresaBancoConexion(
                    empresa_id=eid,
                    nombre='Santander Cta Cte',
                    fintoc_api_key=conn_data.get('fintoc_api_key'),
                    fintoc_link_token=conn_data.get('fintoc_link_token'),
                    fintoc_account_id=conn_data.get('fintoc_account_id'),
                    cuenta_contable_id=cuenta.id,
                    activa=True,
                )
                db.session.add(conn)
                db.session.flush()
            else:
                return jsonify({'error': 'No hay conexiones bancarias activas. Agregue una en Bancos.'}), 400
        resultado = _sincronizar_conexion_banco(conn, eid)
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify(resultado)
    except BancoIntegrationError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 502
    except Exception as e:
        db.session.rollback()
        red = mensaje_error_red_fintoc(e)
        if red:
            return jsonify({'error': red}), 502
        return jsonify({'error': 'Error al sincronizar banco. Intente de nuevo.'}), 500


@bp.route('/api/bancos/sincronizar-todos', methods=['POST'])
def sincronizar_todos_bancos():
    """Sincroniza todas las conexiones bancarias activas de la empresa."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        conexiones = EmpresaBancoConexion.query.filter_by(empresa_id=eid, activa=True).order_by(
            EmpresaBancoConexion.id,
        ).all()
        if not conexiones:
            return jsonify({'error': 'No hay conexiones bancarias activas'}), 400
        resultados = []
        total_insertados = 0
        for conn in conexiones:
            try:
                r = _sincronizar_conexion_banco(conn, eid)
                total_insertados += r.get('insertados', 0)
                resultados.append(r)
            except BancoIntegrationError as e:
                resultados.append({
                    'banco_id': conn.id,
                    'banco_nombre': conn.nombre,
                    'error': str(e),
                    'insertados': 0,
                })
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify({
            'mensaje': f'Sincronización completada ({len(conexiones)} cuenta(s))',
            'insertados_total': total_insertados,
            'resultados': resultados,
        })
    except Exception as e:
        db.session.rollback()
        red = mensaje_error_red_fintoc(e)
        if red:
            return jsonify({'error': red}), 502
        return jsonify({'error': str(e)}), 500


@bp.route('/api/bancos/<int:banco_id>/sincronizar', methods=['POST'])
def sincronizar_banco_id(banco_id):
    """Sincroniza una conexión bancaria específica."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        conn = EmpresaBancoConexion.query.filter_by(id=banco_id, empresa_id=eid).first_or_404()
        resultado = _sincronizar_conexion_banco(conn, eid)
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify(resultado)
    except BancoIntegrationError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 502
    except Exception as e:
        db.session.rollback()
        red = mensaje_error_red_fintoc(e)
        if red:
            return jsonify({'error': red}), 502
        return jsonify({'error': str(e)}), 500


@bp.route('/api/banco/movimientos', methods=['GET'])
def listar_movimientos_banco():
    """Últimos movimientos de todas las cuentas bancarias vinculadas."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        cuentas_banco = _obtener_cuentas_banco_empresa(eid)
        cuenta_ids = [c.id for c in cuentas_banco]
        limite = request.args.get('limite', 50, type=int)
        banco_id = request.args.get('banco_id', type=int)
        if banco_id:
            conn = EmpresaBancoConexion.query.filter_by(id=banco_id, empresa_id=eid).first_or_404()
            if conn.cuenta_contable_id:
                cuenta_ids = [conn.cuenta_contable_id]
        query = Movimiento.query.filter(
            Movimiento.empresa_id == eid,
            db.or_(
                Movimiento.cta_origen_id.in_(cuenta_ids),
                Movimiento.cta_destino_id.in_(cuenta_ids),
            ),
        ).order_by(Movimiento.fecha_movimiento.desc(), Movimiento.id.desc())
        if limite > 0:
            query = query.limit(limite)
        return jsonify([_movimiento_a_dict(m) for m in query.all()])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/cuentas', methods=['GET', 'POST'])
def manejar_cuentas():
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        if request.method == 'POST':
            data = request.json or {}
            campos, error = _validar_datos_cuenta(data, eid)
            if error:
                return jsonify({'error': error}), 400
            nueva = Cuenta(empresa_id=eid, **campos)
            db.session.add(nueva)
            db.session.commit()
            return jsonify({
                'mensaje': 'Cuenta creada',
                'cuenta': _cuenta_a_dict(nueva, nueva.saldo_inicial or 0.0),
            }), 201

        tipo = request.args.get('tipo')
        con_saldos = _param_bool(request.args.get('con_saldos'))
        query = Cuenta.query.filter_by(empresa_id=eid).order_by(Cuenta.categoria, Cuenta.nombre)
        if tipo == 'gasto':
            query = query.filter_by(categoria='gasto')
        elif tipo == 'remuneracion':
            query = query.filter(
                Cuenta.categoria == 'gasto',
                Cuenta.nombre.like('Remuneracion trabajador%'),
            )
        cuentas = query.all()
        if con_saldos:
            movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
            flujo = {f['id']: f['saldo'] for f in calcular_flujo_financiero(cuentas, movimientos)}
            return jsonify([_cuenta_a_dict(c, flujo.get(c.id, 0.0)) for c in cuentas])
        return jsonify([_cuenta_a_dict(c) for c in cuentas])
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/cuentas/categorias', methods=['GET'])
def get_categorias_cuenta():
    return jsonify(CATEGORIAS_CUENTA)


@bp.route('/api/cuentas/<int:cuenta_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_cuenta(cuenta_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    cuenta = Cuenta.query.filter_by(empresa_id=eid, id=cuenta_id).first_or_404()
    try:
        if request.method == 'GET':
            movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
            saldo = calcular_balance_cuenta(cuenta.id, movimientos, cuenta.saldo_inicial or 0.0)
            return jsonify(_cuenta_a_dict(cuenta, saldo))

        if request.method == 'DELETE':
            motivo = _cuenta_en_uso(cuenta_id, eid)
            if motivo:
                return jsonify({'error': f'No se puede eliminar: la cuenta {motivo}'}), 400
            db.session.delete(cuenta)
            db.session.commit()
            return jsonify({'mensaje': 'Cuenta eliminada'})

        data = request.json or {}
        campos, error = _validar_datos_cuenta(data, eid, cuenta_id=cuenta_id)
        if error:
            return jsonify({'error': error}), 400
        for clave, valor in campos.items():
            setattr(cuenta, clave, valor)
        db.session.commit()
        movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
        saldo = calcular_balance_cuenta(cuenta.id, movimientos, cuenta.saldo_inicial or 0.0)
        return jsonify({'mensaje': 'Cuenta actualizada', 'cuenta': _cuenta_a_dict(cuenta, saldo)})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/movimientos/<int:mov_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_movimiento(mov_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    mov = Movimiento.query.filter_by(empresa_id=eid, id=mov_id).first_or_404()

    if request.method == 'GET':
        return jsonify(_movimiento_a_dict(mov))

    if request.method == 'DELETE':
        db.session.delete(mov)
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify({'mensaje': 'Movimiento eliminado'})

    data = request.json
    _aplicar_datos_movimiento(mov, data)
    db.session.commit()
    _recalcular_todos_proyectos(eid)
    return jsonify({'mensaje': 'Movimiento actualizado', 'movimiento': _movimiento_a_dict(mov)})


@bp.route('/api/movimientos/<int:mov_id>/duplicar', methods=['POST'])
def duplicar_movimiento(mov_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    original = Movimiento.query.filter_by(empresa_id=eid, id=mov_id).first_or_404()
    copia = Movimiento(
        empresa_id=eid,
        fecha_movimiento=original.fecha_movimiento,
        fecha_estado_pago=original.fecha_estado_pago,
        fecha_facturacion=original.fecha_facturacion,
        monto_pesos=original.monto_pesos,
        monto_uf=original.monto_uf,
        valor_uf=original.valor_uf,
        centro_costo=original.centro_costo,
        estado='Activo',
        clase=original.clase,
        cta_origen_id=original.cta_origen_id,
        cta_destino_id=original.cta_destino_id,
        transaccion=original.transaccion,
        descripcion=(original.descripcion or '') + ' (copia)',
        numero_factura=original.numero_factura,
        status_pago=original.status_pago,
        condicion_pago_dias=original.condicion_pago_dias or 30,
        proyecto_id=original.proyecto_id,
        numero_ep=original.numero_ep,
        atencion_de=original.atencion_de,
        notas_ep=original.notas_ep,
        incluir_iva=original.incluir_iva,
        template_html=original.template_html,
    )
    db.session.add(copia)
    db.session.commit()
    _recalcular_todos_proyectos(eid)
    return jsonify({'mensaje': 'Movimiento duplicado', 'id': copia.id}), 201


@bp.route('/api/movimientos', methods=['GET', 'POST'])
def manejar_movimientos():
    eid, err = _requiere_empresa()
    if err:
        return err
    if request.method == 'POST':
        data = request.json
        clase = data.get('clase', 'general')
        if clase == 'estado_pago':
            mov = _crear_estado_pago(int(data['proyecto_id']), data, eid)
        elif clase == 'gasto':
            mov = _crear_gasto(int(data['proyecto_id']), data, eid)
        else:
            origen = Cuenta.query.filter_by(empresa_id=eid, id=int(data['origen_id'])).first_or_404()
            destino = Cuenta.query.filter_by(empresa_id=eid, id=int(data['destino_id'])).first_or_404()
            tipo = calcular_transaccion(origen.categoria, destino.categoria)
            centro = data.get('centro_costo', 'Administración')
            proyecto_id = data.get('proyecto_id')
            if proyecto_id:
                proyecto_id = int(proyecto_id)
                proyecto = Proyecto.query.filter_by(empresa_id=eid, id=proyecto_id).first_or_404()
                centro = proyecto.nombre
            clase_mov = (
                'estado_pago'
                if tipo == 'Ingreso' and _nombre_cuenta_es_clientes(origen.nombre)
                else 'general'
            )
            mov = Movimiento(
                empresa_id=eid,
                fecha_movimiento=_parse_fecha(data['fecha']),
                fecha_estado_pago=_parse_fecha(data.get('fecha_estado_pago')),
                fecha_facturacion=_parse_fecha(data.get('fecha_facturacion')),
                monto_pesos=float(data['monto']),
                centro_costo=centro,
                clase=clase_mov,
                cta_origen_id=origen.id,
                cta_destino_id=destino.id,
                transaccion=tipo,
                descripcion=data.get('descripcion'),
                numero_factura=data.get('numero_factura'),
                proyecto_id=proyecto_id,
                status_pago=(
                    data.get('status_pago')
                    or ('Por enviar' if clase_mov == 'estado_pago' else None)
                ),
            )
            db.session.add(mov)
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify({'mensaje': 'Movimiento registrado', 'id': mov.id}), 201

    query = Movimiento.query.filter_by(empresa_id=eid)
    query = _filtrar_movimientos_query(query, request.args)
    query = _ordenar_movimientos(query, request.args.get('sort'), request.args.get('order'))
    page, per_page = _parse_pagination_args()
    page_items, total, page = _paginate_query(query, page, per_page)
    return _paginated_json(
        [_movimiento_a_dict(m) for m in page_items],
        total,
        page,
        per_page,
    )


@bp.route('/api/importar', methods=['POST'])
def importar_datos():
    """Importa CLIENTES, PROYECTOS y MOVIMIENTOS desde el Excel maestro."""
    from importar_excel import importar_desde_excel, DEFAULT_XLSX
    eid, err = _requiere_empresa()
    if err:
        return err
    reset = request.args.get('reset', '1') == '1'
    path = request.json.get('archivo') if request.is_json and request.json else str(DEFAULT_XLSX)
    try:
        stats = importar_desde_excel(path, reset=reset, empresa_id=eid)
        return jsonify(stats)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/sii/documentos', methods=['GET'])
def sii_listar_documentos():
    """Lista DTEs emitidos en un mes (parámetros: mes, anio)."""
    eid, err = _requiere_empresa()
    if err:
        return err
    mes = request.args.get('mes', type=int)
    anio = request.args.get('anio', type=int)
    if not mes or not anio:
        return jsonify({'error': 'Parámetros mes (1-12) y anio son requeridos'}), 400
    try:
        client = _sii_client_for_empresa(eid)
        documentos = client.obtener_dtes_emitidos(mes, anio)
        return jsonify(documentos)
    except SIIIntegrationError as e:
        return jsonify({'error': str(e)}), _http_status_sii_error(str(e))


@bp.route('/api/sii/emitir', methods=['POST'])
def sii_emitir_documento():
    """Emite un DTE y registra el movimiento contable asociado."""
    eid, err = _requiere_empresa()
    if err:
        return err
    data = request.json or {}
    campos = ('rut_receptor', 'razon_social', 'detalle', 'monto')
    faltantes = [c for c in campos if not data.get(c)]
    if faltantes:
        return jsonify({'error': f'Campos requeridos: {", ".join(faltantes)}'}), 400

    try:
        client = _sii_client_for_empresa(eid)
        dte = client.emitir_factura(
            rut_receptor=data['rut_receptor'],
            razon_social=data['razon_social'],
            detalle=data['detalle'],
            monto=float(data['monto']),
            tipo_documento=int(data.get('tipo_documento', 33)),
            pagado=bool(data.get('pagado', False)),
        )
        mov = _registrar_movimiento_desde_dte(data, dte, eid)
        db.session.commit()
        _recalcular_todos_proyectos(eid)
        return jsonify({
            'mensaje': 'DTE emitido y movimiento registrado',
            'dte': dte,
            'movimiento_id': mov.id,
            'tipo_contable': 'Ingreso' if data.get('pagado') else 'Por cobrar',
        }), 201
    except SIIIntegrationError as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), _http_status_sii_error(str(e))
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/uf', methods=['GET', 'POST'])
def manejar_uf():
    try:
        if request.method == 'POST':
            data = request.json or {}
            if not data.get('fecha') or data.get('valor') is None:
                return jsonify({'error': 'Campos requeridos: fecha, valor'}), 400
            fecha = _parse_fecha(data['fecha'])
            if not fecha:
                return jsonify({'error': 'fecha inválida (use YYYY-MM-DD)'}), 400
            valor = float(data['valor'])
            if valor <= 0:
                return jsonify({'error': 'valor debe ser mayor a 0'}), 400
            reg = _guardar_uf(fecha, valor)
            return jsonify({'mensaje': 'UF guardada', 'uf': _valor_uf_a_dict(reg)}), 201

        fecha_param = request.args.get('fecha')
        if fecha_param:
            fecha = _parse_fecha(fecha_param)
            if not fecha:
                return jsonify({'error': 'fecha inválida (use YYYY-MM-DD)'}), 400
            valor, fecha_usada, _ = _obtener_uf_para_fecha(fecha, auto_fetch=True)
            if valor is None:
                return jsonify({'error': f'Sin valor UF para {fecha_param}'}), 404
            return jsonify({
                'fecha': fecha_usada.strftime('%Y-%m-%d'),
                'valor': valor,
                'solicitada': fecha_param,
            })

        registros = ValorUF.query.order_by(ValorUF.fecha.desc()).all()
        return jsonify([_valor_uf_a_dict(v) for v in registros])
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/uf/hoy', methods=['GET'])
def uf_hoy():
    try:
        return jsonify(_uf_hoy())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/uf/<int:uf_id>', methods=['PUT', 'DELETE'])
def manejar_uf_id(uf_id):
    reg = ValorUF.query.get_or_404(uf_id)
    try:
        if request.method == 'DELETE':
            db.session.delete(reg)
            db.session.commit()
            return jsonify({'mensaje': 'UF eliminada'})

        data = request.json or {}
        if data.get('fecha'):
            reg.fecha = _parse_fecha(data['fecha'])
        if data.get('valor') is not None:
            reg.valor = float(data['valor'])
        db.session.commit()
        return jsonify({'mensaje': 'UF actualizada', 'uf': _valor_uf_a_dict(reg)})
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


