"""Plantillas y helpers del documento Estado de Pago (cobro preliminar)."""
from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Movimiento, PlantillaEstadoPago
from propuestas_service import generar_docx_propuesta, generar_pdf_propuesta

IVA_EP = 0.19

INTRO_EP_DEFAULT = (
    'Por medio de la presente enviamos el Estado de Pago '
    'correspondiente a los servicios indicados a continuación:'
)

TEMPLATE_ESTADO_PAGO = r"""<div class="prop-doc ep-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0" width="100%">
<tr>
  <td class="prop-doc-logo-wrap" valign="top" align="left" data-prop="logo" width="28%">{{LOGO}}</td>
  <td class="prop-doc-header-text" valign="top" align="left" width="72%">
    <p class="ep-doc-empresa" data-prop="empresa_nombre"><strong>{{EMPRESA}}</strong></p>
    <p class="ep-doc-meta" data-prop="empresa_rut">{{RUT}}</p>
    <p class="ep-doc-meta" data-prop="empresa_direccion">{{DIRECCION}}</p>
  </td>
</tr>
</table>

<p class="ep-doc-intro" data-prop="intro">{{INTRO}}</p>

<h1 class="ep-doc-titulo" data-prop="titulo_ep">Estado de Pago N°{{NUMERO_EP}}</h1>

<table class="ep-doc-meta-grid" cellpadding="0" cellspacing="0" width="100%">
  <tr>
    <td class="ep-meta-left" valign="top" width="50%">
      <p class="ep-meta-line"><strong>Fecha</strong> <span data-prop="fecha">{{FECHA}}</span></p>
      <p class="ep-meta-line"><strong>A la atención de</strong><br><span data-prop="atencion">{{ATENCION}}</span></p>
      <p class="ep-meta-line"><strong>Servicio</strong><br><span data-prop="servicio">{{SERVICIO}}</span></p>
    </td>
    <td class="ep-meta-right" valign="top" width="50%">
      <p class="ep-meta-line"><strong>Proyecto</strong><br><span data-prop="proyecto">{{PROYECTO}}</span></p>
      <p class="ep-meta-line"><strong>Total Servicio</strong><br><span data-prop="total_servicio_uf">{{TOTAL_SERVICIO_UF}} UF</span></p>
    </td>
  </tr>
</table>

<div id="ep-bloque-tabla">{{TABLA_EP}}</div>

<table class="ep-doc-totales" cellpadding="0" cellspacing="0" width="100%">
  <tr>
    <td class="ep-doc-notas" valign="top" width="55%">
      <strong>Notas:</strong>
      <span data-prop="notas">{{NOTAS}}</span>
    </td>
    <td class="ep-doc-totales-col" valign="top" align="right" width="45%">
      <table class="ep-doc-totales-tabla" cellpadding="0" cellspacing="0" align="right">
        <tr><td align="left">Subtotal</td><td class="text-end" align="right" data-prop="subtotal">{{SUBTOTAL}}</td></tr>
        <tr><td align="left">IVA 19%</td><td class="text-end" align="right" data-prop="iva">{{IVA}}</td></tr>
        <tr class="ep-doc-total-row"><td></td><td class="text-end fw-bold" align="right" data-prop="total">{{TOTAL}}</td></tr>
      </table>
    </td>
  </tr>
</table>
</div>"""


def plantilla_default() -> str:
    return TEMPLATE_ESTADO_PAGO


def obtener_plantilla_ep(empresa_id: int) -> str:
    row = PlantillaEstadoPago.query.filter_by(empresa_id=empresa_id).first()
    if row and row.contenido_html:
        return row.contenido_html
    return plantilla_default()


def guardar_plantilla_ep(empresa_id: int, contenido_html: str) -> PlantillaEstadoPago:
    row = PlantillaEstadoPago.query.filter_by(empresa_id=empresa_id).first()
    if row:
        row.contenido_html = contenido_html
        row.updated_at = datetime.utcnow()
    else:
        row = PlantillaEstadoPago(
            empresa_id=empresa_id,
            contenido_html=contenido_html,
        )
        db.session.add(row)
    db.session.commit()
    return row


