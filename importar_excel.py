"""Importa datos desde MAESTRO bgreenEstudio.xlsx a la base de datos."""

import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

from extensions import db
from models import Cliente, Proyecto, Propuesta, Cuenta, Movimiento, Trabajador, Liquidacion
from bootstrap import (
    create_app,
    ensure_schema,
    sembrar_cuentas_empresa,
    empresa_default_id,
    cuentas_remuneracion,
    uf_hoy,
    NOMBRE_CUENTA_BANCO_PESOS,
    TIPOS_CONTRATO,
    SISTEMAS_SALUD,
    AFPS,
    ESTADOS_PROPUESTA,
)
from contabilidad import calcular_transaccion, recalcular_proyecto
from common import _inferir_clase_movimiento

app = create_app()

DEFAULT_XLSX = Path(__file__).parent / 'MAESTRO bgreenEstudio.xlsx'

CUENTA_ALIASES = {
    'Cta Cte Santander 91345803 pesos': NOMBRE_CUENTA_BANCO_PESOS,
    'Cta Cte Santander F91345803 pesos': NOMBRE_CUENTA_BANCO_PESOS,
}

HEADER_CUENTAS = {'Cta Origen', 'Cta Destino', 'Centro de costo'}

HOJAS_PERSONAL = ('RRHH', 'PERSONAL', 'TRABAJADORES', 'PERSONAS', 'TRABAJADOR')
RRHH_HEADER_ROW = 10  # fila 11 en Excel (encabezados)
RRHH_COL_RUT_EMPRESA = 'RUT Empresa'

MAPEO_COLUMNAS_TRABAJADOR = {
    'rut': ('rut', 'rut trabajador', 'rut_trabajador'),
    'nombre_completo': ('nombre completo', 'nombre_completo'),
    'nombres': ('nombres', 'nombre', 'nombres trabajador'),
    'apellido_paterno': ('apellido paterno', 'apellido_paterno', 'apellido pat', 'ap pat'),
    'apellido_materno': ('apellido materno', 'apellido_materno', 'apellido mat', 'ap mat'),
    'fecha_ingreso': (
        'fecha ingreso', 'fecha_ingreso', 'ingreso', 'fecha inicio',
        'fecha inicio contrato',
    ),
    'tipo_contrato': ('tipo contrato', 'tipo_contrato', 'contrato'),
    'sueldo_base': (
        'sueldo base', 'sueldo_base', 'sueldo', 'remuneracion', 'renta',
        'sueldo base pesos',
    ),
    'sueldo_base_uf': (
        'sueldo base uf', 'sueldo_base_uf', 'sueldo uf', 'renta uf', 'base uf',
    ),
    'banco': ('banco', 'banco deposito', 'banco deposito sueldo', 'banco sueldo'),
    'cuenta_bancaria': (
        'cuenta bancaria', 'cuenta_bancaria', 'cuenta deposito', 'cuenta deposito sueldo',
        'n cuenta', 'numero cuenta', 'cta bancaria',
    ),
    'nombre_isapre': ('isapre', 'nombre isapre', 'nombre_isapre', 'prevision salud'),
    'nombre_plan_isapre': (
        'plan isapre', 'nombre plan', 'nombre plan isapre', 'nombre_plan_isapre', 'plan salud',
    ),
    'afp': ('afp', 'administradora'),
    'sistema_salud': ('sistema salud', 'sistema_salud', 'salud', 'isapre fonasa', 'isapre'),
    'valor_plan_isapre_uf': (
        'plan isapre uf', 'valor_plan_isapre_uf', 'plan isapre', 'uf isapre',
        'monto plan uf',
    ),
    'cuenta_gasto': ('cuenta gasto', 'cuenta_gasto', 'cuenta remuneracion', 'remuneracion cuenta'),
    'grupo': ('grupo',),
    'activo': ('activo',),
}

HOJAS_PROPUESTAS = ('PROPUESTAS', 'propuestas', 'C PROPUESTAS')
PROPUESTAS_HEADER_ROW = 0

MAPEO_COLUMNAS_PROPUESTA = {
    'numero': ('unnamed: 0', 'numero', 'n', 'n°', 'nro'),
    'nombre': ('nombre',),
    'status': ('status', 'estado'),
    'contacto_bgreen': ('contacto b-green', 'contacto_bgreen', 'contacto bgreen'),
    'cliente_nombre': ('cliente',),
    'contacto_cliente': ('contacto', 'contacto cliente'),
    'servicio': ('servicio',),
    'detalle_servicio': ('detalle servicio', 'detalle_servicio', 'detalle'),
    'superficie_m2': ('m2', 'm²', 'superficie'),
    'unidades': ('unidades',),
    'monto_uf': ('uf',),
    'monto_pesos': ('pesos', 'monto', 'monto pesos'),
    'fecha_envio': ('envio', 'envío', 'fecha envio', 'fecha_envio'),
    'fecha_adjudicacion': ('adjudic', 'adjudicacion', 'adjudicación', 'fecha adjudicacion'),
}

