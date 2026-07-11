from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, abort, session
from werkzeug.utils import secure_filename

from bootstrap import (
    empresa_default_id as _empresa_default_id,
    sembrar_cuentas_empresa as _sembrar_cuentas_empresa,
)
from services.plan_cuentas_seed import PLAN_CUENTAS_TEMPLATES, sembrar_plan_cuentas
from common import *
from extensions import db
from models import (
    Cliente, Cuenta, Empresa, EmpresaBancoConexion, EmpresaSIIConfig, Propuesta, Trabajador,
)

bp = Blueprint('configuracion', __name__)

_CONFIGURACION_ADMIN_PATH_MARKERS = (
    '/credenciales/sii',
    '/bancos',
)


@bp.before_request
def _configuracion_requiere_admin():
    """SII y conexiones bancarias solo para administradores."""
    path = request.path
    if path.startswith('/api/bancos/'):
        if not _es_admin():
            return jsonify({'error': 'Acceso restringido a administradores'}), 403
        return None
    if any(marker in path for marker in _CONFIGURACION_ADMIN_PATH_MARKERS):
        if not _es_admin():
            return jsonify({'error': 'Acceso restringido a administradores'}), 403
    return None

@bp.route('/')
def index():
    # app.html es una SPA con marcadores literales de cliente ({{PROYECTO}},
    # {{...}}, etc.). NO debe pasar por Jinja: se sirve como archivo estático
    # para no romper esos marcadores ni provocar TemplateSyntaxError.
    return send_file(Path(current_app.root_path) / 'app.html')


@bp.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.json or {}
    email = _normalizar_email(data.get('email'))
    password = data.get('password') or ''
    if not email or not password:
        return jsonify({'error': 'Email y contraseña requeridos'}), 400

    trabajador = Trabajador.query.filter(db.func.lower(Trabajador.email) == email).first()
    if not trabajador or not _verificar_password(trabajador, password):
        return jsonify({'error': 'Credenciales inválidas'}), 401

    _establecer_sesion_trabajador(trabajador)
    return jsonify({'mensaje': 'Sesión iniciada', 'usuario': _trabajador_auth_dict(trabajador)})


@bp.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'mensaje': 'Sesión cerrada'})


@bp.route('/api/auth/me', methods=['GET'])
def auth_me():
    trabajador = _usuario_sesion()
    if not trabajador:
        return jsonify({'error': 'No autenticado'}), 401
    return jsonify(_trabajador_auth_dict(trabajador))


@bp.route('/api/auth/needs-setup', methods=['GET'])
def auth_needs_setup():
    """Indica si aún no hay usuarios con credenciales (primer acceso)."""
    necesita = _trabajadores_con_login() == 0
    trabajadores = []
    empresa_id = None
    empresa_nombre = None
    if necesita:
        empresa_id = _empresa_default_id()
        empresa = Empresa.query.get(empresa_id)
        empresa_nombre = empresa.nombre if empresa else None
        _intentar_importar_trabajadores_setup(empresa_id)
        trabajadores = _trabajadores_setup_dicts(empresa_id)
    return jsonify({
        'needs_setup': necesita,
        'trabajadores': trabajadores,
        'empresa_id': empresa_id,
        'empresa_nombre': empresa_nombre,
        'puede_crear_trabajador': necesita and len(trabajadores) == 0,
    })


@bp.route('/api/auth/setup-first', methods=['POST'])
def auth_setup_first():
    """Crea credenciales del primer usuario cuando no existe ninguno."""
    if _trabajadores_con_login() > 0:
        return jsonify({'error': 'Ya existen usuarios con acceso'}), 403

    data = request.json or {}
    email = _normalizar_email(data.get('email'))
    password = data.get('password') or ''
    trabajador_id = data.get('trabajador_id')

    if not email or not password:
        return jsonify({'error': 'Email y contraseña requeridos'}), 400
    if len(password) < 6:
        return jsonify({'error': 'La contraseña debe tener al menos 6 caracteres'}), 400

    if Trabajador.query.filter(db.func.lower(Trabajador.email) == email).first():
        return jsonify({'error': 'Ya existe un trabajador con ese email'}), 400

    if trabajador_id:
        trabajador = Trabajador.query.get(int(trabajador_id))
        if not trabajador:
            return jsonify({'error': 'Trabajador no encontrado'}), 404
    else:
        empresa_id = _empresa_default_id()
        if Trabajador.query.filter_by(empresa_id=empresa_id).count() > 0:
            return jsonify({'error': 'Seleccione un trabajador para asignar acceso'}), 400
        trabajador, crear_err = _crear_trabajador_setup_minimo(empresa_id, data)
        if crear_err:
            return jsonify({'error': crear_err}), 400
        db.session.add(trabajador)
        db.session.flush()

    trabajador.email = email
    trabajador.password_hash = _hash_password(password)
    if not trabajador.rol or trabajador.rol == 'trabajador':
        trabajador.rol = 'admin'
    db.session.commit()

    _establecer_sesion_trabajador(trabajador)
    return jsonify({
        'mensaje': 'Acceso configurado',
        'usuario': _trabajador_auth_dict(trabajador),
    }), 201