def plantilla_ep_a_dict(row: PlantillaEstadoPago | None, empresa_id: int) -> dict:
    default = plantilla_default()
    contenido = row.contenido_html if row and row.contenido_html else default
    return {
        'contenido_html': contenido,
        'contenido_default': default,
        'personalizada': bool(row),
        'updated_at': row.updated_at.isoformat() if row and row.updated_at else None,
        'empresa_id': empresa_id,
    }


def siguiente_numero_ep(proyecto_id: int, empresa_id: int) -> int:
    """Correlativo del próximo EP del proyecto (1, 2, 3…).

    Considera tanto el máximo ``numero_ep`` ya asignado como la cantidad de
    estados de pago existentes (por si hay filas antiguas sin número).
    """
    rows = (
        Movimiento.query.filter_by(
            empresa_id=empresa_id,
            proyecto_id=proyecto_id,
            clase='estado_pago',
        )
        .with_entities(Movimiento.numero_ep)
        .all()
    )
    nums = [n for (n,) in rows if n is not None]
    max_num = max(nums) if nums else 0
    return max(max_num, len(rows)) + 1


def correlativo_ep_para_movimiento(mov: Movimiento) -> int:
    """Número correlativo del EP dentro del proyecto (por fecha/id)."""
    if mov.numero_ep:
        return int(mov.numero_ep)
    eps = (
        Movimiento.query.filter_by(
            empresa_id=mov.empresa_id,
            proyecto_id=mov.proyecto_id,
            clase='estado_pago',
        )
        .order_by(
            Movimiento.fecha_movimiento.asc(),
            Movimiento.id.asc(),
        )
        .all()
    )
    for i, ep in enumerate(eps, start=1):
        if ep.id == mov.id:
            return i
    return siguiente_numero_ep(mov.proyecto_id, mov.empresa_id)


def _texto_celda_html(raw: str) -> str:
    import html as html_mod
    import re

    txt = re.sub(r'<br\s*/?>', ' ', raw or '', flags=re.I)
    txt = re.sub(r'<[^>]+>', '', txt)
    return html_mod.unescape(txt).strip()


