"""Liquidación de sueldo alineada al libro de remuneraciones oficial.

Indicadores de septiembre 2026 (Previred / SII), verificados contra
LibroRemuneraciones del período 09/2026:

- UF del último día del mes.
- Tope AFP y salud: 90 UF desde las remuneraciones de febrero 2026.
- Tope seguro de cesantía: 135,2 UF.
- AFP trabajador = 10 % + comisión de la AFP, sobre la base topeada.
- Salud = 7 % de esa base; el adicional es el plan Isapre que excede el 7 %.
- Gratificación legal Art. 50: 25 % del sueldo, con tope mensual de 4,75 IMM / 12.
- Cesantía trabajador: 0,6 % en indefinido; 0 % en plazo fijo y al cumplir 11 años.
- Impuesto único de segunda categoría sobre la base tributable del libro.
- La base tributable descuenta la cotización AFP (con comisión), la cesantía
  y la salud hasta el 7 % del tope imponible. Con eso el adicional Isapre
  entra a la base solo cuando el plan no supera ese tope, que es lo que
  cuadra con el libro oficial.
"""
from __future__ import annotations

import calendar
import unicodedata
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

# 10 % cotización obligatoria + comisión vigente (Superintendencia de Pensiones).
_COMISION_AFP = {
    'capital': Decimal('0.0144'),
    'cuprum': Decimal('0.0144'),
    'habitat': Decimal('0.0127'),
    'modelo': Decimal('0.0058'),
    'planvital': Decimal('0.0116'),
    'provida': Decimal('0.0145'),
    'uno': Decimal('0.0046'),
}
_COMISION_AFP_DEFECTO = Decimal('0.01')
_TASA_COTIZACION_AFP = Decimal('0.10')
_TASA_SALUD = Decimal('0.07')
_TASA_SIS = Decimal('0.0178')
_TASA_CESANTIA_TRABAJADOR = Decimal('0.006')
_TASA_CESANTIA_EMPLEADOR = Decimal('0.024')
_TASA_CESANTIA_EMPLEADOR_11_ANIOS = Decimal('0.008')
_TASA_CESANTIA_EMPLEADOR_PLAZO_FIJO = Decimal('0.03')
_ANIOS_EXENCION_CESANTIA_TRABAJADOR = 11
_GRATIFICACION_PORCION = Decimal('0.25')
_GRATIFICACION_IMM_ANUALES = Decimal('4.75')
_DIAS_MES = Decimal('30')

# (desde UTM inclusive, hasta UTM inclusive, factor, rebaja en UTM)
_TRAMOS_IMPUESTO = (
    (Decimal('0'), Decimal('13.5'), Decimal('0'), Decimal('0')),
    (Decimal('13.5'), Decimal('30'), Decimal('0.04'), Decimal('0.54')),
    (Decimal('30'), Decimal('50'), Decimal('0.08'), Decimal('1.74')),
    (Decimal('50'), Decimal('70'), Decimal('0.135'), Decimal('4.49')),
    (Decimal('70'), Decimal('90'), Decimal('0.23'), Decimal('11.14')),
    (Decimal('90'), Decimal('120'), Decimal('0.304'), Decimal('17.80')),
    (Decimal('120'), Decimal('310'), Decimal('0.35'), Decimal('23.32')),
    (Decimal('310'), Decimal('999999'), Decimal('0.40'), Decimal('38.82')),
)

# UTM publicada por el SII. Meses posteriores se rechazan al generar la planilla
# si no están en esta tabla.
_UTM = {
    (2026, 1): 69751,
    (2026, 2): 69611,
    (2026, 3): 69889,
    (2026, 4): 69889,
    (2026, 5): 70588,
    (2026, 6): 71506,
    (2026, 7): 71649,
    (2026, 8): 71649,
    (2026, 9): 71721,
}


def utm_periodo(mes: int, anio: int) -> int | None:
    valor = _UTM.get((int(anio), int(mes)))
    return int(valor) if valor else None


def ingreso_minimo_mensual(mes: int, anio: int) -> int:
    """IMM de 18 a 65 años. Rige el tope de la gratificación legal."""
    periodo = (int(anio), int(mes))
    if periodo >= (2026, 5):
        return 553_553
    if periodo >= (2026, 1):
        return 539_000
    return 539_000


