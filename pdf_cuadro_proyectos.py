"""Generación de PDF «Cuadro de Proyectos Activos» para presentación bancaria."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpdf import FPDF

TEAL = (0, 128, 128)
TEAL_LIGHT = (227, 242, 242)
DARK = (0, 0, 0)
HEADER_BG = (241, 243, 245)
BORDER = (222, 226, 230)
TEXT_MUTED = (108, 117, 125)
WHITE = (255, 255, 255)
ROW_ALT = (248, 250, 250)

_FONTS_DIR = Path(__file__).parent / 'fonts'


def _registrar_fuentes(pdf: FPDF) -> str:
    regular = _FONTS_DIR / 'Roboto-Regular.ttf'
    bold = _FONTS_DIR / 'Roboto-Bold.ttf'
    if regular.exists() and bold.exists():
        try:
            pdf.add_font('Roboto', '', str(regular))
            pdf.add_font('Roboto', 'B', str(bold))
            return 'Roboto'
        except Exception:
            pass
    return 'Helvetica'


def _fmt_clp(valor: float | int | None) -> str:
    try:
        n = int(round(float(valor or 0)))
    except (TypeError, ValueError):
        n = 0
    return f'$ {n:,}'.replace(',', '.')


def _fmt_pct(valor: float | None) -> str:
    if valor is None:
        return '—'
    return f'{valor:.0f}%'.replace('.', ',')


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


def _truncar(texto: str, max_len: int) -> str:
    s = _texto_seguro(texto).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + '...'


# Anchos de columna (landscape A4 útil ≈ 269 mm con márgenes 14)
_COLS = [
    ('#', 8),
    ('Proyecto', 48),
    ('Cliente', 42),
    ('Servicio', 32),
    ('Contrato', 28),
    ('Facturado', 28),
    ('Pagado', 28),
    ('Saldo por cobrar', 30),
    ('Avance', 16),
]
_COL_W = [c[1] for c in _COLS]
_TABLE_W = sum(_COL_W)


class CuadroProyectosPDF(FPDF):
    _font_family = 'Helvetica'
    _fecha_emision = ''
    _empresa_nombre = ''

    def __init__(self):
        super().__init__(orientation='L', format='A4')
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(auto=True, margin=16)
        self._font_family = _registrar_fuentes(self)

    def _set_font(self, style: str = '', size: int = 9):
        self.set_font(self._font_family, style, size)

    def header(self):
        if self.page_no() == 1:
            return
        self._set_font('B', 8)
        self.set_text_color(*TEAL)
        self.cell(0, 5, _texto_seguro(f'Cuadro de Proyectos Activos — {self._empresa_nombre}'), new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*TEAL)
        self.set_line_width(0.4)
        self.line(14, self.get_y(), 14 + _TABLE_W, self.get_y())
        self.ln(3)
        self.set_text_color(0, 0, 0)
        _fila_header(self)

    def footer(self):
        self.set_y(-12)
        self._set_font('', 7)
        self.set_text_color(*TEXT_MUTED)
        self.cell(120, 4, _texto_seguro(f'Emitido el {self._fecha_emision}'), align='L')
        self.cell(0, 4, f'Pagina {self.page_no()}/{{nb}}', align='R')
        self.set_text_color(0, 0, 0)


def _fila_header(pdf: CuadroProyectosPDF):
    y = pdf.get_y()
    h = 6.5
    pdf.set_fill_color(*HEADER_BG)
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.15)
    pdf._set_font('B', 7)
    pdf.set_text_color(50, 50, 50)
    cx = 14.0
    for (texto, ancho) in _COLS:
        pdf.rect(cx, y, ancho, h, style='FD')
        pdf.set_xy(cx + 1, y + 1.5)
        align = 'C' if texto in ('#', 'Avance') else ('R' if texto not in ('Proyecto', 'Cliente', 'Servicio') else 'L')
        pdf.cell(ancho - 2, 3.5, _texto_seguro(texto), align=align)
        cx += ancho
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + h)


def _fila_dato(
    pdf: CuadroProyectosPDF,
    valores: list[tuple[str, str]],
    *,
    negrita: bool = False,
    fondo: tuple[int, int, int] | None = None,
):
    """valores: lista de (texto, alineacion) alineada con _COL_W."""
    y = pdf.get_y()
    h = 6.0
    if y + h > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()

    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.12)
    if fondo:
        pdf.set_fill_color(*fondo)
    pdf._set_font('B' if negrita else '', 7)
    cx = 14.0
    for i, (texto, align) in enumerate(valores):
        ancho = _COL_W[i]
        style = 'FD' if fondo else 'D'
        pdf.rect(cx, y, ancho, h, style=style)
        pdf.set_xy(cx + 1, y + 1.3)
        pdf.cell(ancho - 2, 3.5, _texto_seguro(texto), align=align)
        cx += ancho
    pdf.set_y(y + h)


def generar_pdf_cuadro_proyectos(
    empresa: dict,
    proyectos: list[dict],
    *,
    fecha_emision: datetime | None = None,
) -> bytes:
    """Genera el PDF del cuadro de proyectos activos.

    ``empresa``: dict con razon_social/nombre, rut, direccion, giro, logo_path.
    ``proyectos``: lista de dicts con nombre, cliente, servicio, monto_contrato,
    monto_facturado, monto_pagado (y opcionalmente saldo_por_cobrar / avance_cobro).
    """
    ahora = fecha_emision or datetime.now()
    fecha_txt = ahora.strftime('%d/%m/%Y')

    pdf = CuadroProyectosPDF()
    pdf.alias_nb_pages()
    pdf._fecha_emision = fecha_txt
    pdf._empresa_nombre = empresa.get('razon_social') or empresa.get('nombre') or ''
    pdf.add_page()

    # Cabecera empresa: datos a la izquierda, logo a la derecha
    logo_path = empresa.get('logo_path')
    y0 = pdf.get_y()
    if logo_path and Path(logo_path).is_file():
        try:
            logo_h = 14.0
            # Aproximar ancho tipico del logo y anclarlo al borde derecho de la tabla
            logo_w = 42.0
            x_logo = 14 + _TABLE_W - logo_w
            pdf.image(str(logo_path), x=x_logo, y=y0, h=logo_h)
        except Exception:
            pass

    pdf.set_xy(14, y0)
    pdf._set_font('B', 12)
    pdf.set_text_color(*DARK)
    pdf.cell(180, 5, _texto_seguro(pdf._empresa_nombre), new_x='LMARGIN', new_y='NEXT')
    pdf.set_x(14)
    pdf._set_font('', 8)
    pdf.set_text_color(*TEXT_MUTED)
    meta_parts = []
    if empresa.get('rut'):
        meta_parts.append(f"RUT {_fmt_rut(empresa['rut'])}")
    if empresa.get('giro'):
        meta_parts.append(_texto_seguro(empresa['giro']))
    if empresa.get('direccion'):
        meta_parts.append(_texto_seguro(empresa['direccion']))
    pdf.cell(180, 4, '  ·  '.join(meta_parts), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(max(pdf.get_y(), y0 + 16))
    pdf.ln(2)

    # Barra título
    y = pdf.get_y()
    pdf.set_fill_color(*DARK)
    pdf.rect(14, y, _TABLE_W, 12, style='F')
    pdf.set_fill_color(*TEAL)
    pdf.rect(14, y, 3, 12, style='F')
    pdf.set_xy(20, y + 1.5)
    pdf._set_font('B', 11)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 5, _texto_seguro('CUADRO DE PROYECTOS ACTIVOS'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_x(20)
    pdf._set_font('', 8)
    pdf.set_text_color(200, 200, 200)
    pdf.cell(0, 4, _texto_seguro(f'Listado de proyectos vigentes al {fecha_txt} — Montos en pesos chilenos (CLP)'), new_x='LMARGIN', new_y='NEXT')
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 14)
    pdf.ln(2)

    # Intro formal
    pdf._set_font('', 8)
    pdf.set_text_color(60, 60, 60)
    intro = (
        'El presente documento resume los proyectos activos de la empresa, '
        'incluyendo montos contratados, facturados y pagados, para efectos de '
        'presentacion ante instituciones financieras.'
    )
    pdf.multi_cell(_TABLE_W, 4, _texto_seguro(intro))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    _fila_header(pdf)

    tot_contrato = 0.0
    tot_facturado = 0.0
    tot_pagado = 0.0
    tot_saldo = 0.0

    for idx, p in enumerate(proyectos, start=1):
        contrato = float(p.get('monto_contrato') or 0)
        facturado = float(p.get('monto_facturado') or 0)
        pagado = float(p.get('monto_pagado') or 0)
        saldo = float(p.get('saldo_por_cobrar') if p.get('saldo_por_cobrar') is not None else max(0.0, contrato - pagado))
        avance = p.get('avance_cobro')
        if avance is None:
            avance = (pagado / contrato * 100.0) if contrato > 0 else None

        tot_contrato += contrato
        tot_facturado += facturado
        tot_pagado += pagado
        tot_saldo += saldo

        fondo = ROW_ALT if idx % 2 == 0 else None
        _fila_dato(pdf, [
            (str(idx), 'C'),
            (_truncar(p.get('nombre') or '', 38), 'L'),
            (_truncar(p.get('cliente') or '', 32), 'L'),
            (_truncar(p.get('servicio') or '', 24), 'L'),
            (_fmt_clp(contrato), 'R'),
            (_fmt_clp(facturado), 'R'),
            (_fmt_clp(pagado), 'R'),
            (_fmt_clp(saldo), 'R'),
            (_fmt_pct(avance), 'C'),
        ], fondo=fondo)

    if not proyectos:
        y = pdf.get_y()
        pdf.set_draw_color(*BORDER)
        pdf.rect(14, y, _TABLE_W, 10, style='D')
        pdf.set_xy(14, y + 3)
        pdf._set_font('', 8)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(_TABLE_W, 4, _texto_seguro('No hay proyectos activos registrados.'), align='C')
        pdf.set_text_color(0, 0, 0)
        pdf.set_y(y + 10)
    else:
        avance_total = (tot_pagado / tot_contrato * 100.0) if tot_contrato > 0 else None
        _fila_dato(pdf, [
            ('', 'C'),
            ('TOTALES', 'L'),
            ('', 'L'),
            (f'{len(proyectos)} proyecto(s)', 'L'),
            (_fmt_clp(tot_contrato), 'R'),
            (_fmt_clp(tot_facturado), 'R'),
            (_fmt_clp(tot_pagado), 'R'),
            (_fmt_clp(tot_saldo), 'R'),
            (_fmt_pct(avance_total), 'C'),
        ], negrita=True, fondo=TEAL_LIGHT)

    # Resumen y nota
    pdf.ln(6)
    pdf.set_fill_color(*TEAL_LIGHT)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.3)
    y = pdf.get_y()
    pdf.rect(14, y, _TABLE_W, 6, style='FD')
    pdf.set_xy(16, y + 1)
    pdf._set_font('B', 8)
    pdf.set_text_color(*TEAL)
    pdf.cell(0, 4, _texto_seguro('RESUMEN'))
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y + 8)

    resumen = [
        ('Proyectos activos', str(len(proyectos))),
        ('Monto contratado total', _fmt_clp(tot_contrato)),
        ('Monto facturado total', _fmt_clp(tot_facturado)),
        ('Monto pagado total', _fmt_clp(tot_pagado)),
        ('Saldo por cobrar total', _fmt_clp(tot_saldo)),
    ]
    pdf._set_font('', 8)
    for etq, val in resumen:
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(55, 4.5, _texto_seguro(etq))
        pdf.set_text_color(0, 0, 0)
        pdf._set_font('B', 8)
        pdf.cell(0, 4.5, val, new_x='LMARGIN', new_y='NEXT')
        pdf._set_font('', 8)

    pdf.ln(4)
    pdf._set_font('', 7)
    pdf.set_text_color(*TEXT_MUTED)
    nota = (
        'Notas: El monto contratado corresponde a la suma de estados de pago del proyecto. '
        'El monto pagado incluye estados de pago con status Pagado o Cedida. '
        'El saldo por cobrar es la diferencia entre contrato y pagado. '
        'Avance = pagado / contrato.'
    )
    pdf.multi_cell(_TABLE_W, 3.5, _texto_seguro(nota))
    pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())
