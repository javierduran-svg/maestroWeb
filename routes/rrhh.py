import json
from datetime import date
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file, abort

from bootstrap import (
    cuentas_remuneracion as _cuentas_remuneracion,
    uf_hoy as _uf_hoy,
    _obtener_uf_para_fecha,
    TIPOS_CONTRATO,
    SISTEMAS_SALUD,
    AFPS,
)
from common import *
from extensions import db
from models import Cuenta, Liquidacion, Trabajador
from pdf_liquidaciones import generar_pdf_liquidacion, generar_pdf_planilla
from previred_integration import PreviredFileGenerator

bp = Blueprint('rrhh', __name__)


@bp.before_request
def _rrhh_requiere_admin():
    """Lista de personal (GET) abierta para asignaciones; resto solo admin."""
    if request.method == 'OPTIONS':
        return None
    if request.path == '/api/personal' and request.method == 'GET':
        return None
    if request.method == 'GET' and request.path.rstrip('/').endswith('/foto'):
        return None
    if request.method == 'GET' and request.path.rstrip('/').endswith('/firma'):
        return None
    if not _es_admin():
        return jsonify({'error': 'Acceso restringido a administradores'}), 403
    return None

@bp.route('/api/personal/costos-hh', methods=['PUT'])
def actualizar_costos_hh():
    """Actualiza factor_overhead y/o costo_hh_manual de uno o varios trabajadores."""
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        data = request.json
        if data is None:
            return jsonify({'error': 'Body JSON requerido'}), 400
        items = data if isinstance(data, list) else [data]
        if not items:
            return jsonify({'error': 'Lista vacía'}), 400

        actualizados = []
        for item in items:
            if not isinstance(item, dict):
                return jsonify({'error': 'Cada ítem debe ser un objeto'}), 400
            tid = item.get('trabajador_id')
            if tid is None:
                return jsonify({'error': 'trabajador_id requerido en cada ítem'}), 400
            trabajador = Trabajador.query.filter_by(empresa_id=eid, id=int(tid)).first()
            if not trabajador:
                return jsonify({'error': f'Trabajador {tid} no encontrado'}), 404
            if 'factor_overhead' in item:
                try:
                    factor = float(item['factor_overhead'])
                except (TypeError, ValueError):
                    return jsonify({'error': 'factor_overhead inválido'}), 400
                if factor <= 0:
                    return jsonify({'error': 'factor_overhead debe ser mayor a 0'}), 400
                trabajador.factor_overhead = factor
            if 'costo_hh_manual' in item:
                raw = item['costo_hh_manual']
                if raw is None or raw == '':
                    trabajador.costo_hh_manual = None
                else:
                    try:
                        manual = float(raw)
                    except (TypeError, ValueError):
                        return jsonify({'error': 'costo_hh_manual inválido'}), 400
                    if manual < 0:
                        return jsonify({'error': 'costo_hh_manual no puede ser negativo'}), 400
                    trabajador.costo_hh_manual = manual
            actualizados.append(_trabajador_a_dict(trabajador))

        db.session.commit()
        return jsonify({
            'mensaje': f'{len(actualizados)} trabajador(es) actualizado(s)',
            'trabajadores': actualizados,
        })
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal', methods=['GET', 'POST'])
def manejar_personal():
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        if request.method == 'POST':
            data = request.json or {}
            campos, error = _validar_datos_trabajador(data, eid)
            if error:
                return jsonify({'error': error}), 400

            nuevo = Trabajador(empresa_id=eid, **campos)
            if 'rol' in data:
                if not _es_admin():
                    return jsonify({'error': 'Solo administradores pueden asignar roles'}), 403
                nuevo_rol = str(data.get('rol') or '').strip().lower()
                if nuevo_rol not in ('admin', 'trabajador'):
                    return jsonify({'error': "rol inválido; use 'admin' o 'trabajador'"}), 400
                nuevo.rol = nuevo_rol
            pwd_err = _aplicar_password_trabajador(nuevo, data, es_nuevo=True)
            if pwd_err:
                return jsonify({'error': pwd_err}), 400
            db.session.add(nuevo)
            db.session.commit()
            return jsonify({'mensaje': 'Trabajador creado', 'trabajador': _trabajador_a_dict(nuevo)}), 201

        trabajadores = Trabajador.query.filter_by(empresa_id=eid).order_by(
            Trabajador.apellido_paterno, Trabajador.nombres,
        ).all()
        return jsonify({
            'trabajadores': [_trabajador_a_dict(t) for t in trabajadores],
            'cuentas_remuneracion': [
                {'id': c.id, 'nombre': c.nombre}
                for c in _cuentas_remuneracion(eid)
            ],
            'tipos_contrato': TIPOS_CONTRATO,
            'sistemas_salud': SISTEMAS_SALUD,
            'afps': AFPS,
        })
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/<int:trabajador_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_trabajador(trabajador_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = Trabajador.query.filter_by(empresa_id=eid, id=trabajador_id).first_or_404()
    try:
        if request.method == 'GET':
            return jsonify(_trabajador_a_dict(trabajador))

        if request.method == 'DELETE':
            pagadas = Liquidacion.query.filter_by(
                empresa_id=eid, trabajador_id=trabajador_id, estado='Pagado',
            ).count()
            if pagadas:
                return jsonify({
                    'error': 'No se puede eliminar: tiene liquidaciones en estado Pagado',
                }), 400
            Liquidacion.query.filter_by(empresa_id=eid, trabajador_id=trabajador_id).delete()
            _eliminar_foto_trabajador(trabajador)
            db.session.delete(trabajador)
            db.session.commit()
            return jsonify({'mensaje': 'Trabajador eliminado'})

        data = request.json or {}
        if 'rol' in data:
            if not _es_admin():
                return jsonify({'error': 'Solo administradores pueden cambiar roles'}), 403
            nuevo_rol = str(data.get('rol') or '').strip().lower()
            if nuevo_rol not in ('admin', 'trabajador'):
                return jsonify({'error': "rol inválido; use 'admin' o 'trabajador'"}), 400
            trabajador.rol = nuevo_rol
        campos, error = _validar_datos_trabajador(data, eid, trabajador_id=trabajador_id)
        if error:
            return jsonify({'error': error}), 400
        _aplicar_datos_trabajador(trabajador, campos)
        pwd_err = _aplicar_password_trabajador(trabajador, data, es_nuevo=False)
        if pwd_err:
            return jsonify({'error': pwd_err}), 400
        db.session.commit()
        return jsonify({'mensaje': 'Trabajador actualizado', 'trabajador': _trabajador_a_dict(trabajador)})
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/<int:trabajador_id>/foto', methods=['GET', 'POST', 'DELETE'])
def manejar_foto_trabajador(trabajador_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = Trabajador.query.filter_by(empresa_id=eid, id=trabajador_id).first_or_404()

    if request.method == 'GET':
        path = _foto_path(trabajador)
        if not path:
            if trabajador.foto_path:
                trabajador.foto_path = None
                db.session.commit()
            abort(404)
        return send_file(path, mimetype='image/jpeg')

    if request.method == 'DELETE':
        if trabajador.foto_path:
            _eliminar_foto_trabajador(trabajador)
            db.session.commit()
        return jsonify({'mensaje': 'Foto eliminada', 'trabajador': _trabajador_a_dict(trabajador)})

    archivo = request.files.get('foto')
    _, error = _guardar_foto_trabajador(trabajador, archivo)
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'mensaje': 'Foto actualizada', 'trabajador': _trabajador_a_dict(trabajador)})


