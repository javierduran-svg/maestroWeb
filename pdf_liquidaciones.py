"""Generación de PDF de liquidaciones de sueldo (fpdf2), estilo Consultora Sustentable."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fpdf import FPDF

MESES_ES = (
    '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
)

MESES_ES_MAYUS = (
    '', 'ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO',
    'JULIO', 'AGOSTO', 'SEPTIEMBRE', 'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE',
)

# Paleta alineada con app.html (--teal-green, bordes, headers)
TEAL = (0, 128, 128)
TEAL_LIGHT = (227, 242, 242)
DARK = (0, 0, 0)
HEADER_BG = (241, 243, 245)
BORDER = (222, 226, 230)
TEXT_MUTED = (108, 117, 125)
WHITE = (255, 255, 255)

_UNIDADES = (
    '', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE',
    'DIEZ', 'ONCE', 'DOCE', 'TRECE', 'CATORCE', 'QUINCE', 'DIECISEIS', 'DIECISIETE',
    'DIECIOCHO', 'DIECINUEVE',
)
_DECENAS = (
    '', '', 'VEINTE', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA', 'SETENTA',
    'OCHENTA', 'NOVENTA',
)
_CENTENAS = (
    '', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS', 'QUINIENTOS',
    'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS',
)

_FONTS_DIR = Path(__file__).parent / 'fonts'
_ROBOTO_OK = False


def _registrar_fuentes(pdf: FPDF) -> tuple[str, str]:
    """Intenta cargar Roboto; si falla usa Helvetica."""
    global _ROBOTO_OK
    regular = _FONTS_DIR / 'Roboto-Regular.ttf'
    bold = _FONTS_DIR / 'Roboto-Bold.ttf'
    if regular.exists() and bold.exists():
        try:
            pdf.add_font('Roboto', '', str(regular))
            pdf.add_font('Roboto', 'B', str(bold))
            _ROBOTO_OK = True
            return 'Roboto', 'Roboto'
        except Exception:
            pass
    return 'Helvetica', 'Helvetica'


def _fmt_clp(valor: float) -> str:
    return f'$ {int(round(valor)):,}'.replace(',', '.')


def _fmt_uf(valor: float) -> str:
    return f'UF {valor:.4f}'.replace('.', ',')


def _fmt_rut(rut: str) -> str:
    if not rut:
        return ''
    limpio = rut.replace('.', '').replace(' ', '').upper()
    if '-' not in limpio and len(limpio) > 1:
        limpio = f'{limpio[:-1]}-{limpio[-1]}'
    cuerpo, dv = limpio.rsplit('-', 1)
    if cuerpo.isdigit():
        cuerpo = f'{int(cuerpo):,}'.replace(',', '.')
    return f'{cuerpo}-{dv}'


def _texto_seguro(texto) -> str:
    if texto is None:
        return ''
    s = str(texto)
    for a, b in (
        ('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'), ('ú', 'u'),
        ('Á', 'A'), ('É', 'E'), ('Í', 'I'), ('Ó', 'O'), ('Ú', 'U'),
        ('ñ', 'n'), ('Ñ', 'N'), ('ü', 'u'), ('Ü', 'U'),
    ):
        s = s.replace(a, b)
    return s


def _numero_a_letras(n: int) -> str:
    if n < 0:
        return f'MENOS {_numero_a_letras(-n)}'
    if n == 0:
        return 'CERO'
    if n < 20:
        return _UNIDADES[n]
    if n < 100:
        d, u = divmod(n, 10)
        if n < 30:
            return 'VEINTI' + _UNIDADES[u] if u else 'VEINTE'
        base = _DECENAS[d]
        return base if u == 0 else f'{base} Y {_UNIDADES[u]}'
    if n < 1000:
        c, r = divmod(n, 100)
        if n == 100:
            return 'CIEN'
        pref = _CENTENAS[c]
        return pref if r == 0 else f'{pref} {_numero_a_letras(r)}'
    if n < 1_000_000:
        miles, r = divmod(n, 1000)
        txt_mil = 'MIL' if miles == 1 else f'{_numero_a_letras(miles)} MIL'
        return txt_mil if r == 0 else f'{txt_mil} {_numero_a_letras(r)}'
    millones, r = divmod(n, 1_000_000)
    txt_m = 'UN MILLON' if millones == 1 else f'{_numero_a_letras(millones)} MILLONES'
    return txt_m if r == 0 else f'{txt_m} {_numero_a_letras(r)}'


def _empresa_desde_env() -> dict:
    return {
        'razon_social': os.environ.get('EMPRESA_RAZON_SOCIAL', 'B green Chile Limitada'),
        'rut': os.environ.get('SII_RUT_EMISOR', os.environ.get('EMPRESA_RUT', '77.748.415-K')),
        'direccion': os.environ.get(
            'EMPRESA_DIRECCION',
            'Obispo Donoso 5 oficina 62, Providencia',
        ),
        'unidad_negocio': os.environ.get('EMPRESA_UNIDAD_NEGOCIO', 'CASA MATRIZ'),
    }


class LiquidacionPDF(FPDF):
    _font_family = 'Helvetica'

    def __init__(self):
        super().__init__()
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(auto=True, margin=18)
        self._font_family, _ = _registrar_fuentes(self)

    def _set_font(self, style: str = '', size: int = 9):
        self.set_font(self._font_family, style, size)

    def header(self):
        pass


def _dibujar_borde(pdf: FPDF, x: float, y: float, w: float, h: float):
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.2)
    pdf.rect(x, y, w, h)


def _barra_titulo(pdf: LiquidacionPDF, titulo: str, subtitulo: str = '', logo_path: str | None = None):
    """Barra superior oscura con acento teal, estilo navbar de la app."""
    y = pdf.get_y()
    pdf.set_fill_color(*DARK)
    pdf.rect(14, y, 182, 14, style='F')
    pdf.set_fill_color(*TEAL)
    pdf.rect(14, y, 3, 14, style='F')
    if logo_path and Path(logo_path).is_file():
        try:
            pdf.image(logo_path, x=165, y=y + 1, h=12)
        except Exception:
            pass
    pdf.set_xy(20, y + 2)
    pdf._set_font('B', 11)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 5, _texto_seguro(titulo), new_x='LMARGIN', new_y='NEXT')
    if subtitulo:
        pdf.set_x(20)
        pdf._set_font('', 8)
        pdf.set_text_color(200, 200, 200)
        pdf.cell(0, 4, _texto_seguro(subtitulo), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 16)


def _seccion_header(pdf: LiquidacionPDF, titulo: str):
    """Encabezado de sección con fondo teal claro."""
    pdf.ln(2)
    y = pdf.get_y()
    pdf.set_fill_color(*TEAL_LIGHT)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.3)
    pdf.rect(14, y, 182, 6, style='FD')
    pdf.set_xy(16, y + 1)
    pdf._set_font('B', 8)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 4, _texto_seguro(titulo.upper()), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.ln(1)


def _tabla_fila_header(pdf: LiquidacionPDF, columnas: list[tuple[str, float]], x: float = 14):
    """Fila de encabezado de tabla con fondo gris claro."""
    y = pdf.get_y()
    h = 5.5
    pdf.set_fill_color(*HEADER_BG)
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.15)
    pdf._set_font('B', 7.5)
    pdf.set_text_color(60, 60, 60)
    cx = x
    for texto, ancho in columnas:
        pdf.rect(cx, y, ancho, h, style='FD')
        pdf.set_xy(cx + 1.5, y + 1.2)
        pdf.cell(ancho - 2, 3.5, _texto_seguro(texto))
        cx += ancho
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + h)


def _tabla_fila_datos(
    pdf: LiquidacionPDF,
    valores: list[tuple[str, float, str]],
    x: float = 14,
    negrita: bool = False,
    fondo: tuple[int, int, int] | None = None,
):
    """Fila de datos: (texto, ancho, alineacion)."""
    y = pdf.get_y()
    h = 5
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.15)
    if fondo:
        pdf.set_fill_color(*fondo)
    pdf._set_font('B' if negrita else '', 7.5)
    cx = x
    for texto, ancho, align in valores:
        style = 'FD' if fondo else 'D'
        pdf.rect(cx, y, ancho, h, style=style)
        pdf.set_xy(cx + 1.5, y + 1)
        pdf.cell(ancho - 2, 3.5, _texto_seguro(texto), align=align)
        cx += ancho
    pdf.set_y(y + h)


def _bloque_info(pdf: LiquidacionPDF, filas: list[tuple[str, str]], x: float, y: float, w: float):
    """Bloque de etiqueta/valor con borde."""
    h_total = len(filas) * 5 + 4
    _dibujar_borde(pdf, x, y, w, h_total)
    pdf.set_fill_color(*HEADER_BG)
    pdf.rect(x, y, w, 5, style='F')
    pdf.set_xy(x + 2, y + 1)
    pdf._set_font('B', 7)
    pdf.set_text_color(*TEAL)
    pdf.cell(w - 4, 3.5, _texto_seguro('DATOS'))
    pdf.set_text_color(0, 0, 0)
    cy = y + 5
    for etq, val in filas:
        pdf.set_xy(x + 2, cy)
        pdf._set_font('B', 7)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(w * 0.35, 4, _texto_seguro(etq))
        pdf._set_font('', 7.5)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(w * 0.63, 4, _texto_seguro(val))
        cy += 5
    return h_total


def _parse_detalle(datos: dict) -> dict:
    det = datos.get('detalle') or {}
    if isinstance(det, str):
        try:
            det = json.loads(det)
        except Exception:
            det = {}
    return det


def _descuentos_desde_detalle(det: dict, t: dict) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    afp_nombre = (t.get('afp') or 'AFP').upper()
    afp_pct = det.get('afp_pct', 11)
    afp_monto = float(det.get('descuento_afp') or 0)
    if afp_monto:
        items.append((f'{afp_nombre} {afp_pct:.2f} %'.replace('.', ','), afp_monto))

    sistema = (t.get('sistema_salud') or '').lower()
    if sistema == 'isapre':
        isapre = (t.get('nombre_isapre') or 'ISAPRE').upper()
        cotiz = float(det.get('descuento_salud_cotizacion') or 0)
        adicional = float(det.get('descuento_adicional_salud') or 0)
        if cotiz:
            items.append((f'{isapre} 7 %', cotiz))
        if adicional:
            items.append(('ADICIONAL DE SALUD', adicional))
        if not cotiz and not adicional:
            salud = float(det.get('descuento_salud') or 0)
            if salud:
                items.append((f'{isapre} 7 %', salud))
    else:
        fonasa_pct = det.get('fonasa_pct', 7)
        salud = float(det.get('descuento_salud') or det.get('descuento_salud_cotizacion') or 0)
        if salud:
            items.append((f'FONASA {fonasa_pct} %', salud))

    impuesto = float(det.get('impuesto_unico') or 0)
    if impuesto:
        items.append(('IMPUESTO UNICO', impuesto))

    for extra in det.get('descuentos_extra') or []:
        concepto = extra.get('concepto', '')
        monto = float(extra.get('monto') or 0)
        if concepto and monto:
            items.append((concepto.upper(), monto))

    return items


def _render_tabla_montos(
    pdf: LiquidacionPDF,
    filas: list[tuple[str, float]],
    total_label: str | None = None,
    total_monto: float | None = None,
):
    """Tabla de conceptos / montos con encabezado."""
    ancho_concepto = 130.0
    ancho_monto = 52.0
    _tabla_fila_header(pdf, [('Concepto', ancho_concepto), ('Monto', ancho_monto)])
    for concepto, monto in filas:
        _tabla_fila_datos(pdf, [
            (concepto, ancho_concepto, 'L'),
            (_fmt_clp(monto), ancho_monto, 'R'),
        ])
    if total_label is not None and total_monto is not None:
        _tabla_fila_datos(pdf, [
            (total_label, ancho_concepto, 'R'),
            (_fmt_clp(total_monto), ancho_monto, 'R'),
        ], negrita=True, fondo=TEAL_LIGHT)


def _render_una_liquidacion(pdf: LiquidacionPDF, datos: dict):
    t = datos.get('trabajador') or {}
    det = _parse_detalle(datos)
    empresa = datos.get('empresa') or _empresa_desde_env()

    mes = datos.get('mes', 0)
    anio = datos.get('anio', 0)
    periodo = (
        f'{MESES_ES_MAYUS[mes]} DE {anio}'
        if 1 <= mes <= 12
        else f'{mes}/{anio}'
    )

    _barra_titulo(
        pdf,
        'LIQUIDACION DE SUELDOS',
        periodo,
        logo_path=empresa.get('logo_path'),
    )

    # Empleador
    pdf._set_font('B', 7.5)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(22, 4, _texto_seguro('Razon Social'))
    pdf._set_font('', 8)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(78, 4, _texto_seguro(empresa.get('razon_social', '')))
    pdf._set_font('B', 7.5)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(10, 4, _texto_seguro('RUT'))
    pdf._set_font('', 8)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(28, 4, _texto_seguro(_fmt_rut(empresa.get('rut', ''))))
    pdf._set_font('B', 7.5)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(18, 4, _texto_seguro('Direccion'))
    pdf._set_font('', 8)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 4, _texto_seguro(empresa.get('direccion', '')), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(3)

    # Datos trabajador en dos bloques
    sueldo_uf = det.get('sueldo_base_uf') or datos.get('sueldo_base_uf') or t.get('sueldo_base_uf') or 0
    sueldo_clp = det.get('sueldo_base_clp') or t.get('sueldo_base') or 0
    plan_uf = det.get('valor_plan_uf') or t.get('valor_plan_isapre_uf') or 0
    fecha_ing = t.get('fecha_ingreso', '')
    if fecha_ing and len(fecha_ing) == 10 and fecha_ing[4] == '-':
        y, m, d = fecha_ing.split('-')
        fecha_ing = f'{d}/{m}/{y}'
    fecha_term = det.get('fecha_termino') or t.get('fecha_termino') or ''
    cargo = det.get('cargo') or t.get('cargo') or ''
    unidad = det.get('unidad_negocio') or empresa.get('unidad_negocio', 'CASA MATRIZ')

    filas_izq = [
        ('Nombre:', t.get('nombre_completo', '')),
        ('RUT:', _fmt_rut(t.get('rut', ''))),
        ('Contrato:', t.get('tipo_contrato', '')),
        ('Ingreso:', fecha_ing),
        ('Termino:', fecha_term or '—'),
        ('Cargo:', cargo or '—'),
    ]
    filas_der = [
        ('Sueldo base (UF):', _fmt_uf(float(sueldo_uf))),
        ('Sueldo base ($):', _fmt_clp(float(sueldo_clp))),
        ('Unidad:', unidad),
        ('AFP:', t.get('afp', '')),
        ('Salud:', t.get('sistema_salud', '')),
    ]
    if (t.get('sistema_salud') or '').lower() == 'isapre' and plan_uf:
        filas_der.append(('Plan salud:', f'{float(plan_uf):.4f} UF'.replace('.', ',')))

    y_start = pdf.get_y()
    col_w = 89.0
    h_izq = _bloque_info(pdf, filas_izq, 14, y_start, col_w)
    h_der = _bloque_info(pdf, filas_der, 14 + col_w + 4, y_start, col_w)
    pdf.set_y(y_start + max(h_izq, h_der) + 4)

    # Haberes
    dias = det.get('dias_trabajados') or datos.get('dias_trabajados') or 0
    sueldo_prop = float(
        det.get('sueldo_proporcional_clp')
        or datos.get('sueldo_base_proporcional')
        or 0
    )
    total_imponible = float(det.get('total_imponible') or datos.get('total_imponible') or 0)
    total_no_imponible = float(det.get('total_no_imponible') or 0)
    total_haberes = float(det.get('total_haberes') or datos.get('total_haberes') or 0)

    _seccion_header(pdf, 'Haberes')
    pdf._set_font('B', 7.5)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 4, _texto_seguro('Imponibles'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)

    haberes_imp = det.get('haberes_imponibles')
    filas_imp: list[tuple[str, float]] = []
    if haberes_imp:
        for h in haberes_imp:
            filas_imp.append((h.get('concepto', ''), float(h.get('monto') or 0)))
    else:
        filas_imp.append((f'SUELDO BASE {dias} DIAS', sueldo_prop))
    _render_tabla_montos(pdf, filas_imp, 'TOTAL IMPONIBLE', total_imponible)
    pdf.ln(2)

    pdf._set_font('B', 7.5)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 4, _texto_seguro('No imponibles'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    haberes_no = det.get('haberes_no_imponibles') or []
    filas_no: list[tuple[str, float]] = [
        (h.get('concepto', ''), float(h.get('monto') or 0)) for h in haberes_no
    ]
    if filas_no:
        _render_tabla_montos(pdf, filas_no, 'TOTAL NO IMPONIBLE', total_no_imponible)
    else:
        _tabla_fila_header(pdf, [('Concepto', 130), ('Monto', 52)])
        _tabla_fila_datos(pdf, [('—', 130, 'L'), (_fmt_clp(0), 52, 'R')])
        _tabla_fila_datos(pdf, [
            ('TOTAL NO IMPONIBLE', 130, 'R'),
            (_fmt_clp(total_no_imponible), 52, 'R'),
        ], negrita=True, fondo=TEAL_LIGHT)
    pdf.ln(1)
    _tabla_fila_datos(pdf, [
        ('TOTAL HABERES', 130, 'R'),
        (_fmt_clp(total_haberes), 52, 'R'),
    ], negrita=True, fondo=HEADER_BG)
    pdf.ln(3)

    # Descuentos
    _seccion_header(pdf, 'Descuentos')
    descuentos = _descuentos_desde_detalle(det, t)
    _render_tabla_montos(
        pdf,
        descuentos,
        'TOTAL DESCUENTOS',
        float(det.get('total_descuentos') or datos.get('total_descuentos') or 0),
    )

    # Resumen líquido
    pdf.ln(3)
    liquido = float(det.get('alcance_liquido') or datos.get('alcance_liquido') or 0)
    tributable = float(det.get('total_tributable') or 0)
    y = pdf.get_y()
    pdf.set_fill_color(*DARK)
    pdf.rect(14, y, 182, 8, style='F')
    pdf.set_fill_color(*TEAL)
    pdf.rect(14, y, 3, 8, style='F')
    pdf.set_xy(20, y + 2)
    pdf._set_font('B', 9)
    pdf.set_text_color(*WHITE)
    pdf.cell(100, 4, _texto_seguro('LIQUIDO A PAGO'))
    pdf.cell(0, 4, _fmt_clp(liquido), align='R', new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    if tributable:
        pdf.ln(1)
        _tabla_fila_datos(pdf, [
            ('TOTAL TRIBUTABLE', 130, 'R'),
            (_fmt_clp(tributable), 52, 'R'),
        ], negrita=True)

    # Depósito bancario
    banco = t.get('banco') or datos.get('banco') or ''
    cuenta = t.get('cuenta_bancaria') or datos.get('cuenta_bancaria') or ''
    if banco or cuenta:
        pdf.ln(3)
        _seccion_header(pdf, 'Datos deposito')
        filas_dep = []
        if banco:
            filas_dep.append(('Banco:', banco))
        if cuenta:
            filas_dep.append(('Cuenta:', cuenta))
        y_dep = pdf.get_y()
        _bloque_info(pdf, filas_dep, 14, y_dep, 182)
        pdf.set_y(y_dep + len(filas_dep) * 5 + 9)

    # Monto en palabras y conformidad
    pdf.ln(4)
    pdf._set_font('', 8)
    pdf.set_text_color(*TEXT_MUTED)
    letras = _numero_a_letras(int(round(liquido)))
    pdf.multi_cell(0, 4, _texto_seguro(f'SON: {letras} PESOS'))
    pdf.ln(2)
    pdf.multi_cell(
        0, 4,
        _texto_seguro(
            'RECIBI CONFORME EL ALCANCE LIQUIDO DE LA PRESENTE LIQUIDACION, '
            'NO TENIENDO CARGO O COBRO ALGUNO QUE HACER POR NINGUN CONCEPTO'
        ),
    )
    pdf.ln(8)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.4)
    pdf.line(70, pdf.get_y(), 140, pdf.get_y())
    pdf.ln(2)
    pdf._set_font('B', 7.5)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 4, _texto_seguro('FIRMA TRABAJADOR'), align='C')
    pdf.set_text_color(0, 0, 0)


def generar_pdf_liquidacion(datos: dict) -> bytes:
    """Genera PDF de una liquidacion. `datos` incluye trabajador, detalle, empresa y montos."""
    pdf = LiquidacionPDF()
    pdf.add_page()
    _render_una_liquidacion(pdf, datos)
    return bytes(pdf.output())


def generar_pdf_planilla(liquidaciones: list[dict], mes: int, anio: int) -> bytes:
    """Genera PDF con todas las liquidaciones del periodo (una pagina por trabajador)."""
    pdf = LiquidacionPDF()
    for datos in liquidaciones:
        pdf.add_page()
        _render_una_liquidacion(pdf, datos)
    if not liquidaciones:
        pdf.add_page()
        pdf._set_font('', 10)
        periodo = f'{MESES_ES[mes]} {anio}' if 1 <= mes <= 12 else f'{mes}/{anio}'
        pdf.cell(0, 10, _texto_seguro(f'Sin liquidaciones para {periodo}'), align='C')
    return bytes(pdf.output())