MAPEO_COLUMNAS_LIQUIDACION = {
    'dias_trabajados': ('dias trabajados', 'dias_trabajados'),
    'sueldo_base_proporcional': ('sueldo base pesos', 'sueldo base proporcional'),
    'total_imponible': ('total imponible', 'total_imponible'),
    'total_haberes': ('total haberes', 'total_haberes'),
    'total_descuentos': ('total descuentos', 'total_descuentos'),
    'alcance_liquido': ('alcance liquido', 'alcance_liquido', 'liquido'),
}

CORRECCIONES_AFP = {
    'curpum': 'Cuprum',
    'provida': 'ProVida',
    'planvital': 'PlanVital',
}


def _normalizar(texto) -> str:
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ''
    s = str(texto).strip()
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


def _parse_fecha(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    try:
        dt = pd.to_datetime(valor, errors='coerce')
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def _parse_monto(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        m = float(valor)
        return m if m != 0 else None
    except (TypeError, ValueError):
        return None


def _limpiar_rut(valor, fallback_id):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return f'IMP-{fallback_id:05d}'
    rut = re.sub(r'\s+', '', str(valor).strip())
    return rut or f'IMP-{fallback_id:05d}'


def _inferir_categoria(nombre: str) -> tuple[str, str]:
    n = _normalizar(nombre)
    if 'cliente' in n:
        return 'activo_cliente', 'CLP'
    if 'socio' in n:
        return 'patrimonio_socio', 'CLP'
    if 'dolar' in n or '5102965614' in n:
        return 'activo_banco', 'USD'
    if any(k in n for k in ('santander', 'bci', 'banco', 'mon ext', 'xepelin', 'credito', 'gbci')):
        return 'activo_banco', 'CLP'
    if 'otros ingresos' in n:
        return 'ingreso', 'CLP'
    if any(k in n for k in ('remuneracion', 'previred', 'f-22', 'f-29', 'impuesto', 'arriendo', 'gasto', 'rendicion', 'insumo', 'licencia', 'membresia', 'comision', 'subcontrato', 'contador')):
        return 'gasto', 'CLP'
    return 'gasto', 'CLP'


def _mapear_status_proyecto(valor) -> str:
    v = _normalizar(valor)
    if v in ('activo',):
        return 'Activo'
    return 'Archivado'


def _mapear_status_pago(clase: str, row, fecha_ep, fecha_fact) -> str | None:
    if clase != 'estado_pago':
        return None
    v = _normalizar(row.get('estado'))
    if any(k in v for k in ('pagad', 'cobrad')):
        return 'Pagado'
    if 'cedid' in v or 'factor' in v:
        return 'Cedida'
    if 'factur' in v:
        return 'Facturado'
    if 'enviad' in v:
        return 'Enviado'
    if 'programad' in v:
        return 'Programado'
    if 'por enviar' in v or 'pendiente' in v:
        return 'Por enviar'
    if fecha_ep:
        return 'Pagado'
    if fecha_fact:
        return 'Facturado'
    return 'Por enviar'


def _mapear_status_movimiento(valor) -> str:
    v = _normalizar(valor)
    if v in ('archivado', 'anulado', 'cancelado'):
        return 'Archivado'
    return 'Activo'


def _mapear_transaccion(valor) -> str:
    v = _normalizar(valor)
    if 'ingreso' in v:
        return 'Ingreso'
    if 'egreso' in v:
        return 'Egreso'
    return 'Transferencia'


def _seed_cuentas_base(empresa_id: int):
    sembrar_cuentas_empresa(empresa_id)
    db.session.commit()


def _resolver_cuenta(nombre_raw: str, cache: dict, empresa_id: int) -> Cuenta:
    nombre = str(nombre_raw).strip()
    if nombre in HEADER_CUENTAS or not nombre:
        raise ValueError(f'Cuenta inválida: {nombre_raw!r}')
    nombre = CUENTA_ALIASES.get(nombre, nombre)
    if nombre in cache:
        return cache[nombre]
    cuenta = Cuenta.query.filter_by(empresa_id=empresa_id, nombre=nombre).first()
    if not cuenta:
        cat, mon = _inferir_categoria(nombre)
        cuenta = Cuenta(empresa_id=empresa_id, nombre=nombre, categoria=cat, moneda=mon)
        db.session.add(cuenta)
        db.session.flush()
    cache[nombre] = cuenta
    return cuenta


def _buscar_proyecto_id(centro_costo: str, mapa_exacto: dict, mapa_norm: dict):
    if not centro_costo or _normalizar(centro_costo) in ('', 'administracion', 'centro de costo'):
        return None
    cc_norm = _normalizar(centro_costo)
    if cc_norm in mapa_norm:
        return mapa_norm[cc_norm]
    for nombre_norm, pid in mapa_norm.items():
        if nombre_norm and (nombre_norm in cc_norm or cc_norm in nombre_norm):
            return pid
    return None


def _resolver_columna_trabajador(df, campo: str):
    aliases = {_normalizar(a) for a in MAPEO_COLUMNAS_TRABAJADOR[campo]}
    for col in df.columns:
        col_norm = _normalizar(col)
        if col_norm in aliases:
            return col
    return None


def _resolver_columna_liquidacion(df, campo: str):
    aliases = {_normalizar(a) for a in MAPEO_COLUMNAS_LIQUIDACION[campo]}
    for col in df.columns:
        if _normalizar(col) in aliases:
            return col
    return None


def _parse_nombre_completo(texto: str) -> tuple[str, str, str]:
    """Separa nombre completo chileno en nombres, apellido paterno y materno."""
    partes = texto.split()
    if len(partes) >= 3:
        return ' '.join(partes[:-2]), partes[-2], partes[-1]
    if len(partes) == 2:
        return partes[0], partes[1], ''
    if len(partes) == 1:
        return partes[0], partes[0], ''
    return '', '', ''


def _es_fila_rrhh_valida(row, col_rut, col_nombre) -> bool:
    activo_col = None
    for col in row.index:
        if _normalizar(col) == 'activo':
            activo_col = col
            break
    if activo_col is not None:
        activo = _normalizar(row.get(activo_col))
        if activo and activo not in ('yes', 'si', 'sí', '1', 'true', 'activo'):
            return False

    rut = row.get(col_rut) if col_rut else None
    nombre = _texto_celda(row.get(col_nombre)) if col_nombre else ''
    if not nombre and col_rut:
        nombre = _texto_celda(row.get(col_rut))
    if not nombre:
        return False
    if rut is None or (isinstance(rut, float) and pd.isna(rut)):
        return not nombre.isdigit()
    return True


def _leer_hoja_personal_df(path: Path, hoja: str) -> pd.DataFrame:
    if _normalizar(hoja) == 'rrhh':
        df = pd.read_excel(path, sheet_name=hoja, header=RRHH_HEADER_ROW)
        if 'RUT.1' in df.columns:
            df = df.rename(columns={'RUT.1': RRHH_COL_RUT_EMPRESA})
        return df
    return pd.read_excel(path, sheet_name=hoja)


def _texto_celda(valor, default=''):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return default
    s = str(valor).strip()
    return default if not s or s.lower() == 'nan' else s


def _normalizar_sistema_salud(valor) -> str:
    v = _normalizar(valor)
    if not v or v in ('fonasa', 'sin isapre', 'no aplica', 'n/a'):
        return 'Fonasa'
    if 'fonasa' in v and 'isapre' not in v:
        return 'Fonasa'
    return 'Isapre'


def _normalizar_tipo_contrato(valor) -> str:
    v = _normalizar(valor)
    if 'plazo' in v or 'fijo' in v or 'temporal' in v:
        return 'Plazo Fijo'
    return 'Indefinido'


def _normalizar_afp(valor) -> str:
    texto = _texto_celda(valor, 'Habitat')
    texto_norm = _normalizar(texto)
    if texto_norm in CORRECCIONES_AFP:
        return CORRECCIONES_AFP[texto_norm]
    for afp in AFPS:
        if _normalizar(afp) == texto_norm:
            return afp
    for afp in AFPS:
        if texto_norm in _normalizar(afp) or _normalizar(afp) in texto_norm:
            return afp
    return texto[:50] if texto else 'Habitat'


def _resolver_cuenta_por_grupo(grupo_raw, cuentas_rem, cache: dict, indice: int, empresa_id: int) -> int:
    grupo = _texto_celda(grupo_raw).upper()
    if grupo and cuentas_rem:
        letra = grupo[0]
        if letra.isalpha():
            idx = (ord(letra) - ord('A')) % len(cuentas_rem)
            return cuentas_rem[idx].id
    return _resolver_cuenta_remuneracion(None, cuentas_rem, cache, indice, empresa_id)


def _resolver_hoja_personal(path: Path) -> str:
    xl = pd.ExcelFile(path)
    for nombre in HOJAS_PERSONAL:
        if nombre in xl.sheet_names:
            return nombre
    for hoja in xl.sheet_names:
        if _normalizar(hoja) in {_normalizar(h) for h in HOJAS_PERSONAL}:
            return hoja
    raise ValueError(
        f'No se encontró hoja de personal. Use una de: {", ".join(HOJAS_PERSONAL)}'
    )


def _resolver_cuenta_remuneracion(nombre_raw, cuentas_rem, cache: dict, indice: int, empresa_id: int) -> int:
    nombre = _texto_celda(nombre_raw)
    if nombre:
        nombre = CUENTA_ALIASES.get(nombre, nombre)
        if nombre in cache:
            return cache[nombre].id
        cuenta = Cuenta.query.filter_by(empresa_id=empresa_id, nombre=nombre).first()
        if cuenta:
            cache[nombre] = cuenta
            return cuenta.id
    if not cuentas_rem:
        raise ValueError('No hay cuentas de remuneración configuradas')
    cuenta = cuentas_rem[indice % len(cuentas_rem)]
    return cuenta.id


def _upsert_liquidacion_rrhh(
    trabajador: Trabajador,
    row,
    cols_liq: dict,
    mes: int,
    anio: int,
    stats: dict,
):
    """Crea o actualiza liquidación del periodo desde fila RRHH."""
    col_imponible = cols_liq.get('total_imponible')
    col_liquido = cols_liq.get('alcance_liquido')
    if not col_imponible and not col_liquido:
        return

    imponible = _parse_monto(row.get(col_imponible)) if col_imponible else None
    liquido = _parse_monto(row.get(col_liquido)) if col_liquido else None
    if imponible is None and liquido is None:
        return

    col_dias = cols_liq.get('dias_trabajados')
    dias = int(row.get(col_dias) or 30) if col_dias and pd.notna(row.get(col_dias)) else 30

    col_sueldo_prop = cols_liq.get('sueldo_base_proporcional')
    sueldo_prop = _parse_monto(row.get(col_sueldo_prop)) if col_sueldo_prop else None
    if sueldo_prop is None:
        sueldo_prop = trabajador.sueldo_base * dias / 30

    col_haberes = cols_liq.get('total_haberes')
    haberes = _parse_monto(row.get(col_haberes)) if col_haberes else imponible
    if haberes is None:
        haberes = float(sueldo_prop)

    col_desc = cols_liq.get('total_descuentos')
    descuentos = _parse_monto(row.get(col_desc)) if col_desc else None
    if descuentos is None and liquido is not None and haberes is not None:
        descuentos = max(0.0, float(haberes) - float(liquido))
    if descuentos is None:
        descuentos = 0.0

    if liquido is None:
        liquido = float(haberes) - float(descuentos)
    if imponible is None:
        imponible = float(haberes)

    existente = Liquidacion.query.filter_by(
        trabajador_id=trabajador.id, mes=mes, anio=anio,
    ).first()
    campos = {
        'dias_trabajados': dias,
        'sueldo_base_proporcional': float(sueldo_prop),
        'total_imponible': float(imponible),
        'total_haberes': float(haberes),
        'total_descuentos': float(descuentos),
        'alcance_liquido': float(liquido),
    }
    if existente:
        if existente.estado != 'Pagado':
            for k, v in campos.items():
                setattr(existente, k, v)
            stats['liquidaciones_actualizadas'] += 1
        return

    db.session.add(Liquidacion(
        empresa_id=trabajador.empresa_id,
        trabajador_id=trabajador.id,
        mes=mes,
        anio=anio,
        estado='Borrador',
        **campos,
    ))
    stats['liquidaciones'] += 1


def importar_trabajadores_desde_excel(
    path: str | Path = DEFAULT_XLSX,
    actualizar: bool = False,
    mes: int | None = None,
    anio: int | None = None,
    empresa_id: int | None = None,
) -> dict:
    """Importa filas de trabajadores desde hoja RRHH (u otra hoja de personal) del Excel."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'No se encontró el archivo: {path}')

    hoy = datetime.utcnow()
    mes_liq = mes or hoy.month
    anio_liq = anio or hoy.year

    with app.app_context():
        ensure_schema()
        eid = empresa_id or empresa_default_id()
        _seed_cuentas_base(eid)

        hoja = _resolver_hoja_personal(path)
        df = _leer_hoja_personal_df(path, hoja)
        if df.empty:
            return {
                'trabajadores': 0, 'actualizados': 0, 'omitidos': 0,
                'liquidaciones': 0, 'liquidaciones_actualizadas': 0,
                'hoja': hoja, 'columnas': [],
            }

        col_rut = _resolver_columna_trabajador(df, 'rut')
        col_nombre_completo = _resolver_columna_trabajador(df, 'nombre_completo')
        col_nombres = _resolver_columna_trabajador(df, 'nombres')
        col_identificador = col_nombre_completo or col_nombres or col_rut
        if not col_rut and not col_identificador:
            raise ValueError(
                'La hoja debe incluir al menos RUT o Nombre Completo '
                f'(hoja: {hoja}, columnas: {list(df.columns)})'
            )

        cols = {campo: _resolver_columna_trabajador(df, campo) for campo in MAPEO_COLUMNAS_TRABAJADOR}
        cols_liq = {campo: _resolver_columna_liquidacion(df, campo) for campo in MAPEO_COLUMNAS_LIQUIDACION}
        cuentas_rem = cuentas_remuneracion(eid)
        cache_cuentas = {}
        es_rrhh = _normalizar(hoja) == 'rrhh'
        stats = {
            'trabajadores': 0, 'actualizados': 0, 'omitidos': 0,
            'liquidaciones': 0, 'liquidaciones_actualizadas': 0,
            'hoja': hoja,
            'columnas': [str(c) for c in df.columns if not str(c).startswith('Unnamed')],
            'periodo_liquidacion': f'{mes_liq:02d}/{anio_liq}',
        }

        for idx, row in df.iterrows():
            if not _es_fila_rrhh_valida(row, col_rut, col_identificador):
                stats['omitidos'] += 1
                continue

            rut = _limpiar_rut(row.get(col_rut) if col_rut else None, idx + 1)

            nombre_completo = ''
            if col_nombre_completo:
                nombre_completo = _texto_celda(row.get(col_nombre_completo))
            if nombre_completo:
                nombres, ap_pat, ap_mat = _parse_nombre_completo(nombre_completo)
            else:
                nombres = _texto_celda(row.get(col_nombres)) if col_nombres else ''
                ap_pat_col = cols.get('apellido_paterno')
                ap_mat_col = cols.get('apellido_materno')
                ap_pat = _texto_celda(row.get(ap_pat_col)) if ap_pat_col else ''
                ap_mat = _texto_celda(row.get(ap_mat_col)) if ap_mat_col else ''
                if not ap_pat and nombres:
                    partes = nombres.split()
                    if len(partes) > 1:
                        ap_pat = partes[-1]
                        nombres = ' '.join(partes[:-1])
                    else:
                        ap_pat = nombres

            if not nombres:
                stats['omitidos'] += 1
                continue

            fecha_col = cols.get('fecha_ingreso')
            fecha_ingreso = _parse_fecha(row.get(fecha_col)) if fecha_col else None
            if not fecha_ingreso:
                fecha_ingreso = datetime.utcnow().date()

            sueldo_col = cols.get('sueldo_base')
            sueldo_uf_col = cols.get('sueldo_base_uf')
            sueldo_uf = _parse_monto(row.get(sueldo_uf_col)) if sueldo_uf_col else None
            sueldo = _parse_monto(row.get(sueldo_col)) if sueldo_col else None
            uf_info = uf_hoy()
            uf_clp = uf_info['valor']
            if sueldo_uf is not None and sueldo_uf > 0:
                sueldo_base_uf = float(sueldo_uf)
                sueldo = round(sueldo_base_uf * uf_clp)
            elif sueldo is not None:
                sueldo_base_uf = round(sueldo / uf_clp, 4) if uf_clp > 0 else 0.0
            elif not es_rrhh:
                stats['omitidos'] += 1
                continue
            else:
                sueldo = 0.0
                sueldo_base_uf = 0.0

            banco_col = cols.get('banco')
            banco = _texto_celda(row.get(banco_col))[:100] if banco_col else ''
            cuenta_banc_col = cols.get('cuenta_bancaria')
            cuenta_bancaria = _texto_celda(row.get(cuenta_banc_col))[:50] if cuenta_banc_col else ''
            isapre_col = cols.get('nombre_isapre')
            nombre_isapre = _texto_celda(row.get(isapre_col))[:100] if isapre_col else ''
            plan_col = cols.get('nombre_plan_isapre')
            nombre_plan = _texto_celda(row.get(plan_col))[:100] if plan_col else ''

            tipo_col = cols.get('tipo_contrato')
            tipo_contrato = _normalizar_tipo_contrato(row.get(tipo_col)) if tipo_col else 'Indefinido'
            if tipo_contrato not in TIPOS_CONTRATO:
                tipo_contrato = 'Indefinido'

            afp_col = cols.get('afp')
            afp = _normalizar_afp(row.get(afp_col)) if afp_col else 'Habitat'

            salud_col = cols.get('sistema_salud')
            sistema_salud = _normalizar_sistema_salud(row.get(salud_col)) if salud_col else 'Fonasa'

            uf_col = cols.get('valor_plan_isapre_uf')
            valor_uf = float(row.get(uf_col) or 0) if uf_col and pd.notna(row.get(uf_col)) else 0.0

            cuenta_col = cols.get('cuenta_gasto')
            grupo_col = cols.get('grupo')
            indice_cuenta = stats['trabajadores'] + stats['actualizados']
            if cuenta_col and _texto_celda(row.get(cuenta_col)):
                cuenta_id = _resolver_cuenta_remuneracion(
                    row.get(cuenta_col), cuentas_rem, cache_cuentas, indice_cuenta, eid,
                )
            elif grupo_col:
                cuenta_id = _resolver_cuenta_por_grupo(
                    row.get(grupo_col), cuentas_rem, cache_cuentas, indice_cuenta, eid,
                )
            else:
                cuenta_id = _resolver_cuenta_remuneracion(
                    None, cuentas_rem, cache_cuentas, indice_cuenta, eid,
                )

            existente = Trabajador.query.filter_by(empresa_id=eid, rut=rut).first()
            if existente:
                if not actualizar:
                    stats['omitidos'] += 1
                    if es_rrhh:
                        _upsert_liquidacion_rrhh(existente, row, cols_liq, mes_liq, anio_liq, stats)
                    continue
                existente.nombres = nombres[:100]
                existente.apellido_paterno = ap_pat[:100]
                existente.apellido_materno = ap_mat[:100] or None
                existente.fecha_ingreso = fecha_ingreso
                existente.tipo_contrato = tipo_contrato
                existente.sueldo_base = float(sueldo)
                existente.sueldo_base_uf = float(sueldo_base_uf)
                existente.banco = banco or None
                existente.cuenta_bancaria = cuenta_bancaria or None
                existente.nombre_isapre = nombre_isapre or None
                existente.nombre_plan_isapre = nombre_plan or None
                existente.afp = afp
                existente.sistema_salud = sistema_salud
                existente.valor_plan_isapre_uf = valor_uf
                existente.cuenta_gasto_id = cuenta_id
                stats['actualizados'] += 1
                trabajador = existente
            else:
                trabajador = Trabajador(
                    empresa_id=eid,
                    rut=rut[:20],
                    nombres=nombres[:100],
                    apellido_paterno=ap_pat[:100],
                    apellido_materno=ap_mat[:100] or None,
                    fecha_ingreso=fecha_ingreso,
                    tipo_contrato=tipo_contrato,
                    sueldo_base=float(sueldo),
                    sueldo_base_uf=float(sueldo_base_uf),
                    banco=banco or None,
                    cuenta_bancaria=cuenta_bancaria or None,
                    nombre_isapre=nombre_isapre or None,
                    nombre_plan_isapre=nombre_plan or None,
                    afp=afp,
                    sistema_salud=sistema_salud,
                    valor_plan_isapre_uf=valor_uf,
                    cuenta_gasto_id=cuenta_id,
                )
                db.session.add(trabajador)
                db.session.flush()
                stats['trabajadores'] += 1

            if es_rrhh:
                _upsert_liquidacion_rrhh(trabajador, row, cols_liq, mes_liq, anio_liq, stats)

        db.session.commit()
        stats['mensaje'] = 'Importación de personal completada'
        return stats


def importar_desde_excel(
    path: str | Path = DEFAULT_XLSX,
    reset: bool = True,
    empresa_id: int | None = None,
) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'No se encontró el archivo: {path}')

    with app.app_context():
        ensure_schema()
        eid = empresa_id or empresa_default_id()
        _seed_cuentas_base(eid)

        if reset:
            Movimiento.query.filter_by(empresa_id=eid).delete()
            Proyecto.query.filter_by(empresa_id=eid).delete()
            Cliente.query.filter_by(empresa_id=eid).delete()
            db.session.commit()

        stats = {'clientes': 0, 'proyectos': 0, 'movimientos': 0, 'cuentas_nuevas': 0, 'omitidos': 0}

        cuentas_antes = Cuenta.query.filter_by(empresa_id=eid).count()

        # --- CLIENTES ---
        df_c = pd.read_excel(path, sheet_name='CLIENTES')
        mapa_clientes_nombre = {}
        ruts_usados = set()
        for _, row in df_c.iterrows():
            excel_id = int(row['ID']) if pd.notna(row.get('ID')) else stats['clientes'] + 1
            razon = str(row.get('RazonSocialReceptor', '')).strip()
            if not razon or razon.lower() == 'nan':
                continue
            rut = _limpiar_rut(row.get('RutReceptor'), excel_id)
            while rut in ruts_usados:
                rut = f'{rut[:14]}-{excel_id}'[:20]
            ruts_usados.add(rut)
            partes = [str(row.get(c, '')).strip() for c in ('GiroReceptor', 'Direccion', 'Ciudad') if pd.notna(row.get(c))]
            comentarios = ' | '.join(p for p in partes if p and p.lower() != 'nan') or None
            cliente = Cliente(
                empresa_id=eid,
                razon_social=razon[:150],
                rut=rut[:20],
                comentarios=comentarios,
            )
            db.session.add(cliente)
            stats['clientes'] += 1
        db.session.flush()
        for c in Cliente.query.filter_by(empresa_id=eid).all():
            mapa_clientes_nombre[_normalizar(c.razon_social)] = c.id
        db.session.commit()

        # --- PROYECTOS ---
        df_p = pd.read_excel(path, sheet_name='PROYECTOS')
        mapa_proyectos_norm = {}
        for _, row in df_p.iterrows():
            nombre = str(row.get('PROYECTO', '')).strip()
            if not nombre or _normalizar(nombre) in ('administracion', 'nan', ''):
                continue
            cliente_nombre = _normalizar(row.get('Cliente'))
            cliente_id = mapa_clientes_nombre.get(cliente_nombre)
            if not cliente_id:
                for k, cid in mapa_clientes_nombre.items():
                    if cliente_nombre and (k in cliente_nombre or cliente_nombre in k):
                        cliente_id = cid
                        break
            if not cliente_id:
                primero = Cliente.query.filter_by(empresa_id=eid).first()
                cliente_id = primero.id if primero else None
            if not cliente_id:
                stats['omitidos'] += 1
                continue
            superficie = float(row['Area']) if pd.notna(row.get('Area')) else 0.0
            servicio = str(row.get('Servicio', 'Consultoría')).strip()[:100]
            if not servicio or servicio.lower() == 'nan':
                servicio = 'Consultoría'
            proyecto = Proyecto(
                empresa_id=eid,
                nombre=nombre[:150],
                superficie=superficie,
                servicio=servicio,
                status=_mapear_status_proyecto(row.get('Status Proyecto')),
                cliente_id=cliente_id,
            )
            db.session.add(proyecto)
            stats['proyectos'] += 1
        db.session.flush()
        for p in Proyecto.query.filter_by(empresa_id=eid).all():
            mapa_proyectos_norm[_normalizar(p.nombre)] = p.id
        db.session.commit()

        # --- MOVIMIENTOS ---
        df_m = pd.read_excel(path, sheet_name='MOVIMIENTOS', header=0)
        col_names = [
            'flag', 'fecha_ep', 'fecha_fact', 'fecha_mov', 'monto_pesos', 'monto_usd',
            'centro_costo', 'x7', 'estado', 'cta_origen', 'cta_destino', 'transaccion',
            'tipo_ingreso', 'descripcion', 'num_factura', 'empresa', 'resp', 'estado_proy', 'u18', 'u19', 'u20',
        ]
        df_m.columns = col_names[:len(df_m.columns)]
        cache_cuentas = {}

        for _, row in df_m.iterrows():
            monto = _parse_monto(row.get('monto_pesos'))
            fecha_mov = _parse_fecha(row.get('fecha_mov'))
            if monto is None or fecha_mov is None:
                stats['omitidos'] += 1
                continue
            try:
                origen = _resolver_cuenta(row['cta_origen'], cache_cuentas, eid)
                destino = _resolver_cuenta(row['cta_destino'], cache_cuentas, eid)
            except ValueError:
                stats['omitidos'] += 1
                continue

            centro = str(row.get('centro_costo', 'Administración')).strip()
            if _normalizar(centro) in ('', 'centro de costo', 'nan'):
                centro = 'Administración'
            proyecto_id = _buscar_proyecto_id(centro, {}, mapa_proyectos_norm)
            transaccion = _mapear_transaccion(row.get('transaccion'))
            if transaccion == 'Transferencia':
                transaccion = calcular_transaccion(origen.categoria, destino.categoria)

            clase = _inferir_clase_movimiento(transaccion, origen.nombre, destino.nombre, proyecto_id)
            desc = str(row.get('descripcion', '')).strip()
            if desc.lower() == 'nan':
                desc = None
            num_fact = row.get('num_factura')
            if pd.notna(num_fact):
                num_fact = str(num_fact).strip()[:50]
                if num_fact.lower() == 'nan':
                    num_fact = None
            else:
                num_fact = None

            fecha_ep = _parse_fecha(row.get('fecha_ep'))
            fecha_fact = _parse_fecha(row.get('fecha_fact'))
            mov = Movimiento(
                empresa_id=eid,
                fecha_movimiento=fecha_mov,
                fecha_estado_pago=fecha_ep,
                fecha_facturacion=fecha_fact,
                monto_pesos=monto,
                centro_costo=centro[:50],
                estado=_mapear_status_movimiento(row.get('estado')),
                clase=clase,
                cta_origen_id=origen.id,
                cta_destino_id=destino.id,
                transaccion=transaccion,
                descripcion=desc[:255] if desc else None,
                numero_factura=num_fact,
                status_pago=_mapear_status_pago(clase, row, fecha_ep, fecha_fact),
                proyecto_id=proyecto_id,
            )
            db.session.add(mov)
            stats['movimientos'] += 1

        db.session.commit()
        movimientos = Movimiento.query.filter_by(empresa_id=eid).all()
        for p in Proyecto.query.filter_by(empresa_id=eid).all():
            recalcular_proyecto(p, movimientos)
        db.session.commit()

        stats['cuentas_nuevas'] = Cuenta.query.filter_by(empresa_id=eid).count() - cuentas_antes
        stats['mensaje'] = 'Importación completada'
        return stats


def _resolver_columna_propuesta(df, campo: str):
    aliases = {_normalizar(a) for a in MAPEO_COLUMNAS_PROPUESTA[campo]}
    for col in df.columns:
        col_norm = _normalizar(col)
        if col_norm in aliases:
            return col
    return None


def _resolver_hoja_propuestas(path: Path) -> str:
    xl = pd.ExcelFile(path)
    nombres_norm = {_normalizar(n): n for n in xl.sheet_names}
    for candidata in HOJAS_PROPUESTAS:
        key = _normalizar(candidata)
        if key in nombres_norm:
            return nombres_norm[key]
    raise ValueError(f'No se encontró hoja de propuestas. Hojas: {", ".join(xl.sheet_names)}')


def _parse_numero_propuesta(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def _parse_monto_excel(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, str) and valor.strip().startswith('#'):
        return None
    try:
        m = float(valor)
        return m
    except (TypeError, ValueError):
        return None


def _normalizar_status_propuesta(valor) -> str:
    texto = _texto_celda(valor, 'No enviada')
    v = _normalizar(texto)
    if 'adjudic' in v and 'no' in v:
        return 'No Adjudicada'
    if 'adjudic' in v:
        return 'Adjudicada'
    if 'enviad' in v and 'no' in v:
        return 'No enviada'
    if 'enviad' in v:
        return 'Enviada'
    for estado in ESTADOS_PROPUESTA:
        if _normalizar(estado) == v:
            return estado
    return texto[:30] if texto else 'No enviada'


def _mapa_clientes_empresa(empresa_id: int) -> dict:
    mapa = {}
    for c in Cliente.query.filter_by(empresa_id=empresa_id).all():
        mapa[_normalizar(c.razon_social)] = c.id
    return mapa


def _buscar_cliente_id_por_nombre(nombre_raw, mapa_clientes: dict) -> int | None:
    nombre = _normalizar(nombre_raw)
    if not nombre:
        return None
    if nombre in mapa_clientes:
        return mapa_clientes[nombre]
    for clave, cid in mapa_clientes.items():
        if clave and (clave in nombre or nombre in clave):
            return cid
    return None


def _parse_fecha_segura(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    return _parse_fecha(valor)


def _fila_propuesta_a_campos(row, cols: dict, mapa_clientes: dict) -> dict | None:
    col_nombre = cols.get('nombre')
    nombre = _texto_celda(row.get(col_nombre)) if col_nombre else ''
    if not nombre:
        return None

    col_numero = cols.get('numero')
    numero = _parse_numero_propuesta(row.get(col_numero)) if col_numero else None
    if numero is None:
        return None

    cliente_nombre = _texto_celda(row.get(cols.get('cliente_nombre'))) if cols.get('cliente_nombre') else ''
    cliente_id = _buscar_cliente_id_por_nombre(cliente_nombre, mapa_clientes)

    pesos = _parse_monto_excel(row.get(cols.get('monto_pesos'))) if cols.get('monto_pesos') else None
    uf = _parse_monto_excel(row.get(cols.get('monto_uf'))) if cols.get('monto_uf') else None
    m2 = _parse_monto_excel(row.get(cols.get('superficie_m2'))) if cols.get('superficie_m2') else None
    unidades = _parse_monto_excel(row.get(cols.get('unidades'))) if cols.get('unidades') else None

    return {
        'numero': numero,
        'nombre': nombre[:200],
        'status': _normalizar_status_propuesta(row.get(cols.get('status')) if cols.get('status') else None),
        'contacto_bgreen': _texto_celda(row.get(cols.get('contacto_bgreen')))[:100] or None,
        'cliente_nombre': cliente_nombre[:150] or None,
        'cliente_id': cliente_id,
        'contacto_cliente': _texto_celda(row.get(cols.get('contacto_cliente')))[:100] or None,
        'servicio': _texto_celda(row.get(cols.get('servicio')))[:100] or None,
        'detalle_servicio': _texto_celda(row.get(cols.get('detalle_servicio')))[:200] or None,
        'superficie_m2': m2,
        'unidades': unidades,
        'monto_uf': uf,
        'monto_pesos': pesos if pesos is not None else 0.0,
        'fecha_envio': _parse_fecha_segura(row.get(cols.get('fecha_envio'))) if cols.get('fecha_envio') else None,
        'fecha_adjudicacion': (
            _parse_fecha_segura(row.get(cols.get('fecha_adjudicacion')))
            if cols.get('fecha_adjudicacion') else None
        ),
    }


def importar_propuestas_desde_excel(
    path: str | Path = DEFAULT_XLSX,
    actualizar: bool = True,
    empresa_id: int | None = None,
) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f'No se encontró el archivo: {path}')

    with app.app_context():
        ensure_schema()
        eid = empresa_id or empresa_default_id()
        hoja = _resolver_hoja_propuestas(path)
        df = pd.read_excel(path, sheet_name=hoja, header=PROPUESTAS_HEADER_ROW)

        cols = {}
        for campo in MAPEO_COLUMNAS_PROPUESTA:
            cols[campo] = _resolver_columna_propuesta(df, campo)
        if not cols.get('nombre'):
            raise ValueError('La hoja de propuestas no tiene columna Nombre')

        mapa_clientes = _mapa_clientes_empresa(eid)
        stats = {
            'propuestas': 0,
            'actualizadas': 0,
            'omitidas': 0,
            'clientes_vinculados': 0,
            'hoja': hoja,
        }

        for _, row in df.iterrows():
            campos = _fila_propuesta_a_campos(row, cols, mapa_clientes)
            if not campos:
                continue

            existente = Propuesta.query.filter_by(empresa_id=eid, numero=campos['numero']).first()
            if existente:
                if not actualizar:
                    stats['omitidas'] += 1
                    continue
                for clave, valor in campos.items():
                    setattr(existente, clave, valor)
                stats['actualizadas'] += 1
            else:
                db.session.add(Propuesta(empresa_id=eid, **campos))
                stats['propuestas'] += 1

            if campos.get('cliente_id'):
                stats['clientes_vinculados'] += 1

        db.session.commit()
        stats['total'] = Propuesta.query.filter_by(empresa_id=eid).count()
        stats['mensaje'] = 'Importación de propuestas completada'
        return stats


if __name__ == '__main__':
    import sys
    archivo = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSX
    resultado = importar_desde_excel(archivo)
    print(resultado)