def _tabla_ep_segura(rows: list[list[str]], row_bgs: list[str | None] | None = None) -> str:
    """Tabla de hitos con anchos absolutos — evita negative availWidth en xhtml2pdf.

    Anchos en pt que suman <480. cellpadding HTML (~6) replica el padding del modal;
    class ep-tabla-pdf aporta borde/fuente sin width:100% ni table-layout:fixed
    (esa combinación con padding CSS disparaba el crash).
    """
    import html as html_mod
    import re

    if not rows:
        return '<p></p>'

    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append('')

    # ~430pt < 480pt útiles; Estado/Descripción anchos para leer sin clip.
    if ncols == 7:
        widths = [120, 42, 40, 48, 62, 78, 40]
        aligns = ['left', 'right', 'right', 'center', 'right', 'left', 'center']
    else:
        w = max(40, 430 // max(ncols, 1))
        widths = [w] * ncols
        aligns = ['left'] * ncols

    table_w = sum(widths)
    # cellpadding=6 ≈ padding vertical del modal; CSS de ep-tabla-pdf no añade padding.
    parts = [
        f'<table class="ep-tabla-pdf" border="1" cellpadding="6" cellspacing="0" width="{table_w}">'
    ]
    for i, row in enumerate(rows):
        parts.append('<tr>')
        src_bg = (row_bgs[i] if row_bgs and i < len(row_bgs) else None) or ''
        for j, cell in enumerate(row):
            tag = 'th' if i == 0 else 'td'
            if i == 0:
                bg = ' bgcolor="#D9D9D9"'
            elif src_bg and re.fullmatch(r'#[0-9A-Fa-f]{3,8}', src_bg.strip()):
                bg = f' bgcolor="{src_bg.strip()}"'
            else:
                bg = ' bgcolor="#F2F2F2"' if i % 2 == 0 else ''
            align = aligns[j]
            w = widths[j]
            safe = html_mod.escape(cell) if cell else '&nbsp;'
            parts.append(
                f'<{tag} width="{w}" align="{align}" valign="middle"{bg}>{safe}</{tag}>'
            )
        parts.append('</tr>')
    parts.append('</table>')
    return ''.join(parts)


def _extraer_filas_tabla(table_html: str) -> tuple[list[list[str]], list[str | None]]:
    import re

    rows: list[list[str]] = []
    row_bgs: list[str | None] = []
    for tr in re.finditer(r'<tr[^>]*>(.*?)</tr>', table_html, flags=re.I | re.S):
        cell_tags = list(re.finditer(r'<t[dh]([^>]*)>(.*?)</t[dh]>', tr.group(1), flags=re.I | re.S))
        if not cell_tags:
            continue
        cells = [_texto_celda_html(m.group(2)) for m in cell_tags]
        bg = None
        for m in cell_tags:
            bg_m = re.search(r'bgcolor=["\']([^"\']+)["\']', m.group(1), flags=re.I)
            if bg_m:
                bg = bg_m.group(1).strip()
                break
        rows.append(cells)
        row_bgs.append(bg)
    return rows, row_bgs


def _preparar_html_ep_para_pdf(html: str) -> str:
    """Reescribe el documento EP a HTML WYSIWYG-compatible con xhtml2pdf.

    Conserva la estructura visual del modal (header + línea teal, intro, meta,
    tabla con padding, totales) usando anchos absolutos. Evita width:100% +
    table-layout:fixed + padding CSS en la tabla de hitos (negative availWidth).
    """
    import html as html_mod
    import re

    out = str(html or '')
    out = out.replace('\u2014', '-').replace('\u2013', '-').replace('\u00a0', ' ')
    out = out.replace('.-', '')

    def _rew_ep_tabla(match: re.Match) -> str:
        rows, row_bgs = _extraer_filas_tabla(match.group(0))
        return _tabla_ep_segura(rows, row_bgs)

    # 1) Tablas con class ep-tabla
    out = re.sub(
        r'<table[^>]*class="[^"]*\bep-tabla\b[^"]*"[^>]*>[\s\S]*?</table>',
        _rew_ep_tabla,
        out,
        flags=re.I,
    )

    # 2) Cualquier tabla dentro de #ep-bloque-tabla (por si perdió la class)
    def _rew_bloque(match: re.Match) -> str:
        inner = match.group(1)
        if re.search(r'<table', inner, flags=re.I):
            inner = re.sub(
                r'<table[^>]*>[\s\S]*?</table>',
                _rew_ep_tabla,
                inner,
                count=1,
                flags=re.I,
            )
        return f'<div id="ep-bloque-tabla">{inner}</div>'

    out = re.sub(
        r'<div[^>]*id=["\']ep-bloque-tabla["\'][^>]*>([\s\S]*?)</div>',
        _rew_bloque,
        out,
        count=1,
        flags=re.I,
    )

    # 3) Totales: leer data-prop si existen; conservar clases del modal.
    def _prop(nombre: str) -> str:
        m = re.search(
            rf'data-prop=["\']{nombre}["\'][^>]*>(.*?)</(?:span|td|th|div|strong)>',
            out,
            flags=re.I | re.S,
        )
        return _texto_celda_html(m.group(1)) if m else ''

    subtotal = _prop('subtotal') or '-'
    iva = _prop('iva') or '-'
    total = _prop('total') or '-'
    notas = _prop('notas')

    totales_html = (
        '<table class="ep-doc-totales" border="0" cellpadding="0" cellspacing="0" width="480">'
        '<tr>'
        f'<td class="ep-doc-notas" width="264" valign="top">'
        f'<strong>Notas:</strong> {html_mod.escape(notas)}</td>'
        '<td class="ep-doc-totales-col" width="216" valign="top" align="right">'
        '<table class="ep-doc-totales-tabla" border="0" cellpadding="2" cellspacing="0" '
        'width="200" align="right">'
        f'<tr><td align="left">Subtotal</td>'
        f'<td align="right">{html_mod.escape(subtotal)}</td></tr>'
        f'<tr><td align="left">IVA 19%</td>'
        f'<td align="right">{html_mod.escape(iva)}</td></tr>'
        '<tr class="ep-doc-total-row"><td align="left"></td>'
        f'<td align="right"><strong>{html_mod.escape(total)}</strong></td></tr>'
        '</table>'
        '</td></tr></table>'
    )

    # Reemplazar bloque de totales anidado (desde class ep-doc-totales hasta su cierre balanceado).
    m_tot = re.search(r'<table[^>]*class="[^"]*\bep-doc-totales\b[^"]*"[^>]*>', out, flags=re.I)
    if m_tot:
        start = m_tot.start()
        i = m_tot.end()
        depth = 1
        lower = out.lower()
        while i < len(out) and depth:
            next_open = lower.find('<table', i)
            next_close = lower.find('</table>', i)
            if next_close < 0:
                break
            if next_open >= 0 and next_open < next_close:
                depth += 1
                i = next_open + 6
            else:
                depth -= 1
                i = next_close + 8
        if depth == 0:
            out = out[:start] + totales_html + out[i:]

    # 4) Cabecera: anchos absolutos + línea teal explícita (sin class prop-doc-header:
    #    su border-bottom + la línea daban doble raya en xhtml2pdf).
    def _rew_header(match: re.Match) -> str:
        return (
            '<table class="ep-pdf-header" border="0" cellpadding="0" '
            'cellspacing="0" width="480">'
            '<tr>'
            f'<td class="prop-doc-logo-wrap" width="135" valign="top" align="left">'
            f'{match.group(1)}</td>'
            f'<td class="prop-doc-header-text" width="345" valign="top" align="left">'
            f'{match.group(2)}</td>'
            '</tr></table>'
            '<div class="ep-doc-header-line">&nbsp;</div>'
        )

    out = re.sub(
        r'<table[^>]*class="[^"]*\bprop-doc-header\b[^"]*"[^>]*>\s*<tr>\s*'
        r'<td[^>]*>([\s\S]*?)</td>\s*'
        r'<td[^>]*>([\s\S]*?)</td>\s*'
        r'</tr>\s*</table>',
        _rew_header,
        out,
        count=1,
        flags=re.I,
    )

    def _rew_meta(match: re.Match) -> str:
        return (
            '<table class="ep-doc-meta-grid" border="0" cellpadding="2" cellspacing="0" '
            'width="480">'
            '<tr>'
            f'<td class="ep-meta-left" width="240" valign="top">{match.group(1)}</td>'
            f'<td class="ep-meta-right" width="240" valign="top">{match.group(2)}</td>'
            '</tr></table>'
        )

    out = re.sub(
        r'<table[^>]*class="[^"]*\bep-doc-meta-grid\b[^"]*"[^>]*>\s*<tr>\s*'
        r'<td[^>]*>([\s\S]*?)</td>\s*'
        r'<td[^>]*>([\s\S]*?)</td>\s*'
        r'</tr>\s*</table>',
        _rew_meta,
        out,
        count=1,
        flags=re.I,
    )

    return out


def generar_pdf_estado_pago(titulo: str, contenido: str, logo_path: str | None = None) -> bytes:
    return generar_pdf_propuesta(titulo, _preparar_html_ep_para_pdf(contenido), logo_path=logo_path)


def generar_docx_estado_pago(titulo: str, contenido: str, logo_path: str | None = None) -> tuple[bytes, str]:
    return generar_docx_propuesta(titulo, _preparar_html_ep_para_pdf(contenido), logo_path=logo_path)