def topes_imponibles_uf(mes: int, anio: int) -> tuple[Decimal, Decimal]:
    """(tope AFP/salud en UF, tope seguro de cesantía en UF)."""
    periodo = (int(anio), int(mes))
    if periodo >= (2026, 2):
        return Decimal('90'), Decimal('135.2')
    if periodo >= (2025, 2):
        return Decimal('87.8'), Decimal('131.9')
    return Decimal('84.3'), Decimal('126.6')


def _clave(texto: str) -> str:
    s = unicodedata.normalize('NFKD', texto or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ''.join(ch for ch in s.lower() if ch.isalnum())


def _dinero(valor) -> Decimal:
    """Pesos o UF con los decimales publicados, sin ruido binario."""
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, int):
        return Decimal(valor)
    return Decimal(str(valor).strip() or '0')


def _uf(valor) -> Decimal:
    return _dinero(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _clp(valor: Decimal) -> int:
    return int(valor.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def comision_afp(nombre: str) -> Decimal:
    return _COMISION_AFP.get(_clave(nombre), _COMISION_AFP_DEFECTO)


def tasa_afp_trabajador(nombre: str) -> Decimal:
    return _TASA_COTIZACION_AFP + comision_afp(nombre)


def _contrato_plazo_fijo(tipo_contrato: str) -> bool:
    clave = _clave(tipo_contrato)
    return 'fijo' in clave or 'obra' in clave or 'faena' in clave


def _cotiza_salud(sistema_salud: str) -> bool:
    clave = _clave(sistema_salud)
    return clave not in ('nocotiza', 'sinisapre', 'sinsistema', 'ninguno', '')


def _es_isapre(sistema_salud: str) -> bool:
    return _clave(sistema_salud) == 'isapre'


def _cumple_11_anios(fecha_ingreso: date | None, mes: int, anio: int) -> bool:
    """Cesantía del trabajador pasa a 0 % al cumplir 11 años con el mismo empleador."""
    if fecha_ingreso is None:
        return False
    try:
        aniversario = fecha_ingreso.replace(year=fecha_ingreso.year + _ANIOS_EXENCION_CESANTIA_TRABAJADOR)
    except ValueError:
        aniversario = fecha_ingreso.replace(
            year=fecha_ingreso.year + _ANIOS_EXENCION_CESANTIA_TRABAJADOR,
            day=28,
        )
    ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
    return aniversario <= ultimo


def tasas_cesantia(
    tipo_contrato: str,
    fecha_ingreso: date | None,
    mes: int,
    anio: int,
    afecto: bool,
) -> tuple[Decimal, Decimal]:
    """(tasa trabajador, tasa empleador) sobre la base de cesantía."""
    if not afecto:
        return Decimal('0'), Decimal('0')
    if _contrato_plazo_fijo(tipo_contrato):
        return Decimal('0'), _TASA_CESANTIA_EMPLEADOR_PLAZO_FIJO
    if _cumple_11_anios(fecha_ingreso, mes, anio):
        return Decimal('0'), _TASA_CESANTIA_EMPLEADOR_11_ANIOS
    return _TASA_CESANTIA_TRABAJADOR, _TASA_CESANTIA_EMPLEADOR


def tope_gratificacion_mensual(mes: int, anio: int) -> int:
    imm = Decimal(ingreso_minimo_mensual(mes, anio))
    return _clp(imm * _GRATIFICACION_IMM_ANUALES / Decimal(12))


def impuesto_unico(tributable: int, utm: int) -> int:
    """Tabla mensual del SII. El tramo llega hasta su tope, inclusive."""
    if tributable <= 0 or utm <= 0:
        return 0
    renta_utm = Decimal(tributable) / Decimal(utm)
    factor = _TRAMOS_IMPUESTO[-1][2]
    rebaja = _TRAMOS_IMPUESTO[-1][3]
    for _desde, hasta, factor_tramo, rebaja_tramo in _TRAMOS_IMPUESTO:
        if renta_utm <= hasta:
            factor = factor_tramo
            rebaja = rebaja_tramo
            break
    if factor == 0:
        return 0
    monto = Decimal(tributable) * factor - rebaja * Decimal(utm)
    if monto <= 0:
        return 0
    return _clp(monto)


def calcular_remuneracion(
    *,
    sueldo_base_uf: float,
    dias_trabajados: int,
    uf_clp: float,
    afp: str,
    sistema_salud: str,
    valor_plan_isapre_uf: float = 0,
    tipo_contrato: str = 'Indefinido',
    fecha_ingreso: date | None = None,
    mes: int,
    anio: int,
    paga_gratificacion: bool = True,
    afecto_cesantia: bool = True,
    sis_cargo_trabajador: bool = False,
    uf_fecha: date | None = None,
    utm_clp: int | None = None,
    aportes_empleador_fn=None,
) -> dict:
    """Devuelve el mismo esquema que la liquidación histórica, con el desglose del libro."""
    uf = _uf(uf_clp)
    uf_fecha_txt = (uf_fecha or date(anio, mes, calendar.monthrange(anio, mes)[1])).isoformat()
    utm = int(utm_clp) if utm_clp else utm_periodo(mes, anio)
    tasa_afp = tasa_afp_trabajador(afp)
    vacio_detalle = {
        'uf_valor': float(uf),
        'uf_fecha': uf_fecha_txt,
        'utm_valor': utm or 0,
        'sueldo_base_uf': float(sueldo_base_uf or 0),
        'sueldo_base_clp': 0.0,
        'dias_trabajados': 0,
        'dias_mes_ref': 30,
        'sueldo_proporcional_clp': 0.0,
        'gratificacion': 0.0,
        'afp_pct': float(tasa_afp * 100),
        'descuento_afp': 0.0,
        'descuento_sis': 0.0,
        'descuento_salud': 0.0,
        'descuento_salud_cotizacion': 0.0,
        'descuento_adicional_salud': 0.0,
        'descuento_cesantia': 0.0,
        'cesantia_pct': 0.0,
        'impuesto_unico': 0.0,
        'valor_plan_uf': float(valor_plan_isapre_uf or 0),
        'fonasa_pct': 7,
        'haberes_imponibles': [],
        'haberes_no_imponibles': [],
        'total_no_imponible': 0.0,
        'total_imponible': 0.0,
        'base_imponible': 0.0,
        'total_haberes': 0.0,
        'total_descuentos': 0.0,
        'total_tributable': 0.0,
        'alcance_liquido': 0.0,
        'aportes_empleador': {'total': 0.0},
    }
    vacio = {
        'dias_trabajados': 0,
        'sueldo_base_proporcional': 0.0,
        'total_imponible': 0.0,
        'total_haberes': 0.0,
        'total_descuentos': 0.0,
        'alcance_liquido': 0.0,
        'uf_valor': float(uf),
        'sueldo_base_uf': float(sueldo_base_uf or 0),
        'detalle': vacio_detalle,
    }
    if dias_trabajados <= 0 or uf <= 0:
        return vacio

    dias = Decimal(int(dias_trabajados))
    sueldo_uf = _dinero(sueldo_base_uf or 0)
    sueldo_clp = _clp(sueldo_uf * uf)
    sueldo_prop = _clp(Decimal(sueldo_clp) * dias / _DIAS_MES)

    gratificacion = 0
    if paga_gratificacion and sueldo_prop > 0:
        tope_mes = tope_gratificacion_mensual(mes, anio)
        tope = _clp(Decimal(tope_mes) * dias / _DIAS_MES)
        gratificacion = min(_clp(Decimal(sueldo_prop) * _GRATIFICACION_PORCION), tope)

    haberes = sueldo_prop + gratificacion
    tope_afp_uf, tope_ces_uf = topes_imponibles_uf(mes, anio)
    tope_afp = _clp(tope_afp_uf * uf)
    tope_ces = _clp(tope_ces_uf * uf)
    base_afp = min(haberes, tope_afp)
    base_ces = min(haberes, tope_ces)

    descuento_afp = _clp(Decimal(base_afp) * tasa_afp)
    descuento_sis = _clp(Decimal(base_afp) * _TASA_SIS) if sis_cargo_trabajador else 0

    if _cotiza_salud(sistema_salud):
        cotiz_salud = _clp(Decimal(base_afp) * _TASA_SALUD)
    else:
        cotiz_salud = 0
    adicional = 0
    if _es_isapre(sistema_salud):
        plan_clp = _clp(_dinero(valor_plan_isapre_uf or 0) * uf)
        if plan_clp > cotiz_salud:
            adicional = plan_clp - cotiz_salud
    descuento_salud = cotiz_salud + adicional

    tasa_ces_trab, tasa_ces_emp = tasas_cesantia(
        tipo_contrato, fecha_ingreso, mes, anio, afecto_cesantia,
    )
    descuento_cesantia = _clp(Decimal(base_ces) * tasa_ces_trab) if tasa_ces_trab else 0

    tope_salud_deducible = _clp(Decimal(tope_afp) * _TASA_SALUD)
    salud_deducible = min(descuento_salud, tope_salud_deducible)
    tributable = haberes - (descuento_afp + descuento_sis) - salud_deducible - descuento_cesantia
    if tributable < 0:
        tributable = 0
    impuesto = impuesto_unico(tributable, utm or 0)

    total_descuentos = (
        descuento_afp + descuento_sis + descuento_salud + descuento_cesantia + impuesto
    )
    liquido = haberes - total_descuentos

    if aportes_empleador_fn is not None:
        aportes = dict(aportes_empleador_fn(float(base_afp), mes, anio))
    else:
        aportes = {'total': 0.0}
    if sis_cargo_trabajador:
        total_previo = float(aportes.get('total') or 0) - float(aportes.get('sis') or 0)
        aportes['sis'] = 0.0
        aportes['sis_pct'] = 0.0
        aportes['total'] = float(total_previo)
    cesantia_empleador = _clp(Decimal(base_ces) * tasa_ces_emp) if tasa_ces_emp else 0
    aportes['cesantia_pct'] = float((tasa_ces_emp * 100).quantize(Decimal('0.01')))
    aportes['cesantia'] = float(cesantia_empleador)
    aportes['total'] = float(aportes.get('total') or 0) + cesantia_empleador

    haberes_imp = [{
        'concepto': f'SUELDO BASE {int(dias_trabajados)} DIAS',
        'monto': float(sueldo_prop),
    }]
    if gratificacion:
        haberes_imp.append({'concepto': 'GRATIFICACION LEGAL', 'monto': float(gratificacion)})

    detalle = {
        'uf_valor': float(uf),
        'uf_fecha': uf_fecha_txt,
        'utm_valor': utm or 0,
        'sueldo_base_uf': float(sueldo_uf),
        'sueldo_base_clp': float(sueldo_clp),
        'dias_trabajados': int(dias_trabajados),
        'dias_mes_ref': 30,
        'sueldo_proporcional_clp': float(sueldo_prop),
        'gratificacion': float(gratificacion),
        'afp_pct': float((tasa_afp * 100).quantize(Decimal('0.01'))),
        'descuento_afp': float(descuento_afp),
        'descuento_sis': float(descuento_sis),
        'descuento_salud': float(descuento_salud),
        'descuento_salud_cotizacion': float(cotiz_salud),
        'descuento_adicional_salud': float(adicional),
        'descuento_cesantia': float(descuento_cesantia),
        'cesantia_pct': float((tasa_ces_trab * 100).quantize(Decimal('0.01'))),
        'impuesto_unico': float(impuesto),
        'valor_plan_uf': float(valor_plan_isapre_uf or 0),
        'fonasa_pct': 7,
        'haberes_imponibles': haberes_imp,
        'haberes_no_imponibles': [],
        'total_no_imponible': 0.0,
        'total_imponible': float(haberes),
        'base_imponible': float(base_afp),
        'total_haberes': float(haberes),
        'total_descuentos': float(total_descuentos),
        'total_tributable': float(tributable),
        'alcance_liquido': float(liquido),
        'aportes_empleador': aportes,
    }
    return {
        'dias_trabajados': int(dias_trabajados),
        'sueldo_base_proporcional': float(sueldo_prop),
        'total_imponible': float(haberes),
        'total_haberes': float(haberes),
        'total_descuentos': float(total_descuentos),
        'alcance_liquido': float(liquido),
        'uf_valor': float(uf),
        'sueldo_base_uf': float(sueldo_uf),
        'detalle': detalle,
    }
