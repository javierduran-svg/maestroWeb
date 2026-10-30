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

# Paleta alineada con app.html (--teal-green, bordes, headers)
TEAL = (0, 128, 128)
TEAL_LIGHT = (227, 242, 242)
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
    texto = f'{float(valor):.4f}'.rstrip('0').rstrip('.')
    if '.' not in texto:
        texto = f'{texto},00'
    else:
        entero, dec = texto.split('.')
        if len(dec) < 2:
            dec = dec.ljust(2, '0')
        texto = f'{entero},{dec}'
    return f'UF {texto}'


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
        self.set_margins(12, 10, 12)
        self.set_auto_page_break(auto=False)
        self._font_family, _ = _registrar_fuentes(self)

    def _set_font(self, style: str = '', size: int = 9):
        self.set_font(self._font_family, style, size)

    def header(self):
        pass


_MARGEN_X = 12
_ANCHO_CONTENIDO = 186
_LOGO_MAX_W = 42
_LOGO_MAX_H = 12
_FILA_H = 4.3
INK = (33, 37, 41)


def _tamano_logo(path: str, max_w: float, max_h: float) -> tuple[float, float]:
    """Escala el logo para que quepa en max_w x max_h sin deformar."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w_px, h_px = im.size
        if w_px <= 0 or h_px <= 0:
            return max_w, max_h
        ratio = w_px / h_px
        w, h = max_h * ratio, max_h
        if w > max_w:
            w, h = max_w, max_w / ratio
        return w, h
    except Exception:
        return max_w, max_h


def _cortar(pdf: LiquidacionPDF, texto, ancho: float, size: float = 7.5) -> str:
    s = _texto_seguro(texto)
    pdf._set_font('', size)
    if pdf.get_string_width(s) <= ancho:
        return s
    while s and pdf.get_string_width(s + '.') > ancho:
        s = s[:-1]
    return s.rstrip() + '.' if s else ''


def _encabezado(pdf: LiquidacionPDF, titulo: str, periodo: str, empresa: dict):
    """Titulo y empresa sobre fondo blanco, con el logo a la derecha."""
    y = pdf.get_y()
    logo_path = empresa.get('logo_path')
    reservado = 0.0
    if logo_path and Path(logo_path).is_file():
        try:
            logo_w, logo_h = _tamano_logo(logo_path, _LOGO_MAX_W, _LOGO_MAX_H)
            pdf.image(
                logo_path,
                x=_MARGEN_X + _ANCHO_CONTENIDO - logo_w,
                y=y,
                w=logo_w,
                h=logo_h,
            )
            reservado = logo_w + 4
        except Exception:
            reservado = 0.0
    texto_w = _ANCHO_CONTENIDO - reservado
    pdf.set_xy(_MARGEN_X, y)
    pdf._set_font('B', 13)
    pdf.set_text_color(*TEAL)
    pdf.cell(texto_w, 6, _texto_seguro(titulo))
    pdf.set_xy(_MARGEN_X, y + 6.2)
    pdf._set_font('', 8)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(texto_w, 4, _texto_seguro(periodo))
    pdf.set_xy(_MARGEN_X, y + 11)
    pdf._set_font('B', 8)
    pdf.set_text_color(*INK)
    empresa_txt = f"{empresa.get('razon_social', '')}    {_fmt_rut(empresa.get('rut', ''))}"
    pdf.cell(texto_w, 4, _cortar(pdf, empresa_txt, texto_w, 8))
    direccion = (empresa.get('direccion') or '').strip()
    alto = 16.5
    if direccion:
        pdf.set_xy(_MARGEN_X, y + 15.2)
        pdf._set_font('', 7.5)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(texto_w, 3.5, _cortar(pdf, direccion, texto_w, 7.5))
        alto = 20
    y_linea = y + alto
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.45)
    pdf.line(_MARGEN_X, y_linea, _MARGEN_X + _ANCHO_CONTENIDO, y_linea)
    pdf.set_y(y_linea + 3)


def _grilla_trabajador(pdf: LiquidacionPDF, pares: list[tuple[str, str, str, str]]):
    """Dos pares etiqueta/valor por fila, sin encabezado de bloque."""
    if not pares:
        return
    x = _MARGEN_X
    y = pdf.get_y()
    h = 4.5
    anchos = (26, 67, 28, 65)
    for i, fila in enumerate(pares):
        yy = y + i * h
        if i % 2 == 0:
            pdf.set_fill_color(248, 249, 250)
        else:
            pdf.set_fill_color(*WHITE)
        pdf.set_draw_color(*BORDER)
        pdf.set_line_width(0.12)
        pdf.rect(x, yy, _ANCHO_CONTENIDO, h, style='FD')
        cx = x
        for j, (texto, ancho) in enumerate(zip(fila, anchos)):
            tam = 6.5 if j % 2 == 0 else 7.5
            valor = _cortar(pdf, texto, ancho - 2.6, tam)
            pdf.set_xy(cx + 1.4, yy + 0.7)
            if j % 2 == 0:
                pdf._set_font('', tam)
                pdf.set_text_color(*TEXT_MUTED)
            else:
                pdf._set_font('B' if j == 1 and i == 0 else '', tam)
                pdf.set_text_color(*INK)
            pdf.cell(ancho - 2.2, 3.2, valor)
            cx += ancho
    pdf.set_text_color(*INK)
    pdf.set_y(y + len(pares) * h + 3.5)


def _tabla_conceptos(
    pdf: LiquidacionPDF,
    x: float,
    y: float,
    w: float,
    titulo: str,
    filas: list[tuple[str, float]],
    total_label: str,
    total_monto: float,
) -> float:
    """Tabla compacta. Devuelve la coordenada y bajo la tabla."""
    pdf.set_xy(x, y)
    pdf._set_font('B', 8)
    pdf.set_text_color(*TEAL)
    pdf.cell(w, 4, _texto_seguro(titulo))
    y += 4.6
    monto_w = 32.0
    conc_w = w - monto_w
    pdf.set_fill_color(*HEADER_BG)
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.12)
    pdf._set_font('B', 6.5)
    pdf.set_text_color(90, 98, 104)
    pdf.rect(x, y, conc_w, _FILA_H, style='FD')
    pdf.rect(x + conc_w, y, monto_w, _FILA_H, style='FD')
    pdf.set_xy(x + 1.3, y + 0.6)
    pdf.cell(conc_w - 2, 3.1, 'Concepto')
    pdf.set_xy(x + conc_w, y + 0.6)
    pdf.cell(monto_w - 1.4, 3.1, 'Monto', align='R')
    y += _FILA_H

    if not filas:
        filas = [('—', 0.0)]
    pdf.set_text_color(*INK)
    for concepto, monto in filas:
        pdf.set_draw_color(*BORDER)
        pdf.rect(x, y, conc_w, _FILA_H, style='D')
        pdf.rect(x + conc_w, y, monto_w, _FILA_H, style='D')
        concepto_txt = _cortar(pdf, concepto, conc_w - 2.4, 7)
        pdf._set_font('', 7)
        pdf.set_xy(x + 1.3, y + 0.6)
        pdf.cell(conc_w - 2, 3.1, concepto_txt)
        pdf.set_xy(x + conc_w, y + 0.6)
        pdf.cell(monto_w - 1.4, 3.1, _fmt_clp(monto), align='R')
        y += _FILA_H

    total_txt = _cortar(pdf, total_label, conc_w - 2.4, 7)
    pdf.set_fill_color(*TEAL_LIGHT)
    pdf.set_draw_color(*BORDER)
    pdf.rect(x, y, conc_w, _FILA_H, style='FD')
    pdf.rect(x + conc_w, y, monto_w, _FILA_H, style='FD')
    pdf._set_font('B', 7)
    pdf.set_text_color(*TEAL)
    pdf.set_xy(x + 1.3, y + 0.6)
    pdf.cell(conc_w - 2, 3.1, total_txt, align='R')
    pdf.set_xy(x + conc_w, y + 0.6)
    pdf.cell(monto_w - 1.4, 3.1, _fmt_clp(total_monto), align='R')
    pdf.set_text_color(*INK)
    return y + _FILA_H


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

    cesantia = float(det.get('descuento_cesantia') or 0)
    if cesantia:
        pct = det.get('cesantia_pct', 0.6)
        items.append((f'SEGURO CESANTIA {float(pct):.1f} %'.replace('.', ','), cesantia))

    sis = float(det.get('descuento_sis') or 0)
    if sis:
        items.append(('SIS 1,78 %', sis))

    impuesto = float(det.get('impuesto_unico') or 0)
    if impuesto:
        items.append(('IMPUESTO UNICO', impuesto))

    for extra in det.get('descuentos_extra') or []:
        concepto = extra.get('concepto', '')
        monto = float(extra.get('monto') or 0)
        if concepto and monto:
            items.append((concepto.upper(), monto))

    return items


def _aportes_empleador_desde_detalle(det: dict) -> list[tuple[str, float]]:
    ap = det.get('aportes_empleador') or {}
    items: list[tuple[str, float]] = []
    sis_label = 'SIS SEGURO SOCIAL PREVISIONAL' if ap.get('sis_en_seguro_social') else 'SIS'
    filas = (
        ('COTIZACION ADICIONAL AFP', 'cotizacion_adicional_afp_pct', 'cotizacion_adicional_afp'),
        ('RENTABILIDAD PROTEGIDA', 'rentabilidad_protegida_pct', 'rentabilidad_protegida'),
        ('EXPECTATIVA DE VIDA', 'expectativa_vida_pct', 'expectativa_vida'),
        (sis_label, 'sis_pct', 'sis'),
        ('SEGURO CESANTIA EMPLEADOR', 'cesantia_pct', 'cesantia'),
    )
    for nombre, pct_k, mon_k in filas:
        monto = float(ap.get(mon_k) or 0)
        if not monto:
            continue
        pct = float(ap.get(pct_k) or 0)
        items.append((f'{nombre} {pct:.2f} %'.replace('.', ','), monto))
    return items


def _render_una_liquidacion(pdf: LiquidacionPDF, datos: dict):
    t = datos.get('trabajador') or {}
    det = _parse_detalle(datos)
    empresa = datos.get('empresa') or _empresa_desde_env()

    mes = datos.get('mes', 0)
    anio = datos.get('anio', 0)
    periodo = (
        f'{MESES_ES[mes]} de {anio}'
        if 1 <= mes <= 12
        else f'{mes}/{anio}'
    )
    _encabezado(pdf, 'Liquidacion de sueldo', periodo, empresa)

    sueldo_uf = det.get('sueldo_base_uf') or datos.get('sueldo_base_uf') or t.get('sueldo_base_uf') or 0
    sueldo_clp = det.get('sueldo_base_clp') or t.get('sueldo_base') or 0
    plan_uf = det.get('valor_plan_uf') or t.get('valor_plan_isapre_uf') or 0
    fecha_ing = t.get('fecha_ingreso', '')
    if fecha_ing and len(str(fecha_ing)) >= 10 and str(fecha_ing)[4] == '-':
        yyyy, mm, dd = str(fecha_ing)[:10].split('-')
        fecha_ing = f'{dd}/{mm}/{yyyy}'
    cargo = (det.get('cargo') or t.get('cargo') or '').strip()
    unidad = det.get('unidad_negocio') or empresa.get('unidad_negocio') or ''
    dias = det.get('dias_trabajados') or datos.get('dias_trabajados') or 0
    salud = t.get('sistema_salud') or ''
    if (salud or '').lower() == 'isapre' and t.get('nombre_isapre'):
        salud = t.get('nombre_isapre')

    izquierda = [
        ('Nombre', t.get('nombre_completo', '')),
        ('RUT', _fmt_rut(t.get('rut', ''))),
        ('Contrato', t.get('tipo_contrato', '')),
        ('Ingreso', fecha_ing),
    ]
    if cargo:
        izquierda.append(('Cargo', cargo))
    if unidad:
        izquierda.append(('Unidad', unidad))
    if (t.get('sistema_salud') or '').lower() == 'isapre' and plan_uf:
        plan_txt = f'{float(plan_uf):.3f}'.rstrip('0').rstrip('.').replace('.', ',')
        salud = f'{salud}  {plan_txt} UF'.strip()
    derecha = [
        ('Sueldo base', _fmt_uf(float(sueldo_uf))),
        ('En pesos', _fmt_clp(float(sueldo_clp))),
        ('Dias', str(dias)),
        ('AFP', t.get('afp', '')),
        ('Salud', salud),
    ]
    n = max(len(izquierda), len(derecha))
    pares = []
    for i in range(n):
        a, b = izquierda[i] if i < len(izquierda) else ('', '')
        c, d = derecha[i] if i < len(derecha) else ('', '')
        pares.append((a, b, c, d))
    _grilla_trabajador(pdf, pares)

    sueldo_prop = float(
        det.get('sueldo_proporcional_clp')
        or datos.get('sueldo_base_proporcional')
        or 0
    )
    total_haberes = float(det.get('total_haberes') or datos.get('total_haberes') or 0)
    haberes: list[tuple[str, float]] = []
    haberes_imp = det.get('haberes_imponibles')
    if haberes_imp:
        for h in haberes_imp:
            haberes.append((h.get('concepto', ''), float(h.get('monto') or 0)))
    else:
        haberes.append((f'Sueldo base {dias} dias', sueldo_prop))
    for h in det.get('haberes_no_imponibles') or []:
        haberes.append((h.get('concepto', ''), float(h.get('monto') or 0)))

    descuentos = _descuentos_desde_detalle(det, t)
    gap = 4.0
    col_w = (_ANCHO_CONTENIDO - gap) / 2
    y_tablas = pdf.get_y()
    y_hab = _tabla_conceptos(
        pdf, _MARGEN_X, y_tablas, col_w,
        'Haberes', haberes, 'Total haberes', total_haberes,
    )
    y_desc = _tabla_conceptos(
        pdf, _MARGEN_X + col_w + gap, y_tablas, col_w,
        'Descuentos', descuentos, 'Total descuentos',
        float(det.get('total_descuentos') or datos.get('total_descuentos') or 0),
    )
    pdf.set_y(max(y_hab, y_desc) + 3.5)

    aportes_filas = _aportes_empleador_desde_detalle(det)
    total_aportes = float((det.get('aportes_empleador') or {}).get('total') or 0)
    if aportes_filas or total_aportes:
        y_ap = _tabla_conceptos(
            pdf, _MARGEN_X, pdf.get_y(), _ANCHO_CONTENIDO,
            'Aportes del empleador, no descuentan el liquido',
            aportes_filas, 'Total aportes', total_aportes,
        )
        pdf.set_y(y_ap + 3)

    liquido = float(det.get('alcance_liquido') or datos.get('alcance_liquido') or 0)
    tributable = float(det.get('total_tributable') or 0)
    y = pdf.get_y()
    alto_liq = 11 if tributable else 8
    pdf.set_fill_color(*TEAL_LIGHT)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.25)
    pdf.rect(_MARGEN_X, y, _ANCHO_CONTENIDO, alto_liq, style='FD')
    pdf.set_fill_color(*TEAL)
    pdf.rect(_MARGEN_X, y, 2.2, alto_liq, style='F')
    pdf.set_xy(_MARGEN_X + 6, y + 1.6)
    pdf._set_font('B', 9)
    pdf.set_text_color(*TEAL)
    pdf.cell(70, 5, 'Liquido a pago')
    pdf._set_font('B', 12)
    pdf.set_xy(_MARGEN_X + 76, y + 1.3)
    pdf.cell(_ANCHO_CONTENIDO - 82, 5.5, _fmt_clp(liquido), align='R')
    if tributable:
        pdf.set_xy(_MARGEN_X + 6, y + 6.4)
        pdf._set_font('', 7)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(_ANCHO_CONTENIDO - 12, 3.2, _texto_seguro(f'Base tributable   {_fmt_clp(tributable)}'))
    pdf.set_y(y + alto_liq + 2.5)

    banco = (t.get('banco') or datos.get('banco') or '').strip()
    cuenta = (t.get('cuenta_bancaria') or datos.get('cuenta_bancaria') or '').strip()
    letras = _numero_a_letras(int(round(liquido)))
    pdf._set_font('', 7.5)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.cell(0, 3.6, _cortar(pdf, f'Son: {letras} pesos.', _ANCHO_CONTENIDO, 7.5), new_x='LMARGIN', new_y='NEXT')
    if banco or cuenta:
        deposito = 'Deposito: ' + '   '.join(p for p in (banco, f'cuenta {cuenta}' if cuenta else '') if p)
        pdf.cell(0, 3.6, _cortar(pdf, deposito, _ANCHO_CONTENIDO, 7.5), new_x='LMARGIN', new_y='NEXT')

    pdf.ln(3)
    pdf._set_font('', 7)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.multi_cell(
        _ANCHO_CONTENIDO, 3.2,
        _texto_seguro(
            'Recibi conforme el alcance liquido de esta liquidacion, '
            'sin cargo ni cobro posterior por ningun concepto.'
        ),
    )
    etiquetas_h = 12
    y_linea = pdf.h - 14 - etiquetas_h
    if y_linea < pdf.get_y() + 28:
        y_linea = min(pdf.h - 14 - etiquetas_h, pdf.get_y() + 28)

    col_w_firma = 72
    x1 = _MARGEN_X + 6
    x2 = _MARGEN_X + _ANCHO_CONTENIDO - col_w_firma - 6
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.35)
    pdf.line(x1, y_linea, x1 + col_w_firma, y_linea)
    pdf.line(x2, y_linea, x2 + col_w_firma, y_linea)
    pdf._set_font('B', 7)
    pdf.set_text_color(*TEAL)
    pdf.set_xy(x1, y_linea + 1.6)
    pdf.cell(col_w_firma, 3.4, 'Firma del trabajador', align='C')
    pdf.set_xy(x2, y_linea + 1.6)
    pdf.cell(col_w_firma, 3.4, 'Firma del empleador', align='C')
    pdf._set_font('', 6.5)
    pdf.set_text_color(*TEXT_MUTED)
    pdf.set_xy(x1, y_linea + 5.2)
    pdf.cell(col_w_firma, 3, _cortar(pdf, t.get('nombre_completo', ''), col_w_firma - 2, 6.5), align='C')
    pdf.set_xy(x2, y_linea + 5.2)
    pdf.cell(col_w_firma, 3, _cortar(pdf, empresa.get('razon_social', ''), col_w_firma - 2, 6.5), align='C')
    pdf.set_xy(x1, y_linea + 8.2)
    pdf.cell(col_w_firma, 3, _texto_seguro(_fmt_rut(t.get('rut', ''))), align='C')
    pdf.set_text_color(*INK)


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