def _normalizar_plan_cuentas_template(raw) -> str:
    template = (raw or 'sociedad_profesionales').strip()
    if template not in PLAN_CUENTAS_TEMPLATES:
        raise ValueError(
            f'plan_cuentas_template inválido; use uno de: {", ".join(sorted(PLAN_CUENTAS_TEMPLATES))}',
        )
    return template


@bp.route('/api/setup', methods=['GET', 'POST'])
def setup_db():
    reset = request.args.get('reset') == '1'
    if reset:
        db.drop_all()
    _migrar_schema()

    default_id = _empresa_default_id()
    empresa = Empresa.query.get(default_id)
    if request.method == 'POST':
        data = request.json or {}
        if 'plan_cuentas_template' in data and empresa is not None:
            try:
                empresa.plan_cuentas_template = _normalizar_plan_cuentas_template(
                    data.get('plan_cuentas_template'),
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400

    template = empresa.plan_cuentas_template if empresa else 'sociedad_profesionales'
    _sembrar_cuentas_empresa(default_id)
    sembrar_plan_cuentas(default_id, template)

    if not Cliente.query.filter_by(empresa_id=default_id).first():
        db.session.add(Cliente(
            empresa_id=default_id,
            razon_social='Inmobiliaria Bosque Real',
            rut='77.666.555-1',
            comentarios='Cliente VIP',
        ))

    db.session.commit()

    propuestas_count = Propuesta.query.filter_by(empresa_id=default_id).count()
    if propuestas_count == 0:
        try:
            from importar_excel import importar_propuestas_desde_excel, DEFAULT_XLSX
            if DEFAULT_XLSX.exists():
                importar_propuestas_desde_excel(DEFAULT_XLSX, actualizar=False, empresa_id=default_id)
                propuestas_count = Propuesta.query.filter_by(empresa_id=default_id).count()
        except Exception:
            pass

    trabajadores_count = Trabajador.query.filter_by(empresa_id=default_id).count()
    if trabajadores_count == 0:
        _intentar_importar_trabajadores_setup(default_id)
        trabajadores_count = Trabajador.query.filter_by(empresa_id=default_id).count()

    return jsonify({
        'mensaje': 'Base de datos lista' + (' (reiniciada)' if reset else ''),
        'cuentas': Cuenta.query.filter_by(empresa_id=default_id).count(),
        'servicios': SERVICIOS,
        'propuestas': propuestas_count,
        'trabajadores': trabajadores_count,
    })


@bp.route('/api/empresas/<int:empresa_id>/credenciales/sii', methods=['GET', 'PUT'])
def manejar_credenciales_sii(empresa_id):
    Empresa.query.get_or_404(empresa_id)
    eid, err = _requiere_empresa()
    if err:
        return err
    if eid != empresa_id:
        return jsonify({'error': 'Empresa no coincide con X-Empresa-Id'}), 403

    cfg = _obtener_o_crear_sii_config(empresa_id)

    if request.method == 'GET':
        return jsonify(_sii_config_a_dict(cfg, masked=True))

    data = request.json or {}
    for campo in ('rut_emisor', 'usuario', 'rut_certificado', 'certificado_path', 'rcv_base_url', 'api_base_url'):
        if campo in data:
            setattr(cfg, campo, (data.get(campo) or '').strip() or None)
    if 'ambiente' in data:
        try:
            cfg.ambiente = int(data['ambiente'])
        except (TypeError, ValueError):
            return jsonify({'error': 'ambiente inválido (0 o 1)'}), 400
    _aplicar_campos_secretos(data, cfg, ('api_key', 'password', 'certificado_password', 'certificado_b64'))
    cfg.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'mensaje': 'Credenciales SII actualizadas', 'config': _sii_config_a_dict(cfg, masked=True)})