@bp.route('/api/personal/<int:trabajador_id>/firma', methods=['GET', 'POST', 'DELETE'])
def manejar_firma_trabajador(trabajador_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    trabajador = Trabajador.query.filter_by(empresa_id=eid, id=trabajador_id).first_or_404()

    if request.method == 'GET':
        path = _firma_path(trabajador)
        if not path:
            if trabajador.firma_path:
                trabajador.firma_path = None
                db.session.commit()
            abort(404)
        return send_file(path, mimetype=_firma_mimetype(trabajador))

    if request.method == 'DELETE':
        if trabajador.firma_path:
            _eliminar_firma_trabajador(trabajador)
            db.session.commit()
        return jsonify({'mensaje': 'Firma eliminada', 'trabajador': _trabajador_a_dict(trabajador)})

    archivo = request.files.get('firma')
    _, error = _guardar_firma_trabajador(trabajador, archivo)
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'mensaje': 'Firma actualizada', 'trabajador': _trabajador_a_dict(trabajador)})


@bp.route('/api/personal/importar-excel', methods=['POST'])
def importar_personal_excel():
    """Importa trabajadores desde hoja RRHH (fila 11+) u otra hoja de personal del Excel maestro."""
    from importar_excel import importar_trabajadores_desde_excel, DEFAULT_XLSX
    eid, err = _requiere_empresa()
    if err:
        return err
    path = request.json.get('archivo') if request.is_json and request.json else str(DEFAULT_XLSX)
    actualizar = request.args.get('actualizar', '0') == '1'
    data = request.json if request.is_json and request.json else {}
    mes = data.get('mes')
    anio = data.get('anio')
    try:
        stats = importar_trabajadores_desde_excel(
            path,
            actualizar=actualizar,
            mes=int(mes) if mes is not None else None,
            anio=int(anio) if anio is not None else None,
            empresa_id=eid,
        )
        return jsonify(stats)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/liquidaciones/generar', methods=['POST'])
