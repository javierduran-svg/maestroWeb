"""Generación de archivo TXT Previred (formato estándar 105 campos, separador ;)."""
from __future__ import annotations

import json
import os
import re
import unicodedata


# Códigos AFP según Tabla N°10 Previred
CODIGOS_AFP = {
    'cuprum': '03',
    'habitat': '05',
    'provida': '08',
    'planvital': '29',
    'capital': '33',
    'modelo': '34',
    'uno': '35',
}

# Códigos institución de salud según Tabla N°16 Previred
CODIGOS_SALUD = {
    'banmedica': '01',
    'banmédica': '01',
    'consalud': '02',
    'vidatres': '03',
    'vida tres': '03',
    'colmena': '04',
    'cruz blanca': '05',
    'fonasa': '07',
    'nueva masvida': '10',
    'masvida': '10',
    'isalud': '11',
    'fundacion': '12',
    'fundación': '12',
    'cruz del norte': '25',
    'esencial': '28',
}

NUM_CAMPOS = 105


def _normalizar(texto: str) -> str:
    s = unicodedata.normalize('NFKD', str(texto or ''))
    return ''.join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _split_rut(rut: str) -> tuple[str, str]:
    limpio = re.sub(r'[\s.]', '', str(rut or '')).upper()
    if '-' in limpio:
        cuerpo, dv = limpio.rsplit('-', 1)
    elif len(limpio) > 1:
        cuerpo, dv = limpio[:-1], limpio[-1]
    else:
        cuerpo, dv = limpio, ''
    cuerpo = re.sub(r'\D', '', cuerpo)
    return cuerpo, dv[:1]


def _campo_num(valor: int | float | None) -> str:
    if valor is None:
        return '0'
    try:
        return str(int(round(float(valor))))
    except (TypeError, ValueError):
        return '0'


def _campo_alfa(valor: str | None, max_len: int = 30) -> str:
    texto = str(valor or '').strip()
    for a, b in (
        ('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u'),
        ('Á', 'A'), ('É', 'E'), ('Í', 'I'), ('Ó', 'O'), ('Ú', 'U'),
        ('ñ', 'n'), ('Ñ', 'N'), ('ü', 'u'), ('Ü', 'U'),
    ):
        texto = texto.replace(a, b)
    return texto[:max_len]


def _periodo_mmaaaa(mes: int, anio: int) -> str:
    return f'{mes:02d}{anio}'


def _codigo_afp(nombre: str) -> str:
    clave = _normalizar(nombre)
    for patron, codigo in CODIGOS_AFP.items():
        if patron in clave:
            return codigo
    return '00'


def _codigo_salud(trabajador) -> str:
    sistema = _normalizar(getattr(trabajador, 'sistema_salud', '') or '')
    if sistema == 'fonasa':
        return '07'
    nombre = _normalizar(getattr(trabajador, 'nombre_isapre', '') or '')
    for patron, codigo in CODIGOS_SALUD.items():
        if patron in nombre:
            return codigo
    return '00'


def _detalle_liquidacion(liq) -> dict:
    if liq.detalle_calculo:
        try:
            return json.loads(liq.detalle_calculo)
        except Exception:
            pass
    return {}


