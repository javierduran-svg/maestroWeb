"""API de contabilidad (plan de cuentas, centros de costo, comprobantes)."""

from flask import Blueprint, jsonify, request

from common import _requiere_empresa, _es_admin
from extensions import db
from models import CentroCosto, Comprobante, CuentaContable

_TIPOS_CUENTA = frozenset({'Activo', 'Pasivo', 'Patrimonio', 'Ingreso', 'Costo', 'Egreso'})

bp = Blueprint('contabilidad', __name__)


@bp.before_request
def _contabilidad_requiere_admin():
    if request.method == 'OPTIONS':
        return None
    if not _es_admin():
        return jsonify({'error': 'Acceso restringido a administradores'}), 403
    return None


def _cuenta_a_dict(cuenta: CuentaContable) -> dict:
    return {
        'id': cuenta.id,
        'codigo': cuenta.codigo,
        'nombre': cuenta.nombre,
        'tipo': cuenta.tipo,
        'es_imputable': cuenta.es_imputable,
        'id_padre': cuenta.id_padre,
        'activa': cuenta.activa,
        'clasificacion_sii': cuenta.clasificacion_sii,
    }


def _centro_a_dict(centro: CentroCosto) -> dict:
    return {
        'id': centro.id,
        'codigo': centro.codigo,
        'nombre': centro.nombre,
        'activo': centro.activo,
    }


def _comprobante_a_dict(comp: Comprobante) -> dict:
    return {
        'id': comp.id,
        'fecha': comp.fecha.isoformat() if comp.fecha else None,
        'tipo': comp.tipo,
        'numero': comp.numero,
        'numero_formateado': comp.numero_formateado,
        'anio': comp.anio,
        'glosa': comp.glosa,
        'estado': comp.estado,
        'moneda_origen': comp.moneda_origen,
        'tipo_cambio': comp.tipo_cambio,
        'created_at': comp.created_at.isoformat() if comp.created_at else None,
    }


@bp.route('/api/contabilidad/cuentas', methods=['GET', 'POST'])
def manejar_cuentas_contables():
    eid, err = _requiere_empresa()
    if err:
        return err

    if request.method == 'POST':
        data = request.json or {}
        codigo = str(data.get('codigo', '')).strip()
        nombre = str(data.get('nombre', '')).strip()
        tipo = str(data.get('tipo', '')).strip()
        if not codigo or not nombre or not tipo:
            return jsonify({'error': 'codigo, nombre y tipo son obligatorios'}), 400
        if tipo not in _TIPOS_CUENTA:
            return jsonify({'error': f'tipo inválido; use uno de: {", ".join(sorted(_TIPOS_CUENTA))}'}), 400
        if CuentaContable.query.filter_by(empresa_id=eid, codigo=codigo).first():
            return jsonify({'error': 'Ya existe una cuenta con ese código'}), 400

        id_padre = data.get('id_padre')
        if id_padre is not None:
            padre = CuentaContable.query.filter_by(empresa_id=eid, id=id_padre).first()
            if padre is None:
                return jsonify({'error': 'Cuenta padre no encontrada en esta empresa'}), 400
            if padre.tipo != tipo:
                return jsonify({'error': 'El tipo debe coincidir con la cuenta padre'}), 400

        clasificacion_sii = data.get('clasificacion_sii')
        if clasificacion_sii is not None:
            clasificacion_sii = str(clasificacion_sii).strip()[:50] or None

        cuenta = CuentaContable(
            empresa_id=eid,
            codigo=codigo[:50],
            nombre=nombre[:150],
            tipo=tipo,
            id_padre=id_padre,
            es_imputable=bool(data.get('es_imputable', True)),
            activa=bool(data.get('activa', True)),
            clasificacion_sii=clasificacion_sii,
        )
        db.session.add(cuenta)
        db.session.commit()
        return jsonify({'mensaje': 'Cuenta contable creada', 'cuenta': _cuenta_a_dict(cuenta)}), 201

    cuentas = (
        CuentaContable.query
        .filter_by(empresa_id=eid)
        .order_by(CuentaContable.codigo)
        .all()
    )
    return jsonify([_cuenta_a_dict(c) for c in cuentas])


@bp.route('/api/contabilidad/cuentas/<int:cuenta_id>', methods=['PUT'])
def actualizar_cuenta_contable(cuenta_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    cuenta = CuentaContable.query.filter_by(empresa_id=eid, id=cuenta_id).first_or_404()
    data = request.json or {}

    if 'nombre' in data:
        nombre = str(data['nombre']).strip()
        if not nombre:
            return jsonify({'error': 'nombre no puede estar vacío'}), 400
        cuenta.nombre = nombre[:150]
    if 'activa' in data:
        cuenta.activa = bool(data['activa'])
    if 'es_imputable' in data:
        cuenta.es_imputable = bool(data['es_imputable'])
    if 'clasificacion_sii' in data:
        val = data.get('clasificacion_sii')
        cuenta.clasificacion_sii = (str(val).strip()[:50] if val else None) or None
    if 'codigo' in data:
        nuevo_codigo = str(data['codigo']).strip()
        if not nuevo_codigo:
            return jsonify({'error': 'codigo no puede estar vacío'}), 400
        if nuevo_codigo != cuenta.codigo:
            if LineaComprobante.query.filter_by(cuenta_contable_id=cuenta.id).first():
                return jsonify({'error': 'No se puede cambiar el código: la cuenta tiene movimientos contables'}), 409
            duplicada = CuentaContable.query.filter_by(
                empresa_id=eid, codigo=nuevo_codigo,
            ).filter(CuentaContable.id != cuenta.id).first()
            if duplicada:
                return jsonify({'error': 'Ya existe una cuenta con ese código'}), 409
            cuenta.codigo = nuevo_codigo[:50]

    db.session.commit()
    return jsonify({'mensaje': 'Cuenta contable actualizada', 'cuenta': _cuenta_a_dict(cuenta)})


@bp.route('/api/contabilidad/centros-costo', methods=['GET'])
def listar_centros_costo():
    eid, err = _requiere_empresa()
    if err:
        return err
    centros = (
        CentroCosto.query
        .filter_by(empresa_id=eid)
        .order_by(CentroCosto.codigo)
        .all()
    )
    return jsonify([_centro_a_dict(c) for c in centros])


@bp.route('/api/contabilidad/comprobantes', methods=['GET'])
def listar_comprobantes():
    eid, err = _requiere_empresa()
    if err:
        return err
    comprobantes = (
        Comprobante.query
        .filter_by(empresa_id=eid)
        .order_by(Comprobante.fecha.desc(), Comprobante.numero.desc())
        .all()
    )
    return jsonify([_comprobante_a_dict(c) for c in comprobantes])
