"""Shared constants and API helpers (extracted from app.py)."""
import os
from pathlib import Path
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

from io import BytesIO
import json

from flask import jsonify, request, send_file, abort, session, has_request_context
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import cast, func, String, text, inspect, or_
from sqlalchemy.orm import aliased
import logging
from datetime import datetime, date, timedelta
import calendar

import re

import requests

from contabilidad import (
    calcular_transaccion,
    calcular_flujo_financiero,
    calcular_balance_cuenta,
    recalcular_proyecto,
    calcular_kpis,
    calcular_series_dashboard,
    generar_eventos_calendario_contable,
    PERIODOS_DASHBOARD,
    STATUS_GASTO_PROGRAMADO,
)
from sii_integration import SIIClient, SIIIntegrationError
from pdf_liquidaciones import generar_pdf_liquidacion, generar_pdf_planilla
from previred_integration import PreviredFileGenerator
from banco_integration import FintocClient, BancoIntegrationError, mensaje_error_red_fintoc
from extensions import db
from bootstrap import (
    ensure_schema as _ensure_schema_core,
    empresa_default_id as _empresa_default_id,
    sembrar_cuentas_empresa as _sembrar_cuentas_empresa,
    cuentas_remuneracion as _cuentas_remuneracion,
    uf_hoy as _uf_hoy,
    asegurar_empresa_default as _asegurar_empresa_default,
    _obtener_uf_para_fecha,
    _guardar_uf,
    CUENTAS_INICIALES,
    UF_REFERENCIA_CLP,
    NOMBRE_CUENTA_BANCO_PESOS,
    TIPOS_CONTRATO,
    SISTEMAS_SALUD,
    AFPS,
    ESTADOS_PROPUESTA,
    EMPRESA_DEFAULT,
)
from sqlalchemy.exc import IntegrityError

from models import (
    Empresa,
    Cliente,
    Proyecto,
    EntregaProgramada,
    TareaEntrega,
    Propuesta,
    Cuenta,
    Movimiento,
    Trabajador,
    ValorUF,
    Liquidacion,
    EmpresaSIIConfig,
    EmpresaBancoConexion,
    CentroCosto,
    CuentaContable,
    Comprobante,
    RegistroTiempo,
)


def _http_status_sii_error(mensaje: str) -> int:
    """Devuelve 400 para errores de configuración/credenciales y 502 para fallos upstream."""
    texto = mensaje.lower()
    if any(x in texto for x in (
        'configure', 'falta', 'verifique', 'no se encuentra', 'no se pudo cargar',
        'inválido', 'invalido', 'requerid', 'certificado',
    )):
        return 400
    if any(x in texto for x in ('tiempo de espera', 'no se pudo conectar', 'error en la petición')):
        return 502
    if any(x in texto for x in ('límite de consultas', 'quota exceeded')):
        return 429
    return 502