def _linea_trabajador(liq, mes: int, anio: int) -> str:
    """Construye una línea de 105 campos para un trabajador."""
    t = liq.trabajador_rel
    if not t:
        return ''

    det = _detalle_liquidacion(liq)
    rut_cuerpo, rut_dv = _split_rut(t.rut)
    periodo = _periodo_mmaaaa(mes, anio)
    dias = int(liq.dias_trabajados or det.get('dias_trabajados') or 0)
    imponible = int(round(float(liq.total_imponible or det.get('total_imponible') or 0)))
    cotiz_afp = int(round(float(det.get('descuento_afp') or imponible * 0.11)))
    cotiz_salud_7 = int(round(float(det.get('descuento_salud_cotizacion') or 0)))
    cotiz_adicional = int(round(float(det.get('descuento_adicional_salud') or 0)))
    plan_uf = float(det.get('valor_plan_uf') or t.valor_plan_isapre_uf or 0)
    es_isapre = _normalizar(t.sistema_salud) == 'isapre'
    cod_salud = _codigo_salud(t)
    es_fonasa = cod_salud == '07'

    campos: list[str] = [''] * NUM_CAMPOS

    # 1- Datos del trabajador (campos 1-25)
    campos[0] = rut_cuerpo.zfill(11) if rut_cuerpo else '0'
    campos[1] = _campo_alfa(rut_dv, 1)
    campos[2] = _campo_alfa(t.apellido_paterno)
    campos[3] = _campo_alfa(t.apellido_materno or '')
    campos[4] = _campo_alfa(t.nombres)
    campos[5] = 'M'  # sexo no registrado en modelo
    campos[6] = '0'  # chileno
    campos[7] = '01'  # remuneraciones del mes
    campos[8] = periodo
    campos[9] = periodo
    campos[10] = 'AFP'
    campos[11] = '0'  # activo
    campos[12] = _campo_num(dias)
    campos[13] = '00'  # línea principal
    campos[14] = '0'  # sin movimiento de personal
    # 16-17 fechas movimiento: vacío
    campos[17] = 'D'  # sin derecho asignación familiar
    # 19-24 numéricos en cero por defecto
    for i in range(18, 25):
        campos[i] = '0'
    campos[24] = 'N'  # sin subsidio trabajador joven

    # 2- Datos AFP (26-39)
    campos[25] = _codigo_afp(t.afp)
    campos[26] = _campo_num(imponible)
    campos[27] = _campo_num(cotiz_afp)
    for i in range(28, 39):
        campos[i] = '0'

    # 3-5 APVI/APVC/Afiliado voluntario (40-61)
    for i in range(39, 61):
        campos[i] = '0'

    # 6- IPS / ISL / Fonasa (62-74)
    campos[61] = '0000'
    for i in range(62, 69):
        campos[i] = '0'
    if es_fonasa:
        campos[69] = _campo_num(cotiz_salud_7 or int(round(imponible * 0.07)))
    else:
        campos[69] = '0'
    for i in range(70, 74):
        campos[i] = '0'

    # 7- Salud / Isapre (75-82)
    campos[74] = cod_salud
    campos[75] = _campo_alfa(t.nombre_plan_isapre or '', 16)
    if es_isapre:
        campos[76] = _campo_num(imponible)
        campos[77] = '2' if plan_uf > 0 else '1'  # UF o pesos
        if plan_uf > 0:
            plan_clp = int(round(plan_uf * float(liq.uf_valor or det.get('uf_valor') or 0)))
            campos[78] = _campo_num(plan_clp or cotiz_salud_7 + cotiz_adicional)
        else:
            campos[78] = _campo_num(cotiz_salud_7 + cotiz_adicional)
        campos[79] = _campo_num(cotiz_salud_7 or int(round(imponible * 0.07)))
        campos[80] = _campo_num(cotiz_adicional)
    else:
        for i in range(76, 81):
            campos[i] = '0'
    campos[81] = '0'  # GES uso futuro

    # 8-12 CCAF, Mutual, Cesantía, Subsidios, Centro costos (83-105)
    for i in range(82, 104):
        campos[i] = '0'
    campos[104] = ''  # centro de costos alfanumérico

    return ';'.join(campos)


class PreviredFileGenerator:
    """Genera archivo TXT de nómina para carga en Previred (105 campos, separador ;)."""

    def __init__(self, empresa_rut: str | None = None):
        self.empresa_rut = empresa_rut or os.environ.get(
            'SII_RUT_EMISOR',
            os.environ.get('EMPRESA_RUT', '77748415-K'),
        )

    def generar_txt(self, mes: int, anio: int, liquidaciones: list) -> str:
        """Arma el contenido TXT a partir de liquidaciones ya consultadas."""
        if not (1 <= mes <= 12):
            raise ValueError('mes debe estar entre 1 y 12')
        if anio < 2000:
            raise ValueError('anio inválido')

        liquidaciones = sorted(liquidaciones, key=lambda l: (
            l.trabajador_rel.apellido_paterno if l.trabajador_rel else '',
            l.trabajador_rel.nombres if l.trabajador_rel else '',
        ))

        if not liquidaciones:
            raise ValueError(f'No hay liquidaciones para {mes:02d}/{anio}')

        lineas = []
        for liq in liquidaciones:
            if liq.dias_trabajados <= 0:
                continue
            linea = _linea_trabajador(liq, mes, anio)
            if linea:
                lineas.append(linea)

        if not lineas:
            raise ValueError(f'No hay trabajadores con días trabajados en {mes:02d}/{anio}')

        return '\r\n'.join(lineas) + '\r\n'

    def nombre_archivo(self, mes: int, anio: int) -> str:
        """Nombre sugerido: rutempleador_aaaamm.txt"""
        cuerpo, _ = _split_rut(self.empresa_rut)
        return f'{cuerpo}_{anio}{mes:02d}.txt'
