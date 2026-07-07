"""Configuración de BD, semillas y utilidades compartidas (app + scripts CLI)."""

import json
import logging
import os
import re
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

load_dotenv()

from extensions import db, migrate
from models import (
    Cuenta,
    Empresa,
    Liquidacion,
    Movimiento,
    Trabajador,
    ValorUF,
)

UF_REFERENCIA_CLP = float(os.environ.get('UF_REFERENCIA_CLP', '38000'))
NOMBRE_CUENTA_BANCO_PESOS = 'Cta Cte Santander F91345803 pesos'
TIPOS_CONTRATO = ['Indefinido', 'Plazo Fijo']
SISTEMAS_SALUD = ['Fonasa', 'Isapre']
AFPS = ['Habitat', 'Capital', 'Cuprum', 'Modelo', 'PlanVital', 'ProVida', 'Uno']
ESTADOS_PROPUESTA = ['No enviada', 'Enviada', 'Adjudicada', 'No Adjudicada']

EMPRESA_DEFAULT = {
    'rut': '77.748.415-K',
    'nombre': 'Consultora Sustentable',
    'direccion': '',
    'email': '',
    'telefono': '',
    'giro': 'Consultoría ambiental y energética',
}

CUENTAS_INICIALES = [
    ('Clientes', 'activo_cliente', 'CLP'),
    ('Cta Cte Santander F91345803 pesos', 'activo_banco', 'CLP'),
    ('Cta Cte Santander 5102965614 dólares', 'activo_banco', 'USD'),
    ('Socio 1', 'patrimonio_socio', 'CLP'),
    ('Socio 2', 'patrimonio_socio', 'CLP'),
    ('Socio 3', 'patrimonio_socio', 'CLP'),
    ('Factoring', 'pasivo_factoring', 'CLP'),
    ('Remuneracion trabajador 1', 'gasto', 'CLP'),
    ('Remuneracion trabajador 2', 'gasto', 'CLP'),
    ('Remuneracion trabajador 3', 'gasto', 'CLP'),
    ('Remuneracion trabajador 4', 'gasto', 'CLP'),
    ('Remuneracion trabajador 5', 'gasto', 'CLP'),
    ('Remuneracion trabajador 6', 'gasto', 'CLP'),
    ('Previred', 'gasto', 'CLP'),
    ('F-22', 'gasto', 'CLP'),
    ('F-29', 'gasto', 'CLP'),
    ('Otros impuestos', 'gasto', 'CLP'),
    ('Comisiones cobros e intereses', 'gasto', 'CLP'),
    ('Rendiciones de gastos', 'gasto', 'CLP'),
    ('Arriendo', 'gasto', 'CLP'),
    ('Insumos y equipos', 'gasto', 'CLP'),
    ('Cuentas servicios', 'gasto', 'CLP'),
    ('Licencias', 'gasto', 'CLP'),
    ('Membresias', 'gasto', 'CLP'),
    ('Otros gastos', 'gasto', 'CLP'),
]

TABLAS_EMPRESA = (
    'clientes', 'proyectos', 'propuestas', 'cuentas', 'movimientos', 'trabajadores',
    'liquidaciones', 'entregas_programadas', 'tareas_entrega',
)