def _cargar_env_local():
    """Lee variables desde .env si existe (no sobreescribe las ya definidas)."""
    env_path = Path(__file__).parent / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        clave, valor = line.split('=', 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env_local()


#   set FLASK_APP=app:app

SERVICIOS = [
    'CEV', 'CES', 'RT', 'Eficiencia energética', 'TDRe', 'VIT',
    'LEED', 'EDGE', 'Certificación Energética', 'Consultoría',
    'CON EE Y CAI', 'ASESOR CER CES', 'ENTIDAD EVALUADORA CER CES',
    'CEV CALIFICACION', 'CALIFICACION',
]
STATUS_PAGO = ['Por enviar', 'Programado', 'Enviado', 'Facturado', 'Pagado', 'Cedida']
STATUS_GASTO = ['', STATUS_GASTO_PROGRAMADO]
ESTADOS_EP_GANTT = ['Pendiente', 'Facturado', 'Pagado']
ESTADOS_ENTREGA = ['Por Hacer', 'Hecho']
ESTADOS_TAREA_ENTREGA = ['Pendiente', 'En proceso', 'Hecho']
NOMBRE_CUENTA_CLIENTES = 'Clientes'
NOMBRE_CUENTA_BANCO_SANTANDER = 'Cta Cte Santander pesos'
NOMBRE_CUENTA_GASTO_BANCO = 'Otros gastos'
NOMBRE_CUENTA_REMUNERACIONES_POR_PAGAR = 'Remuneraciones por pagar'

TASA_AFP = 0.11
TASA_FONASA = 0.07
DIAS_MES_REF = 30
ESTADOS_LIQUIDACION = ['Borrador', 'Pagado']

CATEGORIAS_CUENTA = [
    'activo_cliente',
    'activo_banco',
    'patrimonio_socio',
    'pasivo_factoring',
    'gasto',
    'ingreso',
]
MONEDAS_CUENTA = ('CLP', 'USD')

LOGOS_DIR = Path(__file__).parent / 'uploads' / 'logos'
TRABAJADORES_FOTOS_DIR = Path(__file__).parent / 'uploads' / 'trabajadores'
CERTIFICADOS_DIR = Path(__file__).parent / 'uploads' / 'certificados'
LOGO_MAX_BYTES = 2 * 1024 * 1024
FOTO_MAX_BYTES = 2 * 1024 * 1024
FOTO_MAX_PX = 200
CERTIFICADO_MAX_BYTES = 5 * 1024 * 1024
LOGO_ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.webp'}
FOTO_ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
CERTIFICADO_ALLOWED_EXT = {'.pfx', '.p12'}
SECRET_MASK = '****'


def _parse_fecha(valor):
    if not valor:
        return None
    return datetime.strptime(valor, '%Y-%m-%d').date()


def _parse_condicion_pago(valor):
    try:
        dias = int(valor) if valor is not None else 30
    except (TypeError, ValueError):
        return 30
    return dias if dias in (30, 60, 90) else 30


def _empresa_id_desde_sesion() -> int | None:
    """Empresa del trabajador autenticado (sesión Flask)."""
    raw = session.get('empresa_id')
    if raw is None:
        return None
    try:
        eid = int(raw)
    except (TypeError, ValueError):
        return None
    if not Empresa.query.get(eid):
        return None
    return eid


def _empresa_id_request(required: bool = True) -> int | None:
    """Lee empresa activa desde X-Empresa-Id, ?empresa_id= o sesión."""
    raw = request.headers.get('X-Empresa-Id') or request.args.get('empresa_id')
    if raw is None or str(raw).strip() == '':
        eid = _empresa_id_desde_sesion()
        if eid is not None:
            return eid
        if required:
            return None
        return _empresa_default_id()
    try:
        eid = int(raw)
    except (TypeError, ValueError):
        return _empresa_id_desde_sesion() if required else _empresa_default_id()
    if not Empresa.query.get(eid):
        return _empresa_id_desde_sesion()
    return eid


def _requiere_empresa():
    """Devuelve (empresa_id, None) o (None, response_tuple) para endpoints."""
    eid = _empresa_id_request(required=True)
    if eid is None:
        return None, (jsonify({'error': 'X-Empresa-Id requerido'}), 400)
    return eid, None


AUTH_EXEMPT_PREFIXES = (
    '/api/auth/login',
    '/api/auth/logout',
    '/api/auth/me',
    '/api/auth/needs-setup',
    '/api/auth/setup-first',
    '/api/setup',
)


def _trabajadores_con_login() -> int:
    return Trabajador.query.filter(
        Trabajador.email.isnot(None),
        Trabajador.email != '',
        Trabajador.password_hash.isnot(None),
        Trabajador.password_hash != '',
    ).count()


def _crear_trabajador_setup_minimo(empresa_id: int, data: dict) -> tuple[Trabajador | None, str | None]:
    """Crea trabajador mínimo para primer acceso cuando no hay personal cargado."""
    rut = str(data.get('rut') or '').strip()
    nombres = str(data.get('nombres') or '').strip()
    apellido = str(data.get('apellido_paterno') or data.get('apellido') or '').strip()
    if not rut:
        return None, 'RUT requerido'
    if not nombres:
        return None, 'Nombres requeridos'
    if not apellido:
        return None, 'Apellido paterno requerido'
    if Trabajador.query.filter_by(empresa_id=empresa_id, rut=rut[:20]).first():
        return None, 'Ya existe un trabajador con ese RUT'

    cuentas = _cuentas_remuneracion(empresa_id)
    cuenta = cuentas[0] if cuentas else Cuenta.query.filter_by(empresa_id=empresa_id, categoria='gasto').first()
    if not cuenta:
        return None, 'No hay cuenta de remuneración configurada'

    uf_clp = _uf_hoy()['valor']
    sueldo_base = round(uf_clp) if uf_clp > 0 else round(UF_REFERENCIA_CLP)

    return Trabajador(
        empresa_id=empresa_id,
        rut=rut[:20],
        nombres=nombres[:100],
        apellido_paterno=apellido[:100],
        apellido_materno=(str(data.get('apellido_materno') or '').strip()[:100] or None),
        fecha_ingreso=date.today(),
        tipo_contrato=TIPOS_CONTRATO[0],
        sueldo_base=sueldo_base,
        sueldo_base_uf=1.0,
        afp=AFPS[0],
        sistema_salud=SISTEMAS_SALUD[0],
        valor_plan_isapre_uf=0.0,
        cuenta_gasto_id=cuenta.id,
        rol='admin',
    ), None


def _trabajadores_setup_dicts(empresa_id: int) -> list[dict]:
    return [
        {
            'id': t.id,
            'nombre': _nombre_completo_trabajador(t),
            'rut': t.rut,
            'empresa_id': t.empresa_id,
        }
        for t in Trabajador.query.filter_by(empresa_id=empresa_id).order_by(
            Trabajador.apellido_paterno, Trabajador.nombres,
        ).all()
    ]


def _intentar_importar_trabajadores_setup(empresa_id: int) -> None:
    """Importa trabajadores desde Excel maestro si la tabla está vacía."""
    if Trabajador.query.filter_by(empresa_id=empresa_id).count() > 0:
        return
    try:
        from importar_excel import DEFAULT_XLSX, importar_trabajadores_desde_excel
        if DEFAULT_XLSX.exists():
            importar_trabajadores_desde_excel(DEFAULT_XLSX, actualizar=False, empresa_id=empresa_id)
            db.session.commit()
    except Exception:
        db.session.rollback()


def _usuario_sesion() -> Trabajador | None:
    tid = session.get('trabajador_id')
    if not tid:
        return None
    return Trabajador.query.get(tid)


def _rol_trabajador(t: Trabajador | None = None) -> str:
    """Rol efectivo: 'admin' o 'trabajador'."""
    if t is None:
        t = _usuario_sesion()
    if not t:
        return 'trabajador'
    rol = (getattr(t, 'rol', None) or 'trabajador').strip().lower()
    return rol if rol == 'admin' else 'trabajador'


def _rol_usuario_sesion() -> str:
    """Rol del usuario autenticado (sesión Flask, con respaldo en BD)."""
    rol = session.get('rol')
    if rol in ('admin', 'trabajador'):
        return rol
    rol = _rol_trabajador()
    session['rol'] = rol
    return rol


def _es_admin() -> bool:
    return _rol_usuario_sesion() == 'admin'


def admin_required(f):
    """Decorator: solo administradores (rol admin)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _es_admin():
            return jsonify({'error': 'Acceso restringido a administradores'}), 403
        return f(*args, **kwargs)
    return decorated


def _establecer_sesion_trabajador(trabajador: Trabajador) -> None:
    session.clear()
    session['trabajador_id'] = trabajador.id
    session['empresa_id'] = trabajador.empresa_id
    session['rol'] = _rol_trabajador(trabajador)
    session.permanent = True


def _trabajador_auth_dict(t: Trabajador) -> dict:
    return {
        'id': t.id,
        'trabajador_id': t.id,
        'empresa_id': t.empresa_id,
        'email': t.email or '',
        'nombre': _nombre_completo_trabajador(t),
        'rut': t.rut,
        'rol': _rol_trabajador(t),
        'foto_url': _foto_url(t),
    }


def _normalizar_email(email: str | None) -> str | None:
    if email is None:
        return None
    limpio = str(email).strip().lower()
    return limpio or None


def _hash_password(password: str) -> str:
    return generate_password_hash(password)


def _verificar_password(t: Trabajador, password: str) -> bool:
    if not t.password_hash:
        return False
    return check_password_hash(t.password_hash, password)


def _verificar_empresa(obj, empresa_id: int):
    if obj is None or getattr(obj, 'empresa_id', None) != empresa_id:
        abort(404)


def _migrar_schema_bootstrap():
    """Directorios y credenciales .env (solo al arrancar la app web)."""
    _asegurar_logos_dir()
    _asegurar_trabajadores_fotos_dir()
    _migrar_credenciales_env(_empresa_default_id())


def _migrar_schema():
    """Esquema vía Flask-Migrate (Postgres); legacy SQLite si no hay alembic_version."""
    _ensure_schema_core()
    _migrar_schema_bootstrap()


def _asegurar_certificados_dir():
    CERTIFICADOS_DIR.mkdir(parents=True, exist_ok=True)


def _mask_secret(valor: str | None) -> str:
    return SECRET_MASK if valor else ''


def _sii_config_a_dict(cfg: EmpresaSIIConfig, masked: bool = True) -> dict:
    return {
        'empresa_id': cfg.empresa_id,
        'api_key': _mask_secret(cfg.api_key),
        'rut_emisor': cfg.rut_emisor or '',
        'usuario': cfg.usuario or '',
        'password': _mask_secret(cfg.password),
        'certificado_path': cfg.certificado_path or '',
        'certificado_password': _mask_secret(cfg.certificado_password),
        'rut_certificado': cfg.rut_certificado or '',
        'certificado_b64': _mask_secret(cfg.certificado_b64),
        'ambiente': cfg.ambiente if cfg.ambiente is not None else 0,
        'rcv_base_url': cfg.rcv_base_url or 'https://servicios.simpleapi.cl',
        'api_base_url': cfg.api_base_url or 'https://api.simpleapi.cl',
        'tiene_api_key': bool(cfg.api_key),
        'tiene_password': bool(cfg.password),
        'tiene_certificado_password': bool(cfg.certificado_password),
        'tiene_certificado_b64': bool(cfg.certificado_b64),
    }


def _banco_conexion_a_dict(conn: EmpresaBancoConexion, masked: bool = True) -> dict:
    cuenta_nombre = conn.cuenta_contable.nombre if conn.cuenta_contable else ''
    return {
        'id': conn.id,
        'empresa_id': conn.empresa_id,
        'nombre': conn.nombre,
        'fintoc_api_key': _mask_secret(conn.fintoc_api_key),
        'fintoc_link_token': _mask_secret(conn.fintoc_link_token),
        'fintoc_account_id': conn.fintoc_account_id or '',
        'cuenta_contable_id': conn.cuenta_contable_id,
        'cuenta_contable_nombre': cuenta_nombre,
        'activa': conn.activa,
        'ultima_sincronizacion': conn.ultima_sincronizacion.isoformat() if conn.ultima_sincronizacion else None,
        'tiene_api_key': bool(conn.fintoc_api_key),
        'tiene_link_token': bool(conn.fintoc_link_token),
    }


def _env_sii_creds() -> dict:
    return {
        'api_key': os.environ.get('SII_API_KEY', ''),
        'api_base_url': os.environ.get('SII_API_BASE_URL', 'https://api.simpleapi.cl'),
        'rcv_base_url': os.environ.get('SII_RCV_BASE_URL', 'https://servicios.simpleapi.cl'),
        'rut_emisor': os.environ.get('SII_RUT_EMISOR', ''),
        'usuario': os.environ.get('SII_USUARIO', ''),
        'password': os.environ.get('SII_PASSWORD', ''),
        'certificado_path': os.environ.get('SII_CERTIFICADO_PATH', ''),
        'certificado_password': os.environ.get('SII_CERTIFICADO_PASSWORD', ''),
        'rut_certificado': os.environ.get('SII_RUT_CERTIFICADO', ''),
        'certificado_b64': os.environ.get('SII_CERTIFICADO_B64', ''),
        'ambiente': int(os.environ.get('SII_AMBIENTE', '0')),
    }


def _env_fintoc_creds() -> dict:
    return {
        'fintoc_api_key': os.environ.get('FINTOC_API_KEY', ''),
        'fintoc_link_token': os.environ.get('FINTOC_LINK_TOKEN', ''),
        'fintoc_account_id': os.environ.get('FINTOC_ACCOUNT_ID', ''),
    }


def _obtener_o_crear_sii_config(empresa_id: int) -> EmpresaSIIConfig:
    cfg = EmpresaSIIConfig.query.filter_by(empresa_id=empresa_id).first()
    if cfg:
        return cfg
    cfg = EmpresaSIIConfig(empresa_id=empresa_id, ambiente=0)
    db.session.add(cfg)
    db.session.flush()
    return cfg


def _sii_creds_dict(empresa_id: int) -> dict:
    cfg = EmpresaSIIConfig.query.filter_by(empresa_id=empresa_id).first()
    if cfg and cfg.api_key:
        return {
            'api_key': cfg.api_key or '',
            'api_base_url': cfg.api_base_url or 'https://api.simpleapi.cl',
            'rcv_base_url': cfg.rcv_base_url or 'https://servicios.simpleapi.cl',
            'rut_emisor': cfg.rut_emisor or '',
            'usuario': cfg.usuario or '',
            'password': cfg.password or '',
            'certificado_path': cfg.certificado_path or '',
            'certificado_password': cfg.certificado_password or '',
            'rut_certificado': cfg.rut_certificado or '',
            'certificado_b64': cfg.certificado_b64 or '',
            'ambiente': cfg.ambiente if cfg.ambiente is not None else 0,
        }
    if empresa_id == _empresa_default_id():
        return _env_sii_creds()
    return {}


def _sii_client_for_empresa(empresa_id: int) -> SIIClient:
    return SIIClient(creds=_sii_creds_dict(empresa_id))


def _fintoc_client_for_conexion(conn: EmpresaBancoConexion) -> FintocClient:
    return FintocClient(creds={
        'fintoc_api_key': conn.fintoc_api_key or '',
        'fintoc_link_token': conn.fintoc_link_token or '',
        'fintoc_account_id': conn.fintoc_account_id or '',
    })


def _migrar_credenciales_env(default_id: int):
    """Copia credenciales de .env a empresa 1 si la BD está vacía."""
    _asegurar_certificados_dir()
    cfg = EmpresaSIIConfig.query.filter_by(empresa_id=default_id).first()
    env_sii = _env_sii_creds()
    if not cfg and any(env_sii.get(k) for k in ('api_key', 'rut_emisor', 'usuario')):
        db.session.add(EmpresaSIIConfig(
            empresa_id=default_id,
            api_key=env_sii.get('api_key') or None,
            rut_emisor=env_sii.get('rut_emisor') or None,
            usuario=env_sii.get('usuario') or None,
            password=env_sii.get('password') or None,
            certificado_path=env_sii.get('certificado_path') or None,
            certificado_password=env_sii.get('certificado_password') or None,
            rut_certificado=env_sii.get('rut_certificado') or None,
            certificado_b64=env_sii.get('certificado_b64') or None,
            ambiente=env_sii.get('ambiente', 0),
            rcv_base_url=env_sii.get('rcv_base_url') or None,
            api_base_url=env_sii.get('api_base_url') or None,
        ))
    env_fintoc = _env_fintoc_creds()
    if not EmpresaBancoConexion.query.filter_by(empresa_id=default_id).first():
        if any(env_fintoc.values()):
            cuenta = _obtener_cuenta_banco_santander(default_id)
            db.session.add(EmpresaBancoConexion(
                empresa_id=default_id,
                nombre='Santander Cta Cte',
                fintoc_api_key=env_fintoc.get('fintoc_api_key') or None,
                fintoc_link_token=env_fintoc.get('fintoc_link_token') or None,
                fintoc_account_id=env_fintoc.get('fintoc_account_id') or None,
                cuenta_contable_id=cuenta.id,
                activa=True,
            ))
    db.session.commit()


def _obtener_cuentas_banco_empresa(empresa_id: int) -> list[Cuenta]:
    """Cuentas contables vinculadas a conexiones bancarias activas."""
    conexiones = EmpresaBancoConexion.query.filter_by(empresa_id=empresa_id, activa=True).all()
    cuentas = []
    vistos = set()
    for c in conexiones:
        if c.cuenta_contable_id and c.cuenta_contable_id not in vistos:
            cuenta = Cuenta.query.filter_by(empresa_id=empresa_id, id=c.cuenta_contable_id).first()
            if cuenta:
                cuentas.append(cuenta)
                vistos.add(cuenta.id)
    if not cuentas:
        cuentas = [_obtener_cuenta_banco_santander(empresa_id)]
    return cuentas


def _sincronizar_conexion_banco(conn: EmpresaBancoConexion, empresa_id: int) -> dict:
    if not conn.activa:
        return {'insertados': 0, 'omitidos': 0, 'errores': ['Conexión inactiva'], 'mock': False, 'mensaje': 'Inactiva'}
    cuenta_banco = conn.cuenta_contable
    if not cuenta_banco or cuenta_banco.empresa_id != empresa_id:
        cuenta_banco = _obtener_cuenta_banco_santander(empresa_id)
        conn.cuenta_contable_id = cuenta_banco.id
    cliente = _fintoc_client_for_conexion(conn)
    movimientos_ext, es_mock, mensaje = cliente.obtener_movimientos()
    insertados = 0
    omitidos = 0
    errores = []
    for mov_ext in movimientos_ext:
        try:
            fecha = _parse_fecha(mov_ext.get('fecha')) or date.today()
            monto = abs(float(mov_ext.get('monto', 0)))
            if monto <= 0:
                omitidos += 1
                continue
            descripcion = _descripcion_movimiento_banco(mov_ext)
            if _movimiento_banco_duplicado(mov_ext, descripcion, fecha, monto, empresa_id):
                omitidos += 1
                continue
            mov = _crear_movimiento_desde_banco(mov_ext, cuenta_banco, empresa_id)
            db.session.add(mov)
            insertados += 1
        except Exception as e:
            errores.append(str(e))
    conn.ultima_sincronizacion = datetime.utcnow()
    return {
        'mensaje': mensaje,
        'mock': es_mock,
        'insertados': insertados,
        'omitidos': omitidos,
        'errores': errores,
        'total_recibidos': len(movimientos_ext),
        'banco_id': conn.id,
        'banco_nombre': conn.nombre,
    }


def _aplicar_campos_secretos(data: dict, obj, campos: tuple[str, ...]):
    """PUT parcial: no sobrescribe secretos si vienen enmascarados o vacíos."""
    for campo in campos:
        if campo not in data:
            continue
        valor = data[campo]
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto == '' or texto == SECRET_MASK:
            continue
        setattr(obj, campo, texto)


def _asegurar_logos_dir():
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)


def _asegurar_trabajadores_fotos_dir():
    TRABAJADORES_FOTOS_DIR.mkdir(parents=True, exist_ok=True)


def _foto_relativa_trabajador(trabajador: Trabajador) -> str:
    return f'{trabajador.empresa_id}/{trabajador.id}.jpg'


def _foto_url(trabajador: Trabajador) -> str | None:
    if trabajador.foto_path:
        return f'/api/personal/{trabajador.id}/foto'
    return None


def _foto_path(trabajador: Trabajador) -> Path | None:
    if not trabajador.foto_path:
        return None
    path = TRABAJADORES_FOTOS_DIR / trabajador.foto_path
    return path if path.is_file() else None


def _eliminar_foto_trabajador(trabajador: Trabajador) -> None:
    path = _foto_path(trabajador)
    if path and path.is_file():
        path.unlink(missing_ok=True)
    trabajador.foto_path = None


def _procesar_imagen_foto_trabajador(archivo) -> tuple[bytes | None, str | None]:
    """Convierte a escala de grises, redimensiona y devuelve JPEG."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, 'Pillow no está instalado (pip install Pillow)'

    if not archivo or not getattr(archivo, 'filename', None):
        return None, 'Archivo de foto requerido'

    ext = Path(secure_filename(archivo.filename)).suffix.lower()
    if ext not in FOTO_ALLOWED_EXT:
        return None, f'Formato no permitido. Use: {", ".join(sorted(FOTO_ALLOWED_EXT))}'

    raw = archivo.read()
    if not raw:
        return None, 'Archivo vacío'
    if len(raw) > FOTO_MAX_BYTES:
        return None, f'La foto no puede superar {FOTO_MAX_BYTES // (1024 * 1024)} MB'

    try:
        img = Image.open(BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = img.convert('L')
        img.thumbnail((FOTO_MAX_PX, FOTO_MAX_PX), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format='JPEG', quality=85, optimize=True)
        return out.getvalue(), None
    except Exception as exc:
        return None, f'No se pudo procesar la imagen: {exc}'


def _guardar_foto_trabajador(trabajador: Trabajador, archivo) -> tuple[Trabajador | None, str | None]:
    jpeg_bytes, error = _procesar_imagen_foto_trabajador(archivo)
    if error:
        return None, error

    empresa_dir = TRABAJADORES_FOTOS_DIR / str(trabajador.empresa_id)
    empresa_dir.mkdir(parents=True, exist_ok=True)

    if trabajador.foto_path:
        prev = TRABAJADORES_FOTOS_DIR / trabajador.foto_path
        if prev.is_file():
            prev.unlink(missing_ok=True)

    rel = _foto_relativa_trabajador(trabajador)
    dest = TRABAJADORES_FOTOS_DIR / rel
    dest.write_bytes(jpeg_bytes)
    trabajador.foto_path = rel
    db.session.commit()
    return trabajador, None


def _guardar_logo_empresa(empresa: Empresa, archivo) -> tuple[Empresa | None, str | None]:
    if not archivo or not getattr(archivo, 'filename', None):
        return None, 'Archivo de logo requerido'

    ext = Path(secure_filename(archivo.filename)).suffix.lower()
    if ext not in LOGO_ALLOWED_EXT:
        return None, f'Formato no permitido. Use: {", ".join(sorted(LOGO_ALLOWED_EXT))}'

    raw = archivo.read()
    if not raw:
        return None, 'Archivo vacío'
    if len(raw) > LOGO_MAX_BYTES:
        return None, f'El logo no puede superar {LOGO_MAX_BYTES // (1024 * 1024)} MB'

    _asegurar_logos_dir()
    if empresa.logo_filename:
        prev = LOGOS_DIR / empresa.logo_filename
        if prev.is_file():
            prev.unlink(missing_ok=True)

    nombre = f'empresa_{empresa.id}{ext}'
    dest = LOGOS_DIR / nombre
    dest.write_bytes(raw)
    empresa.logo_filename = nombre
    db.session.commit()
    return empresa, None


def _logo_url(empresa: Empresa) -> str | None:
    if empresa.logo_filename:
        return f'/api/empresas/{empresa.id}/logo'
    return None


def _logo_path(empresa: Empresa) -> Path | None:
    if not empresa.logo_filename:
        return None
    path = LOGOS_DIR / empresa.logo_filename
    return path if path.is_file() else None


def _empresa_a_dict(empresa: Empresa) -> dict:
    return {
        'id': empresa.id,
        'rut': empresa.rut,
        'nombre': empresa.nombre,
        'direccion': empresa.direccion or '',
        'email': empresa.email or '',
        'telefono': empresa.telefono or '',
        'giro': empresa.giro or '',
        'activa': empresa.activa,
        'logo_url': _logo_url(empresa),
        'plan_cuentas_template': empresa.plan_cuentas_template or 'sociedad_profesionales',
        'created_at': empresa.created_at.isoformat() if empresa.created_at else None,
    }


def _eliminar_cuentas_contables_empresa(empresa_id: int) -> None:
    """Elimina el plan de cuentas jerárquico de una empresa (hojas primero)."""
    while True:
        cuentas = CuentaContable.query.filter_by(empresa_id=empresa_id).all()
        if not cuentas:
            break
        ids_con_hijos = {c.id_padre for c in cuentas if c.id_padre is not None}
        hojas = [c for c in cuentas if c.id not in ids_con_hijos]
        if not hojas:
            for c in cuentas:
                c.id_padre = None
            db.session.flush()
            continue
        for c in hojas:
            db.session.delete(c)
        db.session.flush()


def _eliminar_empresa(empresa_id: int) -> dict:
    """Elimina una empresa y todos sus datos asociados; ajusta la sesión Flask."""
    empresa = Empresa.query.get(empresa_id)
    if not empresa:
        raise ValueError('Empresa no encontrada')

    nombre = empresa.nombre
    en_request = has_request_context()
    trabajador_pre = _usuario_sesion() if en_request else None
    session_empresa_era_eliminada = en_request and session.get('empresa_id') == empresa_id
    trabajador_pertenecia = (
        trabajador_pre is not None and trabajador_pre.empresa_id == empresa_id
    )

    logo_filename = empresa.logo_filename
    cfg = EmpresaSIIConfig.query.filter_by(empresa_id=empresa_id).first()
    certificado_path = cfg.certificado_path if cfg else None

    try:
        RegistroTiempo.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        TareaEntrega.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        EntregaProgramada.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Comprobante.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Liquidacion.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Movimiento.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        EmpresaBancoConexion.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        for t in Trabajador.query.filter_by(empresa_id=empresa_id).all():
            _eliminar_foto_trabajador(t)
        Trabajador.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Proyecto.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Propuesta.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Cliente.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        _eliminar_cuentas_contables_empresa(empresa_id)
        CentroCosto.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        Cuenta.query.filter_by(empresa_id=empresa_id).delete(synchronize_session=False)
        if cfg:
            db.session.delete(cfg)
        db.session.delete(empresa)
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ValueError(
            'No se pudo eliminar: existen dependencias que impiden el borrado',
        ) from exc

    if logo_filename:
        path = LOGOS_DIR / logo_filename
        if path.is_file():
            path.unlink(missing_ok=True)
    if certificado_path:
        cert = Path(certificado_path)
        if cert.is_file():
            cert.unlink(missing_ok=True)

    session_cleared = False
    nueva_empresa_id = None
    if en_request:
        if trabajador_pertenecia:
            session.clear()
            session_cleared = True
        elif session_empresa_era_eliminada:
            otra = Empresa.query.order_by(Empresa.nombre).first()
            if otra:
                session['empresa_id'] = otra.id
                nueva_empresa_id = otra.id
            else:
                session.pop('empresa_id', None)
        else:
            raw = session.get('empresa_id')
            if raw is not None:
                try:
                    eid = int(raw)
                except (TypeError, ValueError):
                    eid = None
                if eid and Empresa.query.get(eid):
                    nueva_empresa_id = eid

    return {
        'mensaje': 'Empresa eliminada',
        'empresa_id': empresa_id,
        'nombre': nombre,
        'session_cleared': session_cleared,
        'nueva_empresa_id': nueva_empresa_id,
    }


def _cuenta_por_nombre(nombre, empresa_id: int):
    cuenta = Cuenta.query.filter_by(empresa_id=empresa_id, nombre=nombre).first()
    if not cuenta:
        raise ValueError(f'Cuenta no encontrada: {nombre}')
    return cuenta


def _obtener_cuenta_banco_santander(empresa_id: int) -> Cuenta:
    """Cuenta corriente Santander CLP: busca por nombre corto, seed o la crea."""
    cuenta = Cuenta.query.filter_by(
        empresa_id=empresa_id, nombre=NOMBRE_CUENTA_BANCO_SANTANDER,
    ).first()
    if cuenta:
        return cuenta
    cuenta = Cuenta.query.filter_by(
        empresa_id=empresa_id, nombre=NOMBRE_CUENTA_BANCO_PESOS,
    ).first()
    if cuenta:
        return cuenta
    cuenta = Cuenta(
        empresa_id=empresa_id,
        nombre=NOMBRE_CUENTA_BANCO_SANTANDER,
        categoria='activo_banco',
        moneda='CLP',
    )
    db.session.add(cuenta)
    db.session.flush()
    return cuenta


def _marcador_fintoc(fintoc_id: str) -> str:
    return f'[fintoc:{fintoc_id}]'


def _marcador_liquidacion(liq_id: int) -> str:
    return f'[liq:{liq_id}]'


def _descripcion_movimiento_liquidacion(liq: Liquidacion, trabajador: Trabajador | None) -> str:
    nombre = _nombre_completo_trabajador(trabajador) if trabajador else f'Trabajador {liq.trabajador_id}'
    marcador = _marcador_liquidacion(liq.id)
    return f'{marcador} Liquidación {liq.mes:02d}/{liq.anio} - {nombre}'[:255]


def _movimiento_liquidacion_duplicado(empresa_id: int, liq_id: int) -> bool:
    marcador = _marcador_liquidacion(liq_id)
    return Movimiento.query.filter(
        Movimiento.empresa_id == empresa_id,
        Movimiento.descripcion.like(f'%{marcador}%'),
    ).first() is not None


def _cuenta_origen_liquidacion(empresa_id: int) -> Cuenta:
    """Cuenta origen: remuneraciones por pagar si existe, si no banco."""
    cuenta = Cuenta.query.filter_by(
        empresa_id=empresa_id,
        nombre=NOMBRE_CUENTA_REMUNERACIONES_POR_PAGAR,
    ).first()
    if cuenta:
        return cuenta
    cuenta = Cuenta.query.filter(
        Cuenta.empresa_id == empresa_id,
        Cuenta.nombre.like('%por pagar%'),
        Cuenta.nombre.like('%emuneracion%'),
    ).first()
    if cuenta:
        return cuenta
    return _obtener_cuenta_banco_santander(empresa_id)


def _ingresar_liquidaciones_a_movimientos(empresa_id: int, mes: int, anio: int) -> dict:
    liquidaciones = Liquidacion.query.filter_by(empresa_id=empresa_id, mes=mes, anio=anio).all()
    if not liquidaciones:
        return {
            'insertados': 0,
            'omitidos': 0,
            'errores': [],
            'mensaje': 'No hay liquidaciones para el período',
        }

    origen = _cuenta_origen_liquidacion(empresa_id)
    fecha_mov = _fecha_uf_planilla(mes, anio)
    insertados = 0
    omitidos = 0
    errores = []

    for liq in liquidaciones:
        if _movimiento_liquidacion_duplicado(empresa_id, liq.id):
            omitidos += 1
            continue
        t = liq.trabajador_rel
        if not t:
            errores.append({'liquidacion_id': liq.id, 'error': 'Trabajador no encontrado'})
            continue
        destino = Cuenta.query.filter_by(empresa_id=empresa_id, id=t.cuenta_gasto_id).first()
        if not destino:
            errores.append({
                'liquidacion_id': liq.id,
                'nombre': _nombre_completo_trabajador(t),
                'error': 'Cuenta de gasto no encontrada',
            })
            continue
        monto = float(liq.alcance_liquido or 0)
        if monto <= 0:
            omitidos += 1
            continue
        tipo = calcular_transaccion(origen.categoria, destino.categoria)
        mov = Movimiento(
            empresa_id=empresa_id,
            fecha_movimiento=fecha_mov,
            monto_pesos=monto,
            centro_costo='Administración',
            estado='Activo',
            clase='gasto',
            cta_origen_id=origen.id,
            cta_destino_id=destino.id,
            transaccion=tipo,
            descripcion=_descripcion_movimiento_liquidacion(liq, t),
            status_pago='',
            proyecto_id=None,
        )
        db.session.add(mov)
        insertados += 1

    if insertados:
        db.session.commit()
        _recalcular_todos_proyectos(empresa_id)

    partes = [f'{insertados} ingresado(s)']
    if omitidos:
        partes.append(f'{omitidos} omitido(s)')
    if errores:
        partes.append(f'{len(errores)} error(es)')

    return {
        'insertados': insertados,
        'omitidos': omitidos,
        'errores': errores,
        'mensaje': 'Movimientos de planilla: ' + ', '.join(partes),
    }


def _descripcion_movimiento_banco(mov_ext: dict) -> str:
    desc = mov_ext.get('descripción') or mov_ext.get('descripcion') or 'Movimiento bancario'
    fintoc_id = (mov_ext.get('id') or '').strip()
    if fintoc_id:
        marcador = _marcador_fintoc(fintoc_id)
        if marcador not in desc:
            desc = f'{marcador} {desc}'
    return desc[:255]


def _movimiento_banco_duplicado(mov_ext: dict, descripcion: str, fecha: date, monto: float, empresa_id: int) -> bool:
    fintoc_id = (mov_ext.get('id') or '').strip()
    base = Movimiento.query.filter_by(empresa_id=empresa_id)
    if fintoc_id:
        marcador = _marcador_fintoc(fintoc_id)
        if base.filter(Movimiento.descripcion.like(f'%{marcador}%')).first():
            return True
    return base.filter_by(
        fecha_movimiento=fecha,
        monto_pesos=monto,
        descripcion=descripcion,
    ).first() is not None


def _crear_movimiento_desde_banco(mov_ext: dict, cuenta_banco: Cuenta, empresa_id: int) -> Movimiento:
    fecha = _parse_fecha(mov_ext.get('fecha')) or date.today()
    monto = abs(float(mov_ext.get('monto', 0)))
    tipo = (mov_ext.get('tipo') or 'ingreso').lower()
    descripcion = _descripcion_movimiento_banco(mov_ext)

    if tipo == 'ingreso':
        origen = _cuenta_por_nombre(NOMBRE_CUENTA_CLIENTES, empresa_id)
        destino = cuenta_banco
    else:
        origen = cuenta_banco
        destino = _cuenta_por_nombre(NOMBRE_CUENTA_GASTO_BANCO, empresa_id)

    transaccion = calcular_transaccion(origen.categoria, destino.categoria)
    return Movimiento(
        empresa_id=empresa_id,
        fecha_movimiento=fecha,
        monto_pesos=monto,
        centro_costo='Administración',
        estado='Activo',
        clase='general',
        cta_origen_id=origen.id,
        cta_destino_id=destino.id,
        transaccion=transaccion,
        descripcion=descripcion,
    )


def _cuenta_a_dict(cuenta: Cuenta, saldo: float | None = None) -> dict:
    d = {
        'id': cuenta.id,
        'nombre': cuenta.nombre,
        'categoria': cuenta.categoria,
        'moneda': cuenta.moneda,
        'saldo_inicial': cuenta.saldo_inicial or 0.0,
    }
    if saldo is not None:
        d['saldo'] = saldo
    return d


def _validar_datos_cuenta(data: dict, empresa_id: int, cuenta_id: int | None = None) -> tuple[dict | None, str | None]:
    nombre = (data.get('nombre') or '').strip()
    if not nombre:
        return None, 'El nombre es requerido'
    if len(nombre) > 100:
        return None, 'El nombre no puede superar 100 caracteres'

    duplicado = Cuenta.query.filter_by(empresa_id=empresa_id, nombre=nombre).first()
    if duplicado and duplicado.id != cuenta_id:
        return None, 'Ya existe una cuenta con ese nombre'

    categoria = (data.get('categoria') or '').strip()
    if categoria not in CATEGORIAS_CUENTA:
        return None, f'Categoría inválida. Use: {", ".join(CATEGORIAS_CUENTA)}'

    moneda = (data.get('moneda') or 'CLP').strip().upper()
    if moneda not in MONEDAS_CUENTA:
        return None, f'Moneda inválida. Use: {", ".join(MONEDAS_CUENTA)}'

    try:
        saldo_inicial = float(data.get('saldo_inicial', 0) or 0)
    except (TypeError, ValueError):
        return None, 'Saldo inicial inválido'

    return {'nombre': nombre, 'categoria': categoria, 'moneda': moneda, 'saldo_inicial': saldo_inicial}, None


def _cuenta_en_uso(cuenta_id: int, empresa_id: int) -> str | None:
    if Movimiento.query.filter(
        Movimiento.empresa_id == empresa_id,
        db.or_(Movimiento.cta_origen_id == cuenta_id, Movimiento.cta_destino_id == cuenta_id),
    ).first():
        return 'tiene movimientos asociados'
    if Trabajador.query.filter_by(empresa_id=empresa_id, cuenta_gasto_id=cuenta_id).first():
        return 'está asignada a trabajadores'
    return None


def _nombre_completo_trabajador(t: Trabajador) -> str:
    partes = [t.nombres, t.apellido_paterno]
    if t.apellido_materno:
        partes.append(t.apellido_materno)
    return ' '.join(partes)


def _nombre_display_trabajador(t: Trabajador) -> str:
    alias = (getattr(t, 'alias', None) or '').strip()
    if alias:
        return alias
    return _nombre_completo_trabajador(t)


def _sueldo_base_clp_trabajador(trabajador: Trabajador, uf_clp: float) -> tuple[float, float]:
    """Devuelve (sueldo_base_uf, sueldo_base_clp) usando UF del día."""
    if trabajador.sueldo_base_uf and trabajador.sueldo_base_uf > 0:
        sueldo_uf = float(trabajador.sueldo_base_uf)
        return sueldo_uf, round(sueldo_uf * uf_clp)
    if uf_clp > 0 and trabajador.sueldo_base > 0:
        sueldo_uf = round(trabajador.sueldo_base / uf_clp, 4)
        return sueldo_uf, round(trabajador.sueldo_base)
    return 0.0, float(trabajador.sueldo_base or 0)


def _trabajador_a_dict(t: Trabajador, uf_clp: float | None = None) -> dict:
    if uf_clp is None:
        uf_info = _uf_hoy()
        uf_clp = uf_info['valor']
    sueldo_uf, sueldo_clp = _sueldo_base_clp_trabajador(t, uf_clp)
    return {
        'id': t.id,
        'rut': t.rut,
        'apellido_paterno': t.apellido_paterno,
        'apellido_materno': t.apellido_materno or '',
        'nombres': t.nombres,
        'alias': (t.alias or '').strip(),
        'nombre_completo': _nombre_completo_trabajador(t),
        'nombre_display': _nombre_display_trabajador(t),
        'fecha_ingreso': t.fecha_ingreso.strftime('%Y-%m-%d'),
        'tipo_contrato': t.tipo_contrato,
        'sueldo_base': sueldo_clp,
        'sueldo_base_uf': sueldo_uf,
        'banco': t.banco or '',
        'cuenta_bancaria': t.cuenta_bancaria or '',
        'nombre_isapre': t.nombre_isapre or '',
        'nombre_plan_isapre': t.nombre_plan_isapre or '',
        'afp': t.afp,
        'sistema_salud': t.sistema_salud,
        'valor_plan_isapre_uf': t.valor_plan_isapre_uf,
        'cuenta_gasto_id': t.cuenta_gasto_id,
        'cuenta_gasto': t.cuenta_gasto.nombre if t.cuenta_gasto else '',
        'email': t.email or '',
        'tiene_password': bool(t.password_hash),
        'rol': _rol_trabajador(t),
        'factor_overhead': float(t.factor_overhead or 1.0),
        'costo_hh_manual': float(t.costo_hh_manual) if t.costo_hh_manual is not None else None,
        'costo_hh_real': round(t.costo_hh_real, 2),
        'foto_url': _foto_url(t),
    }


def _validar_datos_trabajador(data: dict, empresa_id: int, trabajador_id: int | None = None) -> tuple[dict | None, str | None]:
    """Valida payload de trabajador; devuelve (campos_normalizados, error)."""
    campos_req = (
        'rut', 'apellido_paterno', 'nombres', 'fecha_ingreso',
        'tipo_contrato', 'afp', 'sistema_salud', 'cuenta_gasto_id',
    )
    faltantes = [c for c in campos_req if data.get(c) is None or data.get(c) == '']
    sueldo_uf_raw = data.get('sueldo_base_uf')
    sueldo_clp_raw = data.get('sueldo_base')
    if (sueldo_uf_raw is None or sueldo_uf_raw == '') and (sueldo_clp_raw is None or sueldo_clp_raw == ''):
        faltantes.append('sueldo_base_uf')
    if faltantes:
        return None, f'Campos requeridos: {", ".join(faltantes)}'

    rut = str(data['rut'])[:20]
    duplicado = Trabajador.query.filter_by(empresa_id=empresa_id, rut=rut).first()
    if duplicado and duplicado.id != trabajador_id:
        return None, 'Ya existe un trabajador con ese RUT'

    email = _normalizar_email(data.get('email'))
    if email:
        duplicado_email = Trabajador.query.filter(
            db.func.lower(Trabajador.email) == email,
        ).first()
        if duplicado_email and duplicado_email.id != trabajador_id:
            return None, 'Ya existe un trabajador con ese email'

    cuenta = Cuenta.query.filter_by(empresa_id=empresa_id, id=int(data['cuenta_gasto_id'])).first()
    if not cuenta:
        return None, 'Cuenta inválida'

    tipo_contrato = data['tipo_contrato']
    if tipo_contrato not in TIPOS_CONTRATO:
        return None, f'Tipo de contrato inválido. Use: {", ".join(TIPOS_CONTRATO)}'

    sistema_salud = data['sistema_salud']
    if sistema_salud not in SISTEMAS_SALUD:
        return None, f'Sistema de salud inválido. Use: {", ".join(SISTEMAS_SALUD)}'

    uf_info = _uf_hoy()
    uf_clp = uf_info['valor']
    if sueldo_uf_raw not in (None, ''):
        sueldo_base_uf = float(sueldo_uf_raw)
        sueldo_base = round(sueldo_base_uf * uf_clp)
    else:
        sueldo_base = float(sueldo_clp_raw)
        sueldo_base_uf = round(sueldo_base / uf_clp, 4) if uf_clp > 0 else 0.0

    return {
        'rut': rut,
        'apellido_paterno': data['apellido_paterno'][:100],
        'apellido_materno': (data.get('apellido_materno') or '')[:100] or None,
        'nombres': data['nombres'][:100],
        'alias': (data.get('alias') or '').strip()[:100] or None,
        'fecha_ingreso': _parse_fecha(data['fecha_ingreso']),
        'tipo_contrato': tipo_contrato,
        'sueldo_base': sueldo_base,
        'sueldo_base_uf': sueldo_base_uf,
        'banco': (data.get('banco') or '')[:100] or None,
        'cuenta_bancaria': (data.get('cuenta_bancaria') or '')[:50] or None,
        'nombre_isapre': (data.get('nombre_isapre') or '')[:100] or None,
        'nombre_plan_isapre': (data.get('nombre_plan_isapre') or '')[:100] or None,
        'afp': data['afp'][:50],
        'sistema_salud': sistema_salud,
        'valor_plan_isapre_uf': float(data.get('valor_plan_isapre_uf') or 0),
        'cuenta_gasto_id': cuenta.id,
        'email': email,
    }, None


def _aplicar_password_trabajador(trabajador: Trabajador, data: dict, es_nuevo: bool) -> str | None:
    """Aplica password_hash si corresponde. Devuelve mensaje de error o None."""
    password = data.get('password')
    if password is not None and str(password).strip() == '':
        password = None
    email = _normalizar_email(data.get('email') if 'email' in data else trabajador.email)

    if es_nuevo and email and not password:
        return 'La contraseña es requerida al asignar email a un nuevo trabajador'
    if password:
        if len(str(password)) < 6:
            return 'La contraseña debe tener al menos 6 caracteres'
        trabajador.password_hash = _hash_password(str(password))
    return None


def _aplicar_datos_trabajador(trabajador: Trabajador, campos: dict):
    for clave, valor in campos.items():
        setattr(trabajador, clave, valor)


def _detalle_calculo_desde_liq(liq: Liquidacion) -> dict:
    if liq.detalle_calculo:
        try:
            return json.loads(liq.detalle_calculo)
        except Exception:
            pass
    t = liq.trabajador_rel
    uf = liq.uf_valor or _uf_hoy()['valor']
    return _calcular_montos_liquidacion(t, liq.dias_trabajados, uf)['detalle']


def _liquidacion_a_dict(liq: Liquidacion) -> dict:
    t = liq.trabajador_rel
    detalle = _detalle_calculo_desde_liq(liq)
    sueldo_uf = liq.sueldo_base_uf
    if sueldo_uf is None and t:
        sueldo_uf, _ = _sueldo_base_clp_trabajador(t, liq.uf_valor or _uf_hoy()['valor'])
    return {
        'id': liq.id,
        'trabajador_id': liq.trabajador_id,
        'nombre': _nombre_completo_trabajador(t) if t else '',
        'rut': t.rut if t else '',
        'mes': liq.mes,
        'anio': liq.anio,
        'dias_trabajados': liq.dias_trabajados,
        'sueldo_base_uf': sueldo_uf or 0,
        'sueldo_base': detalle.get('sueldo_base_clp') or (t.sueldo_base if t else 0),
        'sueldo_base_proporcional': liq.sueldo_base_proporcional,
        'total_imponible': liq.total_imponible,
        'total_haberes': liq.total_haberes,
        'total_descuentos': liq.total_descuentos,
        'alcance_liquido': liq.alcance_liquido,
        'estado': liq.estado,
        'uf_valor': liq.uf_valor,
        'uf_fecha': detalle.get('uf_fecha'),
        'detalle': detalle,
        'banco': t.banco if t else '',
        'cuenta_bancaria': t.cuenta_bancaria if t else '',
        'nombre_isapre': t.nombre_isapre if t else '',
        'nombre_plan_isapre': t.nombre_plan_isapre if t else '',
    }


def _empresa_pdf_payload(empresa: Empresa | None = None) -> dict:
    if empresa is None:
        empresa = Empresa.query.filter_by(activa=True).order_by(Empresa.id).first()
    if empresa is None:
        return {
            'razon_social': os.environ.get('EMPRESA_RAZON_SOCIAL', 'B green Chile Limitada'),
            'rut': os.environ.get('SII_RUT_EMISOR', os.environ.get('EMPRESA_RUT', '77.748.415-K')),
            'direccion': os.environ.get(
                'EMPRESA_DIRECCION',
                'Obispo Donoso 5 oficina 62, Providencia',
            ),
            'unidad_negocio': os.environ.get('EMPRESA_UNIDAD_NEGOCIO', 'CASA MATRIZ'),
            'logo_path': None,
        }
    logo = _logo_path(empresa)
    return {
        'razon_social': empresa.nombre,
        'rut': empresa.rut,
        'direccion': empresa.direccion or '',
        'unidad_negocio': os.environ.get('EMPRESA_UNIDAD_NEGOCIO', 'CASA MATRIZ'),
        'logo_path': str(logo) if logo else None,
    }


def _resolver_empresa_pdf(empresa_id: int | None = None) -> Empresa | None:
    if empresa_id:
        return Empresa.query.get(empresa_id)
    return Empresa.query.filter_by(activa=True).order_by(Empresa.id).first()


def _liquidacion_pdf_payload(liq: Liquidacion, empresa_id: int | None = None) -> dict:
    d = _liquidacion_a_dict(liq)
    t = liq.trabajador_rel
    d['trabajador'] = _trabajador_a_dict(t, uf_clp=liq.uf_valor) if t else {}
    d['detalle'] = _enriquecer_detalle_pdf(d.get('detalle') or {}, t, liq)
    empresa = _resolver_empresa_pdf(empresa_id)
    d['empresa'] = _empresa_pdf_payload(empresa)
    return d


def _valor_uf_a_dict(v: ValorUF) -> dict:
    return {
        'id': v.id,
        'fecha': v.fecha.strftime('%Y-%m-%d'),
        'valor': v.valor,
    }


def _fecha_uf_planilla(mes: int, anio: int) -> date:
    """Fecha de referencia UF para planilla: último día del mes liquidado."""
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, ultimo_dia)


def _dias_trabajados_mes(trabajador: Trabajador, mes: int, anio: int) -> int:
    """Días efectivos del mes; ajusta si el ingreso fue durante el período."""
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    if trabajador.fecha_ingreso.year > anio:
        return 0
    if trabajador.fecha_ingreso.year == anio and trabajador.fecha_ingreso.month > mes:
        return 0
    if trabajador.fecha_ingreso.year == anio and trabajador.fecha_ingreso.month == mes:
        return ultimo_dia - trabajador.fecha_ingreso.day + 1
    return ultimo_dia


def _desglose_salud_liquidacion(
    trabajador: Trabajador, total_imponible: float, uf_clp: float,
) -> tuple[float, float, float]:
    """Devuelve (cotizacion_7pct, adicional_salud, total_descuento_salud)."""
    cotizacion = round(total_imponible * TASA_FONASA)
    if trabajador.sistema_salud.lower() == 'isapre':
        plan_clp = round((trabajador.valor_plan_isapre_uf or 0) * uf_clp)
        if plan_clp > cotizacion:
            return float(cotizacion), float(plan_clp - cotizacion), float(plan_clp)
        return float(cotizacion), 0.0, float(cotizacion)
    return float(cotizacion), 0.0, float(cotizacion)


def _desglose_salud_desde_monto(
    monto_salud: float, total_imponible: float, es_isapre: bool,
) -> tuple[float, float]:
    """Inferir cotizacion 7% y adicional a partir del monto almacenado (registros antiguos)."""
    cotiz_7 = round(total_imponible * TASA_FONASA)
    monto = float(monto_salud)
    if not es_isapre:
        return monto, 0.0
    if monto > cotiz_7:
        return float(cotiz_7), float(monto - cotiz_7)
    return monto, 0.0


def _enriquecer_detalle_pdf(detalle: dict, trabajador: Trabajador | None, liq: Liquidacion) -> dict:
    """Completa campos del detalle para el PDF (compatibilidad con registros antiguos)."""
    det = dict(detalle or {})
    t = trabajador
    uf_clp = float(det.get('uf_valor') or liq.uf_valor or _uf_hoy()['valor'])
    imponible = float(det.get('total_imponible') or liq.total_imponible or 0)
    dias = int(det.get('dias_trabajados') or liq.dias_trabajados or 0)
    es_isapre = bool(t and (t.sistema_salud or '').lower() == 'isapre')

    if t and not det.get('sueldo_base_clp'):
        _, sueldo_clp = _sueldo_base_clp_trabajador(t, uf_clp)
        det['sueldo_base_clp'] = sueldo_clp
    if t and not det.get('sueldo_base_uf'):
        sueldo_uf, _ = _sueldo_base_clp_trabajador(t, uf_clp)
        det['sueldo_base_uf'] = sueldo_uf

    if not det.get('sueldo_proporcional_clp'):
        det['sueldo_proporcional_clp'] = float(liq.sueldo_base_proporcional or 0)

    if 'descuento_salud_cotizacion' not in detalle:
        monto_salud = float(det.get('descuento_salud') or 0)
        if monto_salud and imponible:
            cotiz, adicional = _desglose_salud_desde_monto(monto_salud, imponible, es_isapre)
        elif t and imponible:
            cotiz, adicional, monto_salud = _desglose_salud_liquidacion(t, imponible, uf_clp)
        else:
            cotiz, adicional, monto_salud = 0.0, 0.0, 0.0
        det['descuento_salud_cotizacion'] = cotiz
        det['descuento_adicional_salud'] = adicional
        if not det.get('descuento_salud'):
            det['descuento_salud'] = monto_salud
    else:
        det.setdefault('descuento_adicional_salud', 0.0)

    if not det.get('haberes_imponibles') and dias:
        det['haberes_imponibles'] = [{
            'concepto': f'SUELDO BASE {dias} DIAS',
            'monto': float(det.get('sueldo_proporcional_clp') or 0),
        }]
    det.setdefault('haberes_no_imponibles', [])
    det.setdefault('total_no_imponible', 0.0)
    det.setdefault('impuesto_unico', 0.0)
    det.setdefault('descuentos_extra', [])

    afp = float(det.get('descuento_afp') or 0)
    cotiz_salud = float(det.get('descuento_salud_cotizacion') or 0)
    if not det.get('total_tributable') and imponible:
        det['total_tributable'] = round(imponible - afp - cotiz_salud)

    det.setdefault('unidad_negocio', os.environ.get('EMPRESA_UNIDAD_NEGOCIO', 'CASA MATRIZ'))
    if t:
        det.setdefault('valor_plan_uf', float(t.valor_plan_isapre_uf or 0))
    return det


def _calcular_montos_liquidacion(trabajador: Trabajador, dias_trabajados: int, uf_clp: float) -> dict:
    hoy = date.today()
    uf_fecha = hoy.strftime('%Y-%m-%d')
    vacio = {
        'dias_trabajados': 0,
        'sueldo_base_proporcional': 0.0,
        'total_imponible': 0.0,
        'total_haberes': 0.0,
        'total_descuentos': 0.0,
        'alcance_liquido': 0.0,
        'uf_valor': uf_clp,
        'sueldo_base_uf': 0.0,
        'detalle': {
            'uf_valor': uf_clp,
            'uf_fecha': uf_fecha,
            'sueldo_base_uf': 0.0,
            'sueldo_base_clp': 0.0,
            'dias_trabajados': 0,
            'dias_mes_ref': DIAS_MES_REF,
            'sueldo_proporcional_clp': 0.0,
            'afp_pct': round(TASA_AFP * 100, 2),
            'descuento_afp': 0.0,
            'descuento_salud': 0.0,
            'descuento_salud_cotizacion': 0.0,
            'descuento_adicional_salud': 0.0,
            'impuesto_unico': 0.0,
            'fonasa_pct': int(TASA_FONASA * 100),
            'haberes_imponibles': [],
            'haberes_no_imponibles': [],
            'total_no_imponible': 0.0,
            'total_imponible': 0.0,
            'total_haberes': 0.0,
            'total_descuentos': 0.0,
            'total_tributable': 0.0,
            'alcance_liquido': 0.0,
            'unidad_negocio': os.environ.get('EMPRESA_UNIDAD_NEGOCIO', 'CASA MATRIZ'),
        },
    }
    if dias_trabajados <= 0:
        return vacio

    sueldo_uf, sueldo_clp = _sueldo_base_clp_trabajador(trabajador, uf_clp)
    sueldo_prop = round(sueldo_clp * dias_trabajados / DIAS_MES_REF)
    total_imponible = float(sueldo_prop)
    total_no_imponible = 0.0
    total_haberes = total_imponible + total_no_imponible

    descuento_afp = round(total_imponible * TASA_AFP)
    cotiz_salud, adicional_salud, descuento_salud = _desglose_salud_liquidacion(
        trabajador, total_imponible, uf_clp,
    )
    impuesto_unico = 0.0
    total_descuentos = float(descuento_afp + descuento_salud + impuesto_unico)
    liquido = total_haberes - total_descuentos
    total_tributable = round(total_imponible - descuento_afp - cotiz_salud)

    detalle = {
        'uf_valor': uf_clp,
        'uf_fecha': uf_fecha,
        'sueldo_base_uf': sueldo_uf,
        'sueldo_base_clp': sueldo_clp,
        'dias_trabajados': dias_trabajados,
        'dias_mes_ref': DIAS_MES_REF,
        'sueldo_proporcional_clp': float(sueldo_prop),
        'afp_pct': round(TASA_AFP * 100, 2),
        'descuento_afp': float(descuento_afp),
        'descuento_salud': float(descuento_salud),
        'descuento_salud_cotizacion': cotiz_salud,
        'descuento_adicional_salud': adicional_salud,
        'impuesto_unico': impuesto_unico,
        'valor_plan_uf': float(trabajador.valor_plan_isapre_uf or 0),
        'fonasa_pct': int(TASA_FONASA * 100),
        'haberes_imponibles': [{
            'concepto': f'SUELDO BASE {dias_trabajados} DIAS',
            'monto': float(sueldo_prop),
        }],
        'haberes_no_imponibles': [],
        'total_no_imponible': total_no_imponible,
        'total_imponible': total_imponible,
        'total_haberes': total_haberes,
        'total_descuentos': total_descuentos,
        'total_tributable': total_tributable,
        'alcance_liquido': liquido,
        'unidad_negocio': os.environ.get('EMPRESA_UNIDAD_NEGOCIO', 'CASA MATRIZ'),
    }

    return {
        'dias_trabajados': dias_trabajados,
        'sueldo_base_proporcional': float(sueldo_prop),
        'total_imponible': total_imponible,
        'total_haberes': total_haberes,
        'total_descuentos': total_descuentos,
        'alcance_liquido': liquido,
        'uf_valor': uf_clp,
        'sueldo_base_uf': sueldo_uf,
        'detalle': detalle,
    }


def _recalcular_todos_proyectos(empresa_id: int):
    movimientos = Movimiento.query.filter_by(empresa_id=empresa_id).all()
    for proyecto in Proyecto.query.filter_by(empresa_id=empresa_id).all():
        recalcular_proyecto(proyecto, movimientos)
    db.session.commit()


DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 100
ADMIN_PER_PAGE = 500
FETCH_ALL_PER_PAGE = 0

PROYECTO_SORT_FIELDS = {
    'nombre': Proyecto.nombre,
    'servicio': Proyecto.servicio,
    'superficie': Proyecto.superficie,
    'status': Proyecto.status,
    'monto_contrato': Proyecto.monto_contrato,
    'monto_pagado': Proyecto.monto_pagado,
    'monto_gastos': Proyecto.monto_gastos,
}

MOVIMIENTO_SORT_FIELDS = {
    'fecha_movimiento': Movimiento.fecha_movimiento,
    'fecha_estado_pago': Movimiento.fecha_estado_pago,
    'fecha_facturacion': Movimiento.fecha_facturacion,
    'descripcion': Movimiento.descripcion,
    'monto': Movimiento.monto_pesos,
    'numero_factura': Movimiento.numero_factura,
    'status_pago': Movimiento.status_pago,
    'estado': Movimiento.estado,
}


def _solo_digitos(val) -> str:
    return re.sub(r'\D', '', str(val or ''))


def _parse_pagination_args():
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page_raw = int(request.args.get('per_page', DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        per_page_raw = DEFAULT_PER_PAGE
    if per_page_raw == FETCH_ALL_PER_PAGE:
        per_page = FETCH_ALL_PER_PAGE
    elif per_page_raw == ADMIN_PER_PAGE:
        per_page = ADMIN_PER_PAGE
    else:
        per_page = max(1, min(per_page_raw, MAX_PER_PAGE))
    return page, per_page


def _paginated_json(items, total: int, page: int, per_page: int):
    pages = max(1, (total + per_page - 1) // per_page) if per_page else 1
    return jsonify({
        'items': items,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': pages,
    })


def _paginate_query(query, page: int, per_page: int):
    total = query.count()
    if per_page == FETCH_ALL_PER_PAGE:
        return query.all(), total, 1
    pages = max(1, (total + per_page - 1) // per_page) if per_page else 1
    if total > 0 and page > pages:
        page = pages
    offset = (page - 1) * per_page
    return query.offset(offset).limit(per_page).all(), total, page


def _filtrar_proyectos_query(query, args):
    search = (args.get('search') or args.get('nombre') or '').strip()
    if search:
        query = query.filter(Proyecto.nombre.ilike(f'%{search}%'))
    cliente_id = args.get('cliente_id') or args.get('cliente')
    if cliente_id:
        query = query.filter(Proyecto.cliente_id == int(cliente_id))
    servicio = args.get('servicio')
    if servicio:
        query = query.filter(Proyecto.servicio == servicio)
    status = args.get('status')
    if status:
        query = query.filter(Proyecto.status == status)
    superficie = _solo_digitos(args.get('superficie'))
    if superficie:
        query = query.filter(cast(Proyecto.superficie, String).like(f'%{superficie}%'))
    for field in ('monto_contrato', 'monto_pagado', 'monto_gastos'):
        needle = _solo_digitos(args.get(field))
        if needle:
            col = getattr(Proyecto, field)
            query = query.filter(cast(func.round(col), String).like(f'%{needle}%'))
    return query


def _ordenar_proyectos(query, sort: str | None, order: str | None):
    sort = (sort or 'nombre').strip()
    order = (order or 'asc').lower()
    descending = order == 'desc'
    if sort == 'cliente':
        query = query.join(Proyecto.cliente_rel)
        col = Cliente.razon_social
    else:
        col = PROYECTO_SORT_FIELDS.get(sort, Proyecto.nombre)
    return query.order_by(col.desc() if descending else col.asc())


def _filtrar_movimientos_query(query, args):
    search = (args.get('search') or args.get('descripcion') or '').strip()
    if search:
        pattern = f'%{search}%'
        filtros = [
            Movimiento.descripcion.ilike(pattern),
            Movimiento.numero_factura.ilike(pattern),
            Movimiento.centro_costo.ilike(pattern),
        ]
        monto_digits = _solo_digitos(search)
        if monto_digits:
            filtros.append(
                cast(func.round(Movimiento.monto_pesos), String).like(f'%{monto_digits}%')
            )
        query = query.filter(or_(*filtros))
    status_pago = args.get('status_pago')
    if status_pago:
        query = query.filter(Movimiento.status_pago == status_pago)
    proyecto_id = args.get('proyecto_id')
    if proyecto_id == '__admin__':
        query = query.filter(Movimiento.proyecto_id.is_(None))
    elif proyecto_id:
        query = query.filter(Movimiento.proyecto_id == int(proyecto_id))
    clase = args.get('clase')
    if clase:
        query = query.filter(Movimiento.clase == clase)
    tipo = args.get('tipo')
    if tipo == 'estado_pago':
        query = query.filter(Movimiento.clase == 'estado_pago')
    elif tipo == 'gasto':
        query = query.filter(Movimiento.clase == 'gasto')
    elif tipo in ('Ingreso', 'Egreso', 'Transferencia'):
        query = query.filter(Movimiento.clase == 'general', Movimiento.transaccion == tipo)
    origen_id = args.get('origen_id')
    if origen_id:
        query = query.filter(Movimiento.cta_origen_id == int(origen_id))
    destino_id = args.get('destino_id')
    if destino_id:
        query = query.filter(Movimiento.cta_destino_id == int(destino_id))
    estado = args.get('estado')
    if estado:
        query = query.filter(Movimiento.estado == estado)
    for date_field in ('fecha_movimiento', 'fecha_estado_pago', 'fecha_facturacion'):
        val = args.get(date_field)
        if val:
            parsed = _parse_fecha(val)
            if parsed:
                query = query.filter(getattr(Movimiento, date_field) == parsed)
    fecha_desde = args.get('fecha_desde')
    if fecha_desde:
        parsed = _parse_fecha(fecha_desde)
        if parsed:
            query = query.filter(Movimiento.fecha_movimiento >= parsed)
    fecha_hasta = args.get('fecha_hasta')
    if fecha_hasta:
        parsed = _parse_fecha(fecha_hasta)
        if parsed:
            query = query.filter(Movimiento.fecha_movimiento <= parsed)
    monto = _solo_digitos(args.get('monto'))
    if monto:
        query = query.filter(cast(func.round(Movimiento.monto_pesos), String).like(f'%{monto}%'))
    numero_factura = (args.get('numero_factura') or '').strip()
    if numero_factura:
        query = query.filter(Movimiento.numero_factura.ilike(f'%{numero_factura}%'))
    return query


def _ordenar_movimientos(query, sort: str | None, order: str | None):
    sort = (sort or 'fecha_movimiento').strip()
    order = (order or 'desc').lower()
    descending = order == 'desc'
    cuenta_origen = aliased(Cuenta)
    cuenta_destino = aliased(Cuenta)
    if sort == 'origen':
        query = query.outerjoin(cuenta_origen, Movimiento.cta_origen_id == cuenta_origen.id)
        col = cuenta_origen.nombre
    elif sort == 'destino':
        query = query.outerjoin(cuenta_destino, Movimiento.cta_destino_id == cuenta_destino.id)
        col = cuenta_destino.nombre
    elif sort == 'proyecto':
        query = query.outerjoin(Proyecto, Movimiento.proyecto_id == Proyecto.id)
        col = Proyecto.nombre
    elif sort == 'tipo':
        return query.order_by(
            Movimiento.clase.desc() if descending else Movimiento.clase.asc(),
            Movimiento.transaccion.desc() if descending else Movimiento.transaccion.asc(),
        )
    else:
        col = MOVIMIENTO_SORT_FIELDS.get(sort, Movimiento.fecha_movimiento)
    return query.order_by(col.desc() if descending else col.asc())


def _proyecto_a_dict(p, movimientos):
    recalcular_proyecto(p, movimientos)
    return {
        'id': p.id,
        'nombre': p.nombre,
        'cliente': p.cliente_rel.razon_social,
        'cliente_id': p.cliente_id,
        'servicio': p.servicio,
        'superficie': p.superficie,
        'monto_contrato': p.monto_contrato,
        'monto_pagado': p.monto_pagado,
        'monto_facturado': p.monto_facturado,
        'saldo_por_facturar': p.saldo_por_facturar,
        'monto_gastos': p.monto_gastos,
        'status': p.status,
    }


def _propuesta_a_dict(prop: Propuesta) -> dict:
    cliente_display = prop.cliente_rel.razon_social if prop.cliente_rel else (prop.cliente_nombre or '')
    return {
        'id': prop.id,
        'numero': prop.numero,
        'nombre': prop.nombre,
        'status': prop.status,
        'contacto_bgreen': prop.contacto_bgreen,
        'cliente_nombre': prop.cliente_nombre,
        'cliente_id': prop.cliente_id,
        'cliente': cliente_display,
        'contacto_cliente': prop.contacto_cliente,
        'servicio': prop.servicio,
        'detalle_servicio': prop.detalle_servicio,
        'superficie_m2': prop.superficie_m2,
        'unidades': prop.unidades,
        'monto_uf': prop.monto_uf,
        'monto_pesos': prop.monto_pesos,
        'fecha_envio': prop.fecha_envio.strftime('%Y-%m-%d') if prop.fecha_envio else None,
        'fecha_adjudicacion': (
            prop.fecha_adjudicacion.strftime('%Y-%m-%d') if prop.fecha_adjudicacion else None
        ),
    }


def _validar_datos_propuesta(data: dict, empresa_id: int, propuesta_id: int | None = None):
    numero = data.get('numero')
    nombre = (data.get('nombre') or '').strip()
    if numero is None or nombre == '':
        return None, 'Campos requeridos: numero, nombre'
    try:
        numero_int = int(numero)
    except (TypeError, ValueError):
        return None, 'numero inválido'
    if numero_int <= 0:
        return None, 'numero debe ser mayor a 0'

    status = (data.get('status') or 'No enviada').strip()
    if status not in ESTADOS_PROPUESTA:
        return None, f'estado inválido. Use: {", ".join(ESTADOS_PROPUESTA)}'

    cliente_id = data.get('cliente_id')
    if cliente_id:
        cliente = Cliente.query.filter_by(empresa_id=empresa_id, id=int(cliente_id)).first()
        if not cliente:
            return None, 'Cliente no pertenece a la empresa activa'

    q = Propuesta.query.filter_by(empresa_id=empresa_id, numero=numero_int)
    if propuesta_id:
        q = q.filter(Propuesta.id != propuesta_id)
    if q.first():
        return None, f'Ya existe una propuesta con número {numero_int}'

    campos = {
        'numero': numero_int,
        'nombre': nombre[:200],
        'status': status[:30],
        'contacto_bgreen': (data.get('contacto_bgreen') or '')[:100] or None,
        'cliente_nombre': (data.get('cliente_nombre') or '')[:150] or None,
        'cliente_id': int(cliente_id) if cliente_id else None,
        'contacto_cliente': (data.get('contacto_cliente') or '')[:100] or None,
        'servicio': (data.get('servicio') or '')[:100] or None,
        'detalle_servicio': (data.get('detalle_servicio') or '')[:200] or None,
        'superficie_m2': float(data['superficie_m2']) if data.get('superficie_m2') not in (None, '') else None,
        'unidades': float(data['unidades']) if data.get('unidades') not in (None, '') else None,
        'monto_uf': float(data['monto_uf']) if data.get('monto_uf') not in (None, '') else None,
        'monto_pesos': float(data.get('monto_pesos') or 0),
        'fecha_envio': _parse_fecha(data['fecha_envio']) if data.get('fecha_envio') else None,
        'fecha_adjudicacion': (
            _parse_fecha(data['fecha_adjudicacion']) if data.get('fecha_adjudicacion') else None
        ),
    }
    return campos, None


def _movimiento_a_dict(m: Movimiento) -> dict:
    return {
        'id': m.id,
        'clase': m.clase,
        'fecha_movimiento': m.fecha_movimiento.strftime('%Y-%m-%d'),
        'fecha_estado_pago': m.fecha_estado_pago.strftime('%Y-%m-%d') if m.fecha_estado_pago else None,
        'fecha_facturacion': m.fecha_facturacion.strftime('%Y-%m-%d') if m.fecha_facturacion else None,
        'monto': m.monto_pesos,
        'centro_costo': m.centro_costo,
        'estado': m.estado,
        'origen': m.cta_origen.nombre if m.cta_origen else '',
        'destino': m.cta_destino.nombre if m.cta_destino else '',
        'origen_id': m.cta_origen_id,
        'destino_id': m.cta_destino_id,
        'tipo': m.transaccion,
        'descripcion': m.descripcion,
        'numero_factura': m.numero_factura,
        'status_pago': m.status_pago,
        'condicion_pago_dias': m.condicion_pago_dias or 30,
        'proyecto_id': m.proyecto_id,
        'proyecto': m.proyecto_rel.nombre if m.proyecto_rel else None,
        'transaccion': m.transaccion,
    }


def _aplicar_datos_movimiento(mov: Movimiento, data: dict):
    if 'fecha' in data or 'fecha_movimiento' in data:
        fecha = _parse_fecha(data.get('fecha') or data.get('fecha_movimiento'))
        if fecha:
            mov.fecha_movimiento = fecha
    if 'fecha_estado_pago' in data:
        mov.fecha_estado_pago = _parse_fecha(data.get('fecha_estado_pago'))
    if 'fecha_facturacion' in data:
        mov.fecha_facturacion = _parse_fecha(data.get('fecha_facturacion'))
    if 'monto' in data:
        mov.monto_pesos = float(data['monto'])
    if 'descripcion' in data:
        mov.descripcion = data.get('descripcion')
    if 'numero_factura' in data:
        mov.numero_factura = data.get('numero_factura')
    if 'status_pago' in data:
        status = data.get('status_pago') or None
        if mov.clase == 'gasto' and status and status not in STATUS_GASTO:
            abort(400, description=f'Status de gasto no válido: {status}')
        if mov.clase == 'estado_pago' and status and status not in STATUS_PAGO:
            abort(400, description=f'Status de pago no válido: {status}')
        mov.status_pago = status
    if 'condicion_pago_dias' in data:
        mov.condicion_pago_dias = _parse_condicion_pago(data.get('condicion_pago_dias'))
    if 'estado' in data:
        mov.estado = data['estado']
    if 'centro_costo' in data:
        mov.centro_costo = data['centro_costo'][:50]
    if 'origen_id' in data and data['origen_id']:
        mov.cta_origen_id = int(data['origen_id'])
    if 'destino_id' in data and data['destino_id']:
        mov.cta_destino_id = int(data['destino_id'])
    if 'origen_id' in data or 'destino_id' in data:
        origen = Cuenta.query.filter_by(empresa_id=mov.empresa_id, id=mov.cta_origen_id).first()
        destino = Cuenta.query.filter_by(empresa_id=mov.empresa_id, id=mov.cta_destino_id).first()
        if origen and destino:
            mov.transaccion = calcular_transaccion(origen.categoria, destino.categoria)
    if 'proyecto_id' in data:
        pid = data.get('proyecto_id')
        if pid:
            proyecto = Proyecto.query.filter_by(empresa_id=mov.empresa_id, id=int(pid)).first()
            if not proyecto:
                abort(404)
            mov.proyecto_id = proyecto.id
            mov.centro_costo = proyecto.nombre[:50]
        else:
            mov.proyecto_id = None
            if not data.get('centro_costo'):
                mov.centro_costo = 'Administración'


def _crear_estado_pago(proyecto_id: int, data: dict, empresa_id: int) -> Movimiento:
    proyecto = Proyecto.query.filter_by(empresa_id=empresa_id, id=proyecto_id).first_or_404()
    origen = _cuenta_por_nombre(NOMBRE_CUENTA_CLIENTES, empresa_id)
    destino = _cuenta_por_nombre(NOMBRE_CUENTA_BANCO_PESOS, empresa_id)
    mov = Movimiento(
        empresa_id=empresa_id,
        fecha_movimiento=_parse_fecha(data['fecha']),
        fecha_estado_pago=_parse_fecha(data.get('fecha_estado_pago')),
        fecha_facturacion=_parse_fecha(data.get('fecha_facturacion')),
        monto_pesos=float(data['monto']),
        centro_costo=proyecto.nombre,
        clase='estado_pago',
        cta_origen_id=origen.id,
        cta_destino_id=destino.id,
        transaccion='Ingreso',
        descripcion=data.get('descripcion'),
        numero_factura=data.get('numero_factura'),
        status_pago=data.get('status_pago', 'Por enviar'),
        condicion_pago_dias=_parse_condicion_pago(data.get('condicion_pago_dias', 30)),
        proyecto_id=proyecto.id,
    )
    db.session.add(mov)
    return mov


def _crear_gasto(proyecto_id: int, data: dict, empresa_id: int) -> Movimiento:
    proyecto = Proyecto.query.filter_by(empresa_id=empresa_id, id=proyecto_id).first_or_404()
    origen = _cuenta_por_nombre(NOMBRE_CUENTA_BANCO_PESOS, empresa_id)
    destino = Cuenta.query.filter_by(empresa_id=empresa_id, id=int(data['destino_id'])).first_or_404()
    status = data.get('status_pago') or None
    if status and status not in STATUS_GASTO:
        abort(400, description=f'Status de gasto no válido: {status}')
    tipo = calcular_transaccion(origen.categoria, destino.categoria)
    mov = Movimiento(
        empresa_id=empresa_id,
        fecha_movimiento=_parse_fecha(data['fecha']),
        monto_pesos=float(data['monto']),
        centro_costo=proyecto.nombre,
        clase='gasto',
        cta_origen_id=origen.id,
        cta_destino_id=destino.id,
        transaccion=tipo,
        descripcion=data.get('descripcion'),
        proyecto_id=proyecto.id,
        status_pago=status,
    )
    db.session.add(mov)
    return mov


def _status_pago_a_estado_gantt(status_pago: str | None) -> str:
    if status_pago in ('Pagado', 'Cedida'):
        return 'Pagado'
    if status_pago == 'Facturado':
        return 'Facturado'
    return 'Pendiente'


def _estado_gantt_a_status_pago(estado: str) -> str:
    if estado == 'Pagado':
        return 'Pagado'
    if estado == 'Facturado':
        return 'Facturado'
    return 'Por enviar'


def _pago_ya_cobrado(status_pago: str | None) -> bool:
    return status_pago in ('Pagado', 'Cedida')


def _fecha_estimada_ep(m: Movimiento) -> date:
    """Fecha estimada de cobro para Gantt (desde movimiento contable)."""
    if m.fecha_facturacion and m.condicion_pago_dias and m.status_pago == 'Facturado':
        return m.fecha_facturacion + timedelta(days=m.condicion_pago_dias or 0)
    if m.fecha_estado_pago and _pago_ya_cobrado(m.status_pago):
        return m.fecha_estado_pago
    return m.fecha_movimiento


def _estado_pago_gantt_dict(m: Movimiento) -> dict:
    return {
        'id': m.id,
        'movimiento_id': m.id,
        'proyecto_id': m.proyecto_id,
        'proyecto': m.proyecto_rel.nombre if m.proyecto_rel else None,
        'descripcion': m.descripcion,
        'monto': m.monto_pesos,
        'fecha_estimada': _fecha_estimada_ep(m).strftime('%Y-%m-%d'),
        'estado': _status_pago_a_estado_gantt(m.status_pago),
        'fecha_pago_real': m.fecha_estado_pago.strftime('%Y-%m-%d') if m.fecha_estado_pago else None,
        'status_pago': m.status_pago,
    }


def _primer_id_asignados_json(raw) -> int | None:
    if not raw:
        return None
    if isinstance(raw, list):
        for x in raw:
            try:
                return int(x)
            except (TypeError, ValueError):
                continue
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list):
        return None
    for x in parsed:
        try:
            return int(x)
        except (TypeError, ValueError):
            continue
    return None


def _validar_asignado_id_trabajador(raw, empresa_id: int) -> tuple[int | None, str | None]:
    if raw is None or raw == '':
        return None, None
    try:
        tid = int(raw)
    except (TypeError, ValueError):
        return None, 'asignado_id inválido'
    if not Trabajador.query.filter_by(empresa_id=empresa_id, id=tid).first():
        return None, f'trabajador_id inválido: {tid}'
    return tid, None


def _tarea_a_dict(t: TareaEntrega) -> dict:
    nombre = ''
    if t.asignado_id:
        tr = Trabajador.query.filter_by(empresa_id=t.empresa_id, id=t.asignado_id).first()
        if tr:
            nombre = _nombre_display_trabajador(tr)
    return {
        'id': t.id,
        'entrega_id': t.entrega_id,
        'descripcion': t.descripcion or '',
        'asignado_id': t.asignado_id,
        'asignado_nombre': nombre,
        'fecha_limite': t.fecha_limite.strftime('%Y-%m-%d') if t.fecha_limite else None,
        'status': t.status,
    }


def _entrega_a_dict(e: EntregaProgramada, include_tareas: bool = False) -> dict:
    d = {
        'id': e.id,
        'proyecto_id': e.proyecto_id,
        'proyecto': e.proyecto_rel.nombre if e.proyecto_rel else None,
        'fecha_entrega': e.fecha_entrega.strftime('%Y-%m-%d'),
        'descripcion': e.descripcion,
        'status': e.status,
        'tareas_count': len(e.tareas) if e.tareas is not None else 0,
    }
    if include_tareas:
        d['tareas'] = [_tarea_a_dict(t) for t in sorted(e.tareas, key=lambda x: x.id)]
    return d


def _lunes_semana(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _nivel_alerta_dias(dias: int) -> str:
    """Nivel visual de alerta: rojo (urgente/vencido), amarillo (próximo), info (lejano)."""
    if dias < 0 or dias <= 3:
        return 'rojo'
    if dias <= 14:
        return 'amarillo'
    return 'info'


def _actividad_gestion(
    tipo: str,
    fecha: date,
    titulo: str,
    proyecto: str,
    **extra,
) -> dict:
    """Ítem de actividad para el dashboard de gestión."""
    act = {
        'tipo': tipo,
        'fecha': fecha.strftime('%Y-%m-%d'),
        'titulo': titulo or '',
        'proyecto': proyecto or '',
    }
    act.update({k: v for k, v in extra.items() if v is not None})
    return act


def _proyecto_gantt_dict(p: Proyecto) -> dict:
    nombre = (p.nombre or '').strip()
    codigo = nombre
    nombre_corto = nombre
    if ' - ' in nombre:
        codigo, nombre_corto = nombre.split(' - ', 1)
        codigo = codigo.strip()
        nombre_corto = nombre_corto.strip()
    return {
        'id': p.id,
        'codigo': codigo,
        'nombre': nombre_corto,
        'nombre_completo': nombre,
        'activo': p.status == 'Activo',
        'status': p.status,
        'cliente': p.cliente_rel.razon_social if p.cliente_rel else '',
        'monto_contrato': p.monto_contrato,
    }


def _primer_dia_mes(d: date) -> date:
    return d.replace(day=1)


def _ultimo_dia_mes(d: date) -> date:
    if d.month == 12:
        return d.replace(day=31)
    return (d.replace(month=d.month + 1, day=1) - timedelta(days=1))


def _gantt_timeline_rango(
    desde: date | None,
    hasta: date | None,
    estados_pago: list[Movimiento],
    entregas: list[EntregaProgramada],
    granularidad: str = 'week',
) -> tuple[date, date]:
    fechas: list[date] = []
    for m in estados_pago:
        fechas.append(_fecha_estimada_ep(m))
    for e in entregas:
        fechas.append(e.fecha_entrega)

    hoy = date.today()
    pad_antes = {'day': 7, 'week': 28, 'month': 90}.get(granularidad, 28)
    pad_despues = {'day': 21, 'week': 56, 'month': 120}.get(granularidad, 56)
    # Ventana por defecto centrada en hoy (no anclada al milestone más antiguo).
    if desde is None and hasta is None:
        desde = hoy - timedelta(days=pad_antes)
        hasta = hoy + timedelta(days=pad_despues)
    elif desde is None:
        desde = hoy - timedelta(days=pad_antes)
    elif hasta is None:
        hasta = hoy + timedelta(days=pad_despues)

    if granularidad == 'month':
        desde = _primer_dia_mes(desde)
        hasta = _ultimo_dia_mes(hasta)
    elif granularidad == 'day':
        pass
    else:
        desde = _lunes_semana(desde)
        hasta_lunes = _lunes_semana(hasta)
        if hasta > hasta_lunes + timedelta(days=6):
            hasta = hasta_lunes + timedelta(days=13)
        elif hasta > hasta_lunes:
            hasta = hasta_lunes + timedelta(days=6)
        else:
            hasta = hasta_lunes + timedelta(days=6)
    if hasta < desde:
        min_span = {'day': 28, 'week': 84, 'month': 180}.get(granularidad, 84)
        hasta = desde + timedelta(days=min_span)
    return desde, hasta


def _registrar_movimiento_desde_dte(data: dict, dte: dict, empresa_id: int) -> Movimiento:
    """Crea un Movimiento contable tras emitir un DTE (ingreso o por cobrar)."""
    pagado = bool(data.get('pagado', False))
    origen = _cuenta_por_nombre(NOMBRE_CUENTA_CLIENTES, empresa_id)
    destino = _cuenta_por_nombre(NOMBRE_CUENTA_BANCO_PESOS, empresa_id)
    fecha = _parse_fecha(dte.get('fecha') or data.get('fecha')) or datetime.utcnow().date()

    proyecto_id = data.get('proyecto_id')
    centro = data.get('centro_costo', 'Administración')
    clase = 'general'

    if proyecto_id:
        proyecto = Proyecto.query.filter_by(empresa_id=empresa_id, id=int(proyecto_id)).first()
        if proyecto:
            clase = 'estado_pago'
            centro = proyecto.nombre[:50]

    mov = Movimiento(
        empresa_id=empresa_id,
        fecha_movimiento=fecha,
        fecha_facturacion=fecha,
        fecha_estado_pago=fecha if pagado else None,
        monto_pesos=float(data['monto']),
        centro_costo=centro[:50],
        estado='Activo',
        clase=clase,
        cta_origen_id=origen.id,
        cta_destino_id=destino.id,
        transaccion='Ingreso',
        descripcion=data.get('detalle', 'Factura electrónica SII')[:255],
        numero_factura=str(dte['folio']) if dte.get('folio') else None,
        status_pago='Pagado' if pagado else 'Facturado',
        proyecto_id=int(proyecto_id) if proyecto_id else None,
    )
    db.session.add(mov)
    return mov


def _param_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ('1', 'true', 'yes', 'on')


__all__ = [
    'ADMIN_PER_PAGE',
    'FETCH_ALL_PER_PAGE',
    'AUTH_EXEMPT_PREFIXES',
    'CATEGORIAS_CUENTA',
    'CERTIFICADOS_DIR',
    'CERTIFICADO_ALLOWED_EXT',
    'CERTIFICADO_MAX_BYTES',
    'DEFAULT_PER_PAGE',
    'DIAS_MES_REF',
    'ESTADOS_ENTREGA',
    'ESTADOS_EP_GANTT',
    'ESTADOS_LIQUIDACION',
    'ESTADOS_TAREA_ENTREGA',
    'FOTO_ALLOWED_EXT',
    'FOTO_MAX_BYTES',
    'FOTO_MAX_PX',
    'LOGOS_DIR',
    'LOGO_ALLOWED_EXT',
    'LOGO_MAX_BYTES',
    'MAX_PER_PAGE',
    'MONEDAS_CUENTA',
    'MOVIMIENTO_SORT_FIELDS',
    'NOMBRE_CUENTA_BANCO_SANTANDER',
    'NOMBRE_CUENTA_CLIENTES',
    'NOMBRE_CUENTA_GASTO_BANCO',
    'NOMBRE_CUENTA_REMUNERACIONES_POR_PAGAR',
    'PROYECTO_SORT_FIELDS',
    'SECRET_MASK',
    'SERVICIOS',
    'STATUS_GASTO',
    'STATUS_PAGO',
    'TASA_AFP',
    'TRABAJADORES_FOTOS_DIR',
    '_aplicar_campos_secretos',
    '_aplicar_datos_movimiento',
    '_aplicar_datos_trabajador',
    '_aplicar_password_trabajador',
    '_asegurar_certificados_dir',
    '_asegurar_trabajadores_fotos_dir',
    'admin_required',
    '_banco_conexion_a_dict',
    '_calcular_montos_liquidacion',
    '_cargar_env_local',
    '_crear_estado_pago',
    '_crear_gasto',
    '_crear_movimiento_desde_banco',
    '_cuenta_a_dict',
    '_cuenta_en_uso',
    '_cuenta_origen_liquidacion',
    '_cuenta_por_nombre',
    '_descripcion_movimiento_banco',
    '_descripcion_movimiento_liquidacion',
    '_desglose_salud_desde_monto',
    '_desglose_salud_liquidacion',
    '_detalle_calculo_desde_liq',
    '_dias_trabajados_mes',
    '_eliminar_foto_trabajador',
    '_eliminar_empresa',
    '_empresa_a_dict',
    '_empresa_id_request',
    '_empresa_pdf_payload',
    '_enriquecer_detalle_pdf',
    '_entrega_a_dict',
    '_env_fintoc_creds',
    '_env_sii_creds',
    '_es_admin',
    '_establecer_sesion_trabajador',
    '_estado_gantt_a_status_pago',
    '_estado_pago_gantt_dict',
    '_fecha_estimada_ep',
    '_fecha_uf_planilla',
    '_foto_path',
    '_foto_url',
    '_guardar_foto_trabajador',
    '_guardar_logo_empresa',
    '_guardar_uf',
    '_filtrar_movimientos_query',
    '_filtrar_proyectos_query',
    '_fintoc_client_for_conexion',
    '_gantt_timeline_rango',
    '_hash_password',
    '_http_status_sii_error',
    '_ingresar_liquidaciones_a_movimientos',
    '_liquidacion_a_dict',
    '_liquidacion_pdf_payload',
    '_logo_path',
    '_logo_url',
    '_actividad_gestion',
    '_lunes_semana',
    '_nivel_alerta_dias',
    '_marcador_fintoc',
    '_marcador_liquidacion',
    '_mask_secret',
    '_migrar_credenciales_env',
    '_migrar_schema',
    '_migrar_schema_bootstrap',
    '_movimiento_a_dict',
    '_movimiento_banco_duplicado',
    '_movimiento_liquidacion_duplicado',
    '_nombre_completo_trabajador',
    '_normalizar_email',
    '_obtener_cuenta_banco_santander',
    '_obtener_cuentas_banco_empresa',
    '_obtener_o_crear_sii_config',
    '_obtener_uf_para_fecha',
    '_ordenar_movimientos',
    '_ordenar_proyectos',
    '_paginate_query',
    '_paginated_json',
    '_pago_ya_cobrado',
    '_param_bool',
    '_parse_condicion_pago',
    '_parse_fecha',
    '_parse_pagination_args',
    '_primer_dia_mes',
    '_primer_id_asignados_json',
    '_propuesta_a_dict',
    '_proyecto_a_dict',
    '_proyecto_gantt_dict',
    '_recalcular_todos_proyectos',
    '_registrar_movimiento_desde_dte',
    '_requiere_empresa',
    '_resolver_empresa_pdf',
    '_rol_trabajador',
    '_rol_usuario_sesion',
    '_sii_client_for_empresa',
    '_sii_config_a_dict',
    '_sii_creds_dict',
    '_sincronizar_conexion_banco',
    '_solo_digitos',
    '_status_pago_a_estado_gantt',
    '_sueldo_base_clp_trabajador',
    '_tarea_a_dict',
    '_trabajador_a_dict',
    '_trabajador_auth_dict',
    '_trabajadores_con_login',
    '_trabajadores_setup_dicts',
    '_crear_trabajador_setup_minimo',
    '_intentar_importar_trabajadores_setup',
    '_uf_hoy',
    '_ultimo_dia_mes',
    '_usuario_sesion',
    '_validar_asignado_id_trabajador',
    '_validar_datos_cuenta',
    '_validar_datos_propuesta',
    '_validar_datos_trabajador',
    '_valor_uf_a_dict',
    '_verificar_empresa',
    '_verificar_password',
]