@bp.route('/api/empresas/<int:empresa_id>/credenciales/sii/certificado', methods=['POST'])
def subir_certificado_sii(empresa_id):
    Empresa.query.get_or_404(empresa_id)
    eid, err = _requiere_empresa()
    if err:
        return err
    if eid != empresa_id:
        return jsonify({'error': 'Empresa no coincide con X-Empresa-Id'}), 403
    archivo = request.files.get('certificado')
    if not archivo or not archivo.filename:
        return jsonify({'error': 'No se recibió archivo de certificado'}), 400
    ext = Path(secure_filename(archivo.filename)).suffix.lower()
    if ext not in CERTIFICADO_ALLOWED_EXT:
        return jsonify({'error': 'Formato inválido. Use .pfx o .p12'}), 400
    contenido = archivo.read()
    if len(contenido) > CERTIFICADO_MAX_BYTES:
        return jsonify({'error': 'Archivo demasiado grande (máx. 5 MB)'}), 400
    _asegurar_certificados_dir()
    nombre = f'empresa_{empresa_id}_cert{ext}'
    path = CERTIFICADOS_DIR / nombre
    path.write_bytes(contenido)
    cfg = _obtener_o_crear_sii_config(empresa_id)
    cfg.certificado_path = str(path)
    cfg.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'mensaje': 'Certificado guardado', 'certificado_path': cfg.certificado_path})


@bp.route('/api/empresas/<int:empresa_id>/bancos', methods=['GET', 'POST'])
def manejar_bancos_empresa(empresa_id):
    Empresa.query.get_or_404(empresa_id)
    eid, err = _requiere_empresa()
    if err:
        return err
    if eid != empresa_id:
        return jsonify({'error': 'Empresa no coincide con X-Empresa-Id'}), 403

    if request.method == 'GET':
        conexiones = EmpresaBancoConexion.query.filter_by(empresa_id=empresa_id).order_by(
            EmpresaBancoConexion.nombre,
        ).all()
        return jsonify([_banco_conexion_a_dict(c) for c in conexiones])

    data = request.json or {}
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return jsonify({'error': 'El nombre es obligatorio'}), 400
    cuenta_id = data.get('cuenta_contable_id')
    if cuenta_id:
        cuenta = Cuenta.query.filter_by(empresa_id=empresa_id, id=int(cuenta_id)).first()
        if not cuenta:
            return jsonify({'error': 'Cuenta contable no encontrada en esta empresa'}), 400
    else:
        cuenta = _obtener_cuenta_banco_santander(empresa_id)
        cuenta_id = cuenta.id

    conn = EmpresaBancoConexion(
        empresa_id=empresa_id,
        nombre=nombre[:100],
        fintoc_account_id=(data.get('fintoc_account_id') or '').strip() or None,
        cuenta_contable_id=int(cuenta_id),
        activa=bool(data.get('activa', True)),
    )
    _aplicar_campos_secretos(data, conn, ('fintoc_api_key', 'fintoc_link_token'))
    db.session.add(conn)
    db.session.commit()
    return jsonify({'mensaje': 'Conexión bancaria creada', 'banco': _banco_conexion_a_dict(conn)}), 201


@bp.route('/api/bancos/<int:banco_id>', methods=['PUT', 'DELETE'])
def manejar_banco_id(banco_id):
    eid, err = _requiere_empresa()
    if err:
        return err
    conn = EmpresaBancoConexion.query.filter_by(id=banco_id, empresa_id=eid).first_or_404()

    if request.method == 'DELETE':
        db.session.delete(conn)
        db.session.commit()
        return jsonify({'mensaje': 'Conexión bancaria eliminada'})

    data = request.json or {}
    if 'nombre' in data:
        nombre = (data.get('nombre') or '').strip()
        if not nombre:
            return jsonify({'error': 'El nombre no puede estar vacío'}), 400
        conn.nombre = nombre[:100]
    if 'fintoc_account_id' in data:
        conn.fintoc_account_id = (data.get('fintoc_account_id') or '').strip() or None
    if 'cuenta_contable_id' in data:
        cid = data.get('cuenta_contable_id')
        if cid:
            cuenta = Cuenta.query.filter_by(empresa_id=eid, id=int(cid)).first()
            if not cuenta:
                return jsonify({'error': 'Cuenta contable no encontrada'}), 400
            conn.cuenta_contable_id = cuenta.id
        else:
            conn.cuenta_contable_id = None
    if 'activa' in data:
        conn.activa = bool(data['activa'])
    _aplicar_campos_secretos(data, conn, ('fintoc_api_key', 'fintoc_link_token'))
    db.session.commit()
    return jsonify({'mensaje': 'Conexión bancaria actualizada', 'banco': _banco_conexion_a_dict(conn)})