_app = None
logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """PostgreSQL desde env; SQLite local solo como respaldo rápido sin Docker."""
    database_url = os.environ.get('DATABASE_URL') or os.environ.get('SQLALCHEMY_DATABASE_URI')
    if not database_url:
        database_url = 'sqlite:///' + str(Path(__file__).parent / 'gestion_proyectos.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    return database_url


def init_extensions(flask_app: Flask) -> None:
    db.init_app(flask_app)
    migrate.init_app(flask_app, db)


def configure_app(flask_app: Flask) -> Flask:
    flask_app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-cambiar-en-produccion')
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = get_database_url()
    flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    flask_app.config['SESSION_COOKIE_HTTPONLY'] = True
    flask_app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    is_production = (
        os.environ.get('PRODUCTION', '').strip().lower() in ('1', 'true', 'yes')
        or os.environ.get('FLASK_ENV', '').strip().lower() == 'production'
    )
    if is_production:
        flask_app.config['DEBUG'] = False
        flask_app.config['SESSION_COOKIE_SECURE'] = True
    init_extensions(flask_app)
    return flask_app


def create_app() -> Flask:
    """App Flask mínima para scripts CLI (importar Excel, etc.)."""
    global _app
    if _app is None:
        _app = Flask(__name__, template_folder=str(Path(__file__).parent))
        configure_app(_app)
    return _app


def empresa_default_id() -> int:
    emp = Empresa.query.order_by(Empresa.id).first()
    return emp.id if emp else 1


def asegurar_empresa_default() -> None:
    if Empresa.query.first():
        return
    data = EMPRESA_DEFAULT
    db.session.add(Empresa(
        rut=data['rut'],
        nombre=data['nombre'],
        direccion=data.get('direccion') or None,
        email=data.get('email') or None,
        telefono=data.get('telefono') or None,
        giro=data.get('giro') or None,
        activa=True,
    ))
    db.session.commit()


def sembrar_cuentas_empresa(empresa_id: int) -> None:
    for nombre, categoria, moneda in CUENTAS_INICIALES:
        if not Cuenta.query.filter_by(empresa_id=empresa_id, nombre=nombre).first():
            db.session.add(Cuenta(
                empresa_id=empresa_id,
                nombre=nombre,
                categoria=categoria,
                moneda=moneda,
            ))


def cuentas_remuneracion(empresa_id: int):
    return Cuenta.query.filter(
        Cuenta.empresa_id == empresa_id,
        Cuenta.categoria == 'gasto',
        Cuenta.nombre.like('Remuneracion trabajador%'),
    ).order_by(Cuenta.nombre).all()


_UF_FETCH_TIMEOUT = 10
_UF_MESES_SII = (
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)


def _fetch_uf_mindicador(fecha: date) -> float | None:
    try:
        dd_mm_yyyy = fecha.strftime('%d-%m-%Y')
        resp = requests.get(
            f'https://mindicador.cl/api/uf/{dd_mm_yyyy}',
            timeout=_UF_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        serie = resp.json().get('serie') or []
        if serie:
            return float(serie[0]['valor'])
    except Exception as exc:
        logger.debug('UF mindicador falló para %s: %s', fecha, exc)
    return None


def _parse_uf_sii_html(html: str, fecha: date) -> float | None:
    """Extrae UF diaria del HTML anual de sii.cl/valores_y_fechas/uf/."""
    mes_nombre = _UF_MESES_SII[fecha.month - 1]
    bloque_match = re.search(
        rf"id=['\"]mes_{mes_nombre}['\"][^>]*>(.*?)</div>",
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not bloque_match:
        return None
    for dia_s, valor_s in re.findall(
        r"<th[^>]*><strong>(\d+)</strong></th>\s*<td[^>]*>([^<]*)</td>",
        bloque_match.group(1),
        re.IGNORECASE,
    ):
        if int(dia_s) != fecha.day:
            continue
        valor_s = valor_s.strip()
        if not valor_s:
            return None
        return float(valor_s.replace('.', '').replace(',', '.'))
    return None


def _fetch_uf_sii(fecha: date) -> float | None:
    """Obtiene UF desde tablas HTML del SII (sin API pública JSON)."""
    try:
        url = f'https://www.sii.cl/valores_y_fechas/uf/uf{fecha.year}.htm'
        resp = requests.get(
            url,
            timeout=_UF_FETCH_TIMEOUT,
            headers={'User-Agent': 'MaestroWeb/1.0'},
        )
        resp.raise_for_status()
        return _parse_uf_sii_html(resp.text, fecha)
    except Exception as exc:
        logger.debug('UF SII falló para %s: %s', fecha, exc)
    return None


def _fetch_uf_externa(fecha: date) -> tuple[float | None, str | None]:
    valor = _fetch_uf_mindicador(fecha)
    if valor is not None:
        return valor, 'mindicador'
    valor = _fetch_uf_sii(fecha)
    if valor is not None:
        return valor, 'sii'
    return None, None


def _guardar_uf(fecha: date, valor: float) -> ValorUF:
    reg = ValorUF.query.filter_by(fecha=fecha).first()
    if reg:
        reg.valor = float(valor)
    else:
        reg = ValorUF(fecha=fecha, valor=float(valor))
        db.session.add(reg)
    db.session.commit()
    return reg


def _obtener_uf_para_fecha(
    fecha: date,
    auto_fetch: bool = True,
) -> tuple[float | None, date | None, str | None]:
    reg = ValorUF.query.filter_by(fecha=fecha).first()
    if reg:
        return reg.valor, reg.fecha, 'bd'

    if auto_fetch:
        valor_ext, fuente_ext = _fetch_uf_externa(fecha)
        if valor_ext is not None and fuente_ext is not None:
            reg = _guardar_uf(fecha, valor_ext)
            logger.info('UF %s obtenida desde %s: %s', fecha, fuente_ext, valor_ext)
            return reg.valor, reg.fecha, fuente_ext

    anterior = (
        ValorUF.query.filter(ValorUF.fecha <= fecha)
        .order_by(ValorUF.fecha.desc())
        .first()
    )
    if anterior:
        return anterior.valor, anterior.fecha, 'ultimo_disponible'
    return None, None, None


def uf_hoy() -> dict:
    hoy = date.today()
    valor, fecha_usada, fuente = _obtener_uf_para_fecha(hoy, auto_fetch=True)
    if valor is None:
        ultimo = ValorUF.query.order_by(ValorUF.fecha.desc()).first()
        if ultimo:
            return {
                'valor': ultimo.valor,
                'fecha': ultimo.fecha.strftime('%Y-%m-%d'),
                'es_hoy': ultimo.fecha == hoy,
                'fuente': 'ultimo_registrado',
            }
        return {
            'valor': UF_REFERENCIA_CLP,
            'fecha': hoy.strftime('%Y-%m-%d'),
            'es_hoy': True,
            'fuente': 'respaldo_env',
        }
    return {
        'valor': valor,
        'fecha': fecha_usada.strftime('%Y-%m-%d'),
        'es_hoy': fecha_usada == hoy,
        'fuente': fuente or ('bd' if fecha_usada == hoy else 'ultimo_disponible'),
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


def _migrar_empresa_id_columnas() -> None:
    asegurar_empresa_default()
    default_id = empresa_default_id()
    for tabla in TABLAS_EMPRESA:
        if not inspect(db.engine).has_table(tabla):
            continue
        cols = {c['name'] for c in inspect(db.engine).get_columns(tabla)}
        if 'empresa_id' in cols:
            continue
        with db.engine.connect() as conn:
            conn.execute(text(
                f'ALTER TABLE {tabla} ADD COLUMN empresa_id INTEGER DEFAULT {default_id}',
            ))
            conn.execute(text(
                f'UPDATE {tabla} SET empresa_id = {default_id} WHERE empresa_id IS NULL',
            ))
            conn.commit()


def _tabla_tiene_unique_solo(columna: str, tabla: str) -> bool:
    if not inspect(db.engine).has_table(tabla):
        return False
    with db.engine.connect() as conn:
        row = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=:t",
        ), {'t': tabla}).fetchone()
    if not row or not row[0]:
        return False
    for match in re.finditer(r'UNIQUE\s*\(([^)]+)\)', row[0], re.IGNORECASE):
        cols = [c.strip().lower() for c in match.group(1).split(',')]
        if cols == [columna.lower()]:
            return True
    return False


def _recrear_tabla_empresa_unique(tabla: str, create_sql: str, columnas: str) -> None:
    if not _tabla_tiene_unique_solo(
        {'clientes': 'rut', 'cuentas': 'nombre', 'trabajadores': 'rut'}[tabla],
        tabla,
    ):
        return
    with db.engine.connect() as conn:
        conn.execute(text(f'ALTER TABLE {tabla} RENAME TO _{tabla}_old'))
        conn.execute(text(create_sql))
        conn.execute(text(
            f'INSERT INTO {tabla} ({columnas}) SELECT {columnas} FROM _{tabla}_old',
        ))
        conn.execute(text(f'DROP TABLE _{tabla}_old'))
        conn.commit()


def _migrar_unique_por_empresa() -> None:
    _recrear_tabla_empresa_unique(
        'cuentas',
        '''CREATE TABLE cuentas (
            id INTEGER PRIMARY KEY,
            empresa_id INTEGER NOT NULL DEFAULT 1 REFERENCES empresas(id),
            nombre VARCHAR(100) NOT NULL,
            categoria VARCHAR(50) NOT NULL,
            moneda VARCHAR(3) NOT NULL DEFAULT 'CLP',
            saldo_inicial FLOAT NOT NULL DEFAULT 0,
            UNIQUE (empresa_id, nombre)
        )''',
        'id, empresa_id, nombre, categoria, moneda, saldo_inicial',
    )
    _recrear_tabla_empresa_unique(
        'clientes',
        '''CREATE TABLE clientes (
            id INTEGER PRIMARY KEY,
            empresa_id INTEGER NOT NULL DEFAULT 1 REFERENCES empresas(id),
            razon_social VARCHAR(150) NOT NULL,
            rut VARCHAR(20) NOT NULL,
            comentarios TEXT,
            UNIQUE (empresa_id, rut)
        )''',
        'id, empresa_id, razon_social, rut, comentarios',
    )
    _recrear_tabla_empresa_unique(
        'trabajadores',
        '''CREATE TABLE trabajadores (
            id INTEGER PRIMARY KEY,
            empresa_id INTEGER NOT NULL DEFAULT 1 REFERENCES empresas(id),
            rut VARCHAR(20) NOT NULL,
            apellido_paterno VARCHAR(100) NOT NULL,
            apellido_materno VARCHAR(100),
            nombres VARCHAR(100) NOT NULL,
            fecha_ingreso DATE NOT NULL,
            tipo_contrato VARCHAR(20) NOT NULL,
            sueldo_base FLOAT NOT NULL,
            sueldo_base_uf FLOAT DEFAULT 0,
            banco VARCHAR(100),
            cuenta_bancaria VARCHAR(50),
            nombre_isapre VARCHAR(100),
            nombre_plan_isapre VARCHAR(100),
            afp VARCHAR(50) NOT NULL,
            sistema_salud VARCHAR(20) NOT NULL,
            valor_plan_isapre_uf FLOAT DEFAULT 0,
            cuenta_gasto_id INTEGER NOT NULL REFERENCES cuentas(id),
            UNIQUE (empresa_id, rut)
        )''',
        'id, empresa_id, rut, apellido_paterno, apellido_materno, nombres, '
        'fecha_ingreso, tipo_contrato, sueldo_base, sueldo_base_uf, banco, '
        'cuenta_bancaria, nombre_isapre, nombre_plan_isapre, afp, sistema_salud, '
        'valor_plan_isapre_uf, cuenta_gasto_id',
    )


def _migrar_schema_legacy() -> None:
    """Alteraciones SQLite ad-hoc previas a Flask-Migrate (solo bases legacy)."""
    db.create_all()
    asegurar_empresa_default()
    _migrar_empresa_id_columnas()
    _migrar_unique_por_empresa()
    db.create_all()
    if not inspect(db.engine).has_table('movimientos'):
        return
    columnas = {c['name'] for c in inspect(db.engine).get_columns('movimientos')}
    with db.engine.connect() as conn:
        if 'clase' not in columnas:
            conn.execute(text("ALTER TABLE movimientos ADD COLUMN clase VARCHAR(20) DEFAULT 'general'"))
        if 'status_pago' not in columnas:
            conn.execute(text("ALTER TABLE movimientos ADD COLUMN status_pago VARCHAR(30)"))
        if 'condicion_pago_dias' not in columnas:
            conn.execute(text("ALTER TABLE movimientos ADD COLUMN condicion_pago_dias INTEGER DEFAULT 30"))
        conn.commit()
    for m in Movimiento.query.filter_by(clase='estado_pago').all():
        if not m.status_pago:
            if m.fecha_estado_pago:
                m.status_pago = 'Pagado'
            elif m.fecha_facturacion:
                m.status_pago = 'Facturado'
            else:
                m.status_pago = 'Por enviar'
    db.session.commit()
    db.create_all()
    if inspect(db.engine).has_table('trabajadores'):
        cols_t = {c['name'] for c in inspect(db.engine).get_columns('trabajadores')}
        with db.engine.connect() as conn:
            for col, tipo in (
                ('sueldo_base_uf', 'FLOAT DEFAULT 0'),
                ('banco', 'VARCHAR(100)'),
                ('cuenta_bancaria', 'VARCHAR(50)'),
                ('nombre_isapre', 'VARCHAR(100)'),
                ('nombre_plan_isapre', 'VARCHAR(100)'),
                ('alias', 'VARCHAR(100)'),
                ('email', 'VARCHAR(255)'),
                ('password_hash', 'VARCHAR(255)'),
                ('rol', "VARCHAR(20) DEFAULT 'trabajador'"),
            ):
                if col not in cols_t:
                    conn.execute(text(f'ALTER TABLE trabajadores ADD COLUMN {col} {tipo}'))
            conn.commit()
    if inspect(db.engine).has_table('liquidaciones'):
        cols_l = {c['name'] for c in inspect(db.engine).get_columns('liquidaciones')}
        with db.engine.connect() as conn:
            for col, tipo in (
                ('uf_valor', 'FLOAT'),
                ('sueldo_base_uf', 'FLOAT'),
                ('detalle_calculo', 'TEXT'),
            ):
                if col not in cols_l:
                    conn.execute(text(f'ALTER TABLE liquidaciones ADD COLUMN {col} {tipo}'))
            conn.commit()
    if inspect(db.engine).has_table('empresas'):
        cols_e = {c['name'] for c in inspect(db.engine).get_columns('empresas')}
        with db.engine.connect() as conn:
            if 'logo_filename' not in cols_e:
                conn.execute(text('ALTER TABLE empresas ADD COLUMN logo_filename VARCHAR(100)'))
            conn.commit()
    if inspect(db.engine).has_table('cuentas'):
        cols_c = {c['name'] for c in inspect(db.engine).get_columns('cuentas')}
        with db.engine.connect() as conn:
            if 'saldo_inicial' not in cols_c:
                conn.execute(text('ALTER TABLE cuentas ADD COLUMN saldo_inicial FLOAT DEFAULT 0'))
            conn.commit()
    if inspect(db.engine).has_table('tareas_entrega'):
        cols_te = {c['name'] for c in inspect(db.engine).get_columns('tareas_entrega')}
        with db.engine.connect() as conn:
            if 'asignado_id' not in cols_te:
                conn.execute(text(
                    'ALTER TABLE tareas_entrega ADD COLUMN asignado_id INTEGER REFERENCES trabajadores(id)',
                ))
                conn.commit()
        if 'asignados_ids' in cols_te:
            with db.engine.connect() as conn:
                rows = conn.execute(text(
                    'SELECT id, asignados_ids, asignado_id FROM tareas_entrega',
                )).fetchall()
                for row in rows:
                    if row.asignado_id is not None:
                        continue
                    primer_id = _primer_id_asignados_json(row.asignados_ids)
                    if primer_id is not None:
                        conn.execute(
                            text('UPDATE tareas_entrega SET asignado_id = :aid WHERE id = :id'),
                            {'aid': primer_id, 'id': row.id},
                        )
                conn.commit()
    db.create_all()


DEV_ADMIN_EMAIL = 'javier@b-green.cl'
DEV_ADMIN_RUT = '10.055.191-8'
DEV_ADMIN_NOMBRES = 'Javier'
DEV_ADMIN_APELLIDO = 'Admin'


def _cuenta_gasto_trabajador(empresa_id: int) -> Cuenta | None:
    cuentas = cuentas_remuneracion(empresa_id)
    if cuentas:
        return cuentas[0]
    return Cuenta.query.filter_by(empresa_id=empresa_id, categoria='gasto').first()


def asegurar_admin_desarrollo() -> dict | None:
    """Crea o actualiza el administrador de desarrollo (upsert por email)."""
    if os.environ.get('SEED_DEV_ADMIN', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        logger.info('SEED_DEV_ADMIN desactivado; no se modifica el admin de desarrollo')
        return None

    email = DEV_ADMIN_EMAIL
    password = os.environ.get('DEV_ADMIN_PASSWORD', 'Nathalie')

    empresa_id = empresa_default_id()
    trabajador = Trabajador.query.filter(
        db.func.lower(Trabajador.email) == email,
    ).first()

    accion = 'updated'
    if trabajador is None:
        trabajador = Trabajador.query.filter_by(
            empresa_id=empresa_id,
            rut=DEV_ADMIN_RUT,
        ).first()
        if trabajador is None:
            cuenta = _cuenta_gasto_trabajador(empresa_id)
            if not cuenta:
                raise RuntimeError(
                    f'No hay cuenta de gasto para crear admin en empresa {empresa_id}',
                )
            uf_clp = uf_hoy()['valor']
            sueldo_base = round(uf_clp) if uf_clp > 0 else round(UF_REFERENCIA_CLP)
            trabajador = Trabajador(
                empresa_id=empresa_id,
                rut=DEV_ADMIN_RUT,
                nombres=DEV_ADMIN_NOMBRES,
                apellido_paterno=DEV_ADMIN_APELLIDO,
                fecha_ingreso=date.today(),
                tipo_contrato=TIPOS_CONTRATO[0],
                sueldo_base=sueldo_base,
                sueldo_base_uf=1.0,
                afp=AFPS[0],
                sistema_salud=SISTEMAS_SALUD[0],
                valor_plan_isapre_uf=0.0,
                cuenta_gasto_id=cuenta.id,
            )
            db.session.add(trabajador)
            accion = 'created'

    if not (trabajador.nombres or '').strip():
        trabajador.nombres = DEV_ADMIN_NOMBRES

    trabajador.email = email
    trabajador.password_hash = generate_password_hash(password)
    trabajador.rol = 'admin'
    db.session.flush()

    logger.info(
        'Admin desarrollo %s (id=%s, email=%s)',
        accion,
        trabajador.id,
        email,
    )
    return {
        'accion': accion,
        'trabajador_id': trabajador.id,
        'email': email,
        'empresa_id': trabajador.empresa_id,
        'nombre': f'{trabajador.nombres} {trabajador.apellido_paterno}'.strip(),
        'rol': 'admin',
    }


def ensure_schema_bootstrap() -> None:
    """Semillas mínimas al arrancar (empresa + cuentas)."""
    from services.plan_cuentas_seed import sembrar_plan_cuentas_sii

    asegurar_empresa_default()
    default_id = empresa_default_id()
    sembrar_cuentas_empresa(default_id)
    sembrar_plan_cuentas_sii(default_id)
    asegurar_admin_desarrollo()
    db.session.commit()


def ensure_schema() -> None:
    """Esquema: Flask-Migrate en Postgres; legacy SQLite si no hay alembic_version."""
    tiene_alembic = inspect(db.engine).has_table('alembic_version')
    if not tiene_alembic:
        logger.warning(
            'Sin alembic_version: aplicando migración legacy SQLite. '
            'En Postgres ejecute `flask db upgrade` antes de importar.',
        )
        _migrar_schema_legacy()
    else:
        if not inspect(db.engine).has_table('empresas'):
            logger.warning(
                'alembic_version presente pero el esquema falta; ejecute `flask db upgrade`. '
                'Aplicando db.create_all() como respaldo.',
            )
            db.create_all()
        asegurar_empresa_default()
    ensure_schema_bootstrap()