def generar_liquidaciones():
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        data = request.json or {}
        mes = int(data.get('mes', 0))
        anio = int(data.get('anio', 0))
        if not (1 <= mes <= 12) or anio < 2000:
            return jsonify({'error': 'mes (1-12) y anio válidos son requeridos'}), 400

        trabajadores = Trabajador.query.filter_by(empresa_id=eid).all()
        if not trabajadores:
            return jsonify({'error': 'No hay trabajadores registrados'}), 400

        fecha_uf = date.today()
        uf_clp, fecha_uf_usada, _ = _obtener_uf_para_fecha(fecha_uf, auto_fetch=True)
        if uf_clp is None:
            return jsonify({
                'error': (
                    f'No hay valor UF para hoy ({fecha_uf.strftime("%d/%m/%Y")}). '
                    'Regístrelo con POST /api/uf o verifique conexión a mindicador.cl / sii.cl.'
                ),
            }), 400

        generadas = []
        for t in trabajadores:
            dias = _dias_trabajados_mes(t, mes, anio)
            if dias <= 0:
                continue

            montos = _calcular_montos_liquidacion(t, dias, uf_clp)
            detalle_json = json.dumps(montos['detalle'], ensure_ascii=False)
            campos_extra = {
                'detalle_calculo': detalle_json,
            }
            existente = Liquidacion.query.filter_by(
                empresa_id=eid, trabajador_id=t.id, mes=mes, anio=anio,
            ).first()

            if existente:
                if existente.estado == 'Pagado':
                    generadas.append(_liquidacion_a_dict(existente))
                    continue
                existente.dias_trabajados = montos['dias_trabajados']
                existente.sueldo_base_proporcional = montos['sueldo_base_proporcional']
                existente.total_imponible = montos['total_imponible']
                existente.total_haberes = montos['total_haberes']
                existente.total_descuentos = montos['total_descuentos']
                existente.alcance_liquido = montos['alcance_liquido']
                existente.uf_valor = montos['uf_valor']
                existente.sueldo_base_uf = montos['sueldo_base_uf']
                for k, v in campos_extra.items():
                    setattr(existente, k, v)
                liq = existente
            else:
                liq = Liquidacion(
                    empresa_id=eid,
                    trabajador_id=t.id,
                    mes=mes,
                    anio=anio,
                    estado='Borrador',
                    **{k: v for k, v in montos.items() if k != 'detalle'},
                    **campos_extra,
                )
                db.session.add(liq)

            generadas.append(_liquidacion_a_dict(liq))

        db.session.commit()
        return jsonify({
            'mensaje': f'Planilla generada para {mes:02d}/{anio}',
            'mes': mes,
            'anio': anio,
            'liquidaciones': generadas,
            'uf_clp': uf_clp,
            'uf_fecha': fecha_uf_usada.strftime('%Y-%m-%d'),
            'uf_fecha_referencia': fecha_uf.strftime('%Y-%m-%d'),
        }), 201
    except (ValueError, TypeError) as e:
        db.session.rollback()
        return jsonify({'error': f'Datos inválidos: {e}'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/liquidaciones/<int:mes>/<int:anio>', methods=['GET'])
def obtener_liquidaciones(mes, anio):
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        if not (1 <= mes <= 12):
            return jsonify({'error': 'mes debe estar entre 1 y 12'}), 400

        liquidaciones = Liquidacion.query.filter_by(empresa_id=eid, mes=mes, anio=anio).all()
        liquidaciones.sort(key=lambda l: (
            l.trabajador_rel.apellido_paterno if l.trabajador_rel else '',
            l.trabajador_rel.nombres if l.trabajador_rel else '',
        ))
        uf_info = _uf_hoy()
        return jsonify({
            'mes': mes,
            'anio': anio,
            'liquidaciones': [_liquidacion_a_dict(l) for l in liquidaciones],
            'totales': {
                'haberes': sum(l.total_haberes for l in liquidaciones),
                'descuentos': sum(l.total_descuentos for l in liquidaciones),
                'liquido': sum(l.alcance_liquido for l in liquidaciones),
            },
            'uf_clp': uf_info['valor'],
            'uf_fecha': uf_info['fecha'],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/liquidaciones/id/<int:liq_id>/pdf', methods=['GET'])
def pdf_liquidacion_individual(liq_id):
    try:
        eid = _empresa_id_request(required=False) or request.args.get('empresa_id', type=int)
        if not eid:
            return jsonify({'error': 'X-Empresa-Id requerido'}), 400
        liq = Liquidacion.query.filter_by(empresa_id=eid, id=liq_id).first_or_404()
        payload = _liquidacion_pdf_payload(liq, empresa_id=eid)
        pdf_bytes = generar_pdf_liquidacion(payload)
        nombre = f"liquidacion_{liq.trabajador_rel.rut if liq.trabajador_rel else liq_id}_{liq.mes:02d}_{liq.anio}.pdf"
        return send_file(
            BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=nombre.replace('/', '-'),
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/liquidaciones/<int:mes>/<int:anio>/pdf', methods=['GET'])
def pdf_liquidaciones_planilla(mes, anio):
    try:
        eid = _empresa_id_request(required=False) or request.args.get('empresa_id', type=int)
        if not eid:
            return jsonify({'error': 'X-Empresa-Id requerido'}), 400
        if not (1 <= mes <= 12):
            return jsonify({'error': 'mes debe estar entre 1 y 12'}), 400

        liquidaciones = Liquidacion.query.filter_by(empresa_id=eid, mes=mes, anio=anio).all()
        liquidaciones.sort(key=lambda l: (
            l.trabajador_rel.apellido_paterno if l.trabajador_rel else '',
            l.trabajador_rel.nombres if l.trabajador_rel else '',
        ))
        if not liquidaciones:
            return jsonify({'error': 'No hay liquidaciones para este período'}), 404

        empresa_id = eid
        payloads = [_liquidacion_pdf_payload(l, empresa_id=empresa_id) for l in liquidaciones]
        pdf_bytes = generar_pdf_planilla(payloads, mes, anio)
        return send_file(
            BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'planilla_sueldos_{mes:02d}_{anio}.pdf',
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/personal/planilla/<int:mes>/<int:anio>/movimientos', methods=['POST'])
def ingresar_planilla_movimientos(mes, anio):
    try:
        eid, err = _requiere_empresa()
        if err:
            return err
        if not (1 <= mes <= 12):
            return jsonify({'error': 'mes debe estar entre 1 y 12'}), 400

        resultado = _ingresar_liquidaciones_a_movimientos(eid, mes, anio)
        if (
            resultado['insertados'] == 0
            and resultado['omitidos'] == 0
            and not resultado['errores']
            and resultado['mensaje'] == 'No hay liquidaciones para el período'
        ):
            return jsonify({'error': resultado['mensaje']}), 404
        return jsonify(resultado)
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.route('/api/previred/descargar/<int:mes>/<int:anio>', methods=['GET'])
def descargar_previred(mes, anio):
    try:
        eid = _empresa_id_request(required=False) or request.args.get('empresa_id', type=int)
        if not eid:
            return jsonify({'error': 'X-Empresa-Id requerido'}), 400
        if not (1 <= mes <= 12):
            return jsonify({'error': 'mes debe estar entre 1 y 12'}), 400

        liquidaciones = Liquidacion.query.filter_by(empresa_id=eid, mes=mes, anio=anio).all()
        generador = PreviredFileGenerator()
        contenido = generador.generar_txt(mes, anio, liquidaciones)
        nombre = generador.nombre_archivo(mes, anio)
        return send_file(
            BytesIO(contenido.encode('utf-8')),
            mimetype='text/plain; charset=utf-8',
            as_attachment=True,
            download_name=nombre,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        _migrar_schema()
    app.run(debug=True, port=5000)