@bp.route('/api/empresas', methods=['GET', 'POST'])
def manejar_empresas():
    if request.method == 'POST':
        data = request.json or {}
        rut = str(data.get('rut', '')).strip()
        nombre = str(data.get('nombre', '')).strip()
        if not rut or not nombre:
            return jsonify({'error': 'RUT y nombre son obligatorios'}), 400
        if Empresa.query.filter_by(rut=rut[:20]).first():
            return jsonify({'error': 'Ya existe una empresa con ese RUT'}), 400
        try:
            plan_template = _normalizar_plan_cuentas_template(data.get('plan_cuentas_template'))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        nueva = Empresa(
            rut=rut[:20],
            nombre=nombre[:150],
            direccion=(data.get('direccion') or '').strip()[:255] or None,
            email=(data.get('email') or '').strip()[:120] or None,
            telefono=(data.get('telefono') or '').strip()[:30] or None,
            giro=(data.get('giro') or '').strip()[:150] or None,
            activa=bool(data.get('activa', True)),
            plan_cuentas_template=plan_template,
        )
        db.session.add(nueva)
        db.session.flush()
        _sembrar_cuentas_empresa(nueva.id)
        sembrar_plan_cuentas(nueva.id, plan_template)
        db.session.commit()
        return jsonify({'id': nueva.id, 'mensaje': 'Empresa creada', 'empresa': _empresa_a_dict(nueva)}), 201

    return jsonify([_empresa_a_dict(e) for e in Empresa.query.order_by(Empresa.nombre).all()])


@bp.route('/api/empresas/<int:empresa_id>', methods=['GET', 'PUT', 'DELETE'])
def manejar_empresa(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    if request.method == 'GET':
        return jsonify(_empresa_a_dict(empresa))

    if request.method == 'DELETE':
        try:
            resultado = _eliminar_empresa(empresa_id)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 409
        return jsonify(resultado)

    data = request.json or {}
    if 'rut' in data:
        rut = str(data['rut']).strip()[:20]
        duplicado = Empresa.query.filter(Empresa.rut == rut, Empresa.id != empresa_id).first()
        if duplicado:
            return jsonify({'error': 'Ya existe otra empresa con ese RUT'}), 400
        empresa.rut = rut
    if 'nombre' in data:
        nombre = str(data['nombre']).strip()
        if not nombre:
            return jsonify({'error': 'El nombre no puede estar vacío'}), 400
        empresa.nombre = nombre[:150]
    if 'direccion' in data:
        empresa.direccion = (data.get('direccion') or '').strip()[:255] or None
    if 'email' in data:
        empresa.email = (data.get('email') or '').strip()[:120] or None
    if 'telefono' in data:
        empresa.telefono = (data.get('telefono') or '').strip()[:30] or None
    if 'giro' in data:
        empresa.giro = (data.get('giro') or '').strip()[:150] or None
    if 'activa' in data:
        empresa.activa = bool(data['activa'])
    db.session.commit()
    return jsonify({'mensaje': 'Empresa actualizada', 'empresa': _empresa_a_dict(empresa)})


@bp.route('/api/empresas/<int:empresa_id>/logo', methods=['GET', 'POST', 'DELETE'])
def manejar_logo_empresa(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    if request.method == 'GET':
        path = _logo_path(empresa)
        if not path:
            abort(404)
        return send_file(path)

    if request.method == 'DELETE':
        if empresa.logo_filename:
            path = LOGOS_DIR / empresa.logo_filename
            if path.is_file():
                path.unlink()
            empresa.logo_filename = None
            db.session.commit()
        return jsonify({'mensaje': 'Logo eliminado', 'empresa': _empresa_a_dict(empresa)})

    archivo = request.files.get('logo')
    _, error = _guardar_logo_empresa(empresa, archivo)
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'mensaje': 'Logo actualizado', 'empresa': _empresa_a_dict(empresa)})


