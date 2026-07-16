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
<div class="ep-doc-cabecera">
  <div class="prop-doc-logo-wrap" data-prop="logo" align="left">{{LOGO}}</div>
  <div class="ep-doc-empresa-block">
    <p class="ep-doc-empresa" data-prop="empresa_nombre"><strong>{{EMPRESA}}</strong></p>
    <p class="ep-doc-meta" data-prop="empresa_rut">{{RUT}}</p>
    <p class="ep-doc-meta" data-prop="empresa_direccion">{{DIRECCION}}</p>
  </div>
</div>
<div class="ep-doc-header-line">&nbsp;</div>

<h1 class="ep-doc-titulo" data-prop="titulo_ep">Estado de Pago N°{{NUMERO_EP}}</h1>

<p class="ep-doc-intro" data-prop="intro">{{INTRO}}</p>

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
        <tr class="ep-doc-total-row"><td align="left">Total</td><td class="text-end fw-bold" align="right" data-prop="total">{{TOTAL}}</td></tr>
      </table>
    </td>
  </tr>
</table>
<p class="ep-doc-monto-palabras" data-prop="monto_palabras">{{MONTO_PALABRAS}}</p>
</div>"""


def monto_clp_en_palabras(monto: int | float | None) -> str:
    """Convierte un monto entero CLP a frase chilena: 'Son: … pesos'."""
    try:
        n = int(round(float(monto or 0)))
    except (TypeError, ValueError):
        n = 0
    if n < 0:
        n = abs(n)
    # Apócope final ante "peso(s)": un / veintiún / treinta y un …
    palabras = _entero_a_palabras_es(n, apocope_final=True)
    unidad = 'peso' if n == 1 else 'pesos'
    return f'Son: {palabras} {unidad}'


def _entero_a_palabras_es(n: int, apocope_final: bool = False) -> str:
    if n == 0:
        return 'cero'

    unidades = (
        '', 'uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve',
        'diez', 'once', 'doce', 'trece', 'catorce', 'quince', 'dieciséis', 'diecisiete',
        'dieciocho', 'diecinueve', 'veinte', 'veintiuno', 'veintidós', 'veintitrés',
        'veinticuatro', 'veinticinco', 'veintiséis', 'veintisiete', 'veintiocho', 'veintinueve',
    )
    decenas = (
        '', '', 'veinte', 'treinta', 'cuarenta', 'cincuenta',
        'sesenta', 'setenta', 'ochenta', 'noventa',
    )
    centenas = (
        '', 'ciento', 'doscientos', 'trescientos', 'cuatrocientos', 'quinientos',
        'seiscientos', 'setecientos', 'ochocientos', 'novecientos',
    )

    def _bajo_100(x: int, apocope: bool = False) -> str:
        if x < 30:
            if apocope and x == 1:
                return 'un'
            if apocope and x == 21:
                return 'veintiún'
            return unidades[x]
        d, u = divmod(x, 10)
        if u == 0:
            return decenas[d]
        u_txt = 'ún' if apocope and u == 1 else unidades[u]
        return f'{decenas[d]} y {u_txt}'

    def _bajo_1000(x: int, apocope: bool = False) -> str:
        if x < 100:
            return _bajo_100(x, apocope=apocope)
        if x == 100:
            return 'cien'
        c, r = divmod(x, 100)
        if r == 0:
            return centenas[c]
        return f'{centenas[c]} {_bajo_100(r, apocope=apocope)}'

    partes: list[str] = []
    millones, resto = divmod(n, 1_000_000)
    if millones:
        if millones == 1:
            partes.append('un millón')
        else:
            partes.append(f'{_bajo_1000(millones, apocope=True)} millones')
    miles, unidades_n = divmod(resto, 1000)
    if miles:
        if miles == 1:
            partes.append('mil')
        else:
            partes.append(f'{_bajo_1000(miles, apocope=True)} mil')
    if unidades_n or not partes:
        partes.append(_bajo_1000(unidades_n, apocope=apocope_final))
    return ' '.join(partes)


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


# Ancho útil A4 con márgenes 2cm ≈ 482pt; usamos 480 para cabezal/meta/totales/tabla.
EP_PDF_CONTENT_W = 480


def _tabla_ep_segura(rows: list[list[str]], row_bgs: list[str | None] | None = None) -> str:
    """Tabla de hitos con anchos absolutos — evita negative availWidth en xhtml2pdf.

    Anchos en pt que suman EP_PDF_CONTENT_W (ancho completo del contenido).
    cellpadding HTML (~5) replica el padding del modal; class ep-tabla-pdf aporta
    bordes horizontales/fuente sin width:100% ni table-layout:fixed
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

    # Ancho completo del contenido (~480pt). Columna N° EP primero cuando hay 8 cols.
    if ncols == 8:
        widths = [28, 118, 42, 40, 48, 70, 94, 40]
        aligns = ['center', 'left', 'right', 'right', 'center', 'right', 'left', 'center']
    elif ncols == 7:
        widths = [140, 42, 40, 48, 70, 100, 40]
        aligns = ['left', 'right', 'right', 'center', 'right', 'left', 'center']
    else:
        w = max(36, EP_PDF_CONTENT_W // max(ncols, 1))
        widths = [w] * ncols
        # Ajustar último para no superar el ancho útil.
        widths[-1] = max(36, EP_PDF_CONTENT_W - sum(widths[:-1]))
        aligns = ['left'] * ncols

    table_w = min(sum(widths), EP_PDF_CONTENT_W)
    # border=0: xhtml2pdf pinta border="1" en negro; el borde #ccc viene de CSS ep-tabla-pdf.
    # cellpadding=5 ≈ padding 5px 8px de prop-tabla / modal.
    parts = [
        f'<table class="ep-tabla-pdf" border="0" cellpadding="5" cellspacing="0" width="{table_w}">'
    ]
    for i, row in enumerate(rows):
        parts.append('<tr>')
        src_bg = (row_bgs[i] if row_bgs and i < len(row_bgs) else None) or ''
        for j, cell in enumerate(row):
            tag = 'th' if i == 0 else 'td'
            if i == 0:
                bg = ' bgcolor="#f1f3f5"'
            elif src_bg and re.fullmatch(r'#[0-9A-Fa-f]{3,8}', src_bg.strip()):
                bg = f' bgcolor="{src_bg.strip()}"'
            else:
                bg = ' bgcolor="#F2F2F2"' if i % 2 == 0 else ''
            align = aligns[j] if j < len(aligns) else 'left'
            w = widths[j] if j < len(widths) else 40
            safe = html_mod.escape(cell) if cell else '&nbsp;'
            parts.append(
                f'<{tag} width="{w}" align="{align}" valign="top"{bg}>{safe}</{tag}>'
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

    Conserva la estructura visual del modal (logo + empresa debajo, línea teal,
    título, intro, meta, tabla a ancho completo, totales) usando anchos absolutos.
    Evita width:100% + table-layout:fixed + padding CSS en la tabla de hitos
    (negative availWidth).
    """
    import html as html_mod
    import re

    out = str(html or '')
    out = out.replace('\u2014', '-').replace('\u2013', '-').replace('\u00a0', ' ')
    out = out.replace('.-', '')
    w = EP_PDF_CONTENT_W

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

    # 3) Totales: data-prop puede haberse eliminado en el export del front;
    #    parsear etiquetas visibles del bloque (Subtotal / IVA / Total).
    def _prop(nombre: str) -> str:
        m = re.search(
            rf'data-prop=["\']{nombre}["\'][^>]*>(.*?)</(?:span|td|th|div|p|strong)>',
            out,
            flags=re.I | re.S,
        )
        return _texto_celda_html(m.group(1)) if m else ''

    def _valor_fila_totales(bloque: str, etiqueta: str) -> str:
        m = re.search(
            rf'<tr[^>]*>\s*<t[dh][^>]*>\s*{etiqueta}\s*</t[dh]>\s*<t[dh][^>]*>(.*?)</t[dh]>',
            bloque,
            flags=re.I | re.S,
        )
        return _texto_celda_html(m.group(1)) if m else ''

    def _valor_fila_total_row(bloque: str) -> str:
        m = re.search(
            r'<tr[^>]*class="[^"]*\bep-doc-total-row\b[^"]*"[^>]*>\s*'
            r'<t[dh][^>]*>.*?</t[dh]>\s*<t[dh][^>]*>(.*?)</t[dh]>',
            bloque,
            flags=re.I | re.S,
        )
        return _texto_celda_html(m.group(1)) if m else ''

    def _parse_pesos_cl(txt: str) -> int | None:
        if not txt or txt.strip() in ('-', '—', ''):
            return None
        digits = re.sub(r'[^\d]', '', txt)
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    bloque_tot = ''
    m_tot_scan = re.search(
        r'<table[^>]*class="[^"]*\bep-doc-totales\b[^"]*"[^>]*>',
        out,
        flags=re.I,
    )
    if m_tot_scan:
        start_scan = m_tot_scan.start()
        i_scan = m_tot_scan.end()
        depth_scan = 1
        lower_scan = out.lower()
        while i_scan < len(out) and depth_scan:
            next_open = lower_scan.find('<table', i_scan)
            next_close = lower_scan.find('</table>', i_scan)
            if next_close < 0:
                break
            if next_open >= 0 and next_open < next_close:
                depth_scan += 1
                i_scan = next_open + 6
            else:
                depth_scan -= 1
                i_scan = next_close + 8
        if depth_scan == 0:
            bloque_tot = out[start_scan:i_scan]

    subtotal = _prop('subtotal') or _valor_fila_totales(bloque_tot, r'Subtotal') or '-'
    iva = _prop('iva') or _valor_fila_totales(bloque_tot, r'IVA(?:\s*19\s*%?)?') or '-'
    total = (
        _prop('total')
        or _valor_fila_totales(bloque_tot, r'Total')
        or _valor_fila_total_row(bloque_tot)
        or '-'
    )

    notas = _prop('notas')
    if not notas and bloque_tot:
        m_notas = re.search(
            r'class="[^"]*\bep-doc-notas\b[^"]*"[^>]*>(.*?)</td>',
            bloque_tot,
            flags=re.I | re.S,
        )
        if m_notas:
            raw_notas = re.sub(
                r'<strong>\s*Notas:\s*</strong>',
                '',
                m_notas.group(1),
                flags=re.I,
            )
            notas = _texto_celda_html(raw_notas)

    monto_palabras = _prop('monto_palabras')
    if not monto_palabras:
        m_mp = re.search(
            r'class="[^"]*\bep-doc-monto-palabras\b[^"]*"[^>]*>(.*?)</p>',
            out,
            flags=re.I | re.S,
        )
        if m_mp:
            monto_palabras = _texto_celda_html(m_mp.group(1))
    # Preferir recálculo desde el total visible (el export puede traer placeholder o marcador).
    base_num = _parse_pesos_cl(total)
    if base_num is None:
        base_num = _parse_pesos_cl(subtotal)
    if base_num is not None:
        monto_palabras = monto_clp_en_palabras(base_num)
    elif not monto_palabras or monto_palabras.startswith('{{'):
        monto_palabras = monto_clp_en_palabras(0)

    notas_w = int(w * 0.55)
    tot_w = w - notas_w

    totales_html = (
        f'<table class="ep-doc-totales" border="0" cellpadding="0" cellspacing="0" width="{w}">'
        '<tr>'
        f'<td class="ep-doc-notas" width="{notas_w}" valign="top">'
        f'<strong>Notas:</strong> {html_mod.escape(notas)}</td>'
        f'<td class="ep-doc-totales-col" width="{tot_w}" valign="top" align="right">'
        '<table class="ep-doc-totales-tabla" border="0" cellpadding="2" cellspacing="0" '
        'width="200" align="right">'
        f'<tr><td align="left">Subtotal</td>'
        f'<td align="right">{html_mod.escape(subtotal)}</td></tr>'
        f'<tr><td align="left">IVA 19%</td>'
        f'<td align="right">{html_mod.escape(iva)}</td></tr>'
        '<tr class="ep-doc-total-row"><td align="left">Total</td>'
        f'<td align="right"><strong>{html_mod.escape(total)}</strong></td></tr>'
        '</table>'
        '</td></tr></table>'
        f'<p class="ep-doc-monto-palabras">{html_mod.escape(monto_palabras)}</p>'
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
            # Quitar párrafo monto_palabras previo si venía justo después del bloque.
            after = out[i:]
            m_after_mp = re.match(
                r'\s*<p[^>]*class="[^"]*\bep-doc-monto-palabras\b[^"]*"[^>]*>[\s\S]*?</p>',
                after,
                flags=re.I,
            )
            if m_after_mp:
                i += m_after_mp.end()
            out = out[:start] + totales_html + out[i:]

    # 4) Cabecera apilada: logo → empresa debajo → línea teal (sin prop-doc-header
    #    side-by-side; su border-bottom + la línea daban doble raya en xhtml2pdf).
    def _cabecera_apilada(logo_html: str, empresa_html: str) -> str:
        return (
            f'<table class="ep-pdf-header" border="0" cellpadding="0" '
            f'cellspacing="0" width="{w}">'
            '<tr>'
            f'<td class="prop-doc-logo-wrap" width="{w}" valign="top" align="left">'
            f'{logo_html}</td>'
            '</tr>'
            '<tr>'
            f'<td class="ep-doc-empresa-block" width="{w}" valign="top" align="left" '
            f'style="padding-top:14px;">'
            f'{empresa_html}</td>'
            '</tr></table>'
            '<div class="ep-doc-header-line">&nbsp;</div>'
        )

    # Nuevo: .ep-doc-cabecera con logo + bloque empresa (+ línea teal opcional)
    def _rew_cabecera_div(match: re.Match) -> str:
        logo_html = (match.group(1) or '').strip() or '&nbsp;'
        empresa_html = (match.group(2) or '').strip() or '&nbsp;'
        return _cabecera_apilada(logo_html, empresa_html)

    out = re.sub(
        r'<div[^>]*class="[^"]*\bep-doc-cabecera\b[^"]*"[^>]*>\s*'
        r'<div[^>]*(?:class="[^"]*\bprop-doc-logo-wrap\b[^"]*"|data-prop=["\']logo["\'])[^>]*>'
        r'([\s\S]*?)</div>\s*'
        r'<div[^>]*class="[^"]*\bep-doc-empresa-block\b[^"]*"[^>]*>'
        r'([\s\S]*?)</div>\s*'
        r'</div>'
        r'(?:\s*<div[^>]*class="[^"]*\bep-doc-header-line\b[^"]*"[^>]*>[\s\S]*?</div>)?',
        _rew_cabecera_div,
        out,
        count=1,
        flags=re.I,
    )

    # Legacy: tabla prop-doc-header logo | texto (plantillas antiguas)
    def _rew_header_legacy(match: re.Match) -> str:
        return _cabecera_apilada(match.group(1), match.group(2))

    out = re.sub(
        r'<table[^>]*class="[^"]*\bprop-doc-header\b[^"]*"[^>]*>\s*<tr>\s*'
        r'<td[^>]*>([\s\S]*?)</td>\s*'
        r'<td[^>]*>([\s\S]*?)</td>\s*'
        r'</tr>\s*</table>'
        r'(?:\s*<div[^>]*class="[^"]*\bep-doc-header-line\b[^"]*"[^>]*>[\s\S]*?</div>)?',
        _rew_header_legacy,
        out,
        count=1,
        flags=re.I,
    )

    half = w // 2

    def _rew_meta(match: re.Match) -> str:
        return (
            f'<table class="ep-doc-meta-grid" border="0" cellpadding="2" cellspacing="0" '
            f'width="{w}">'
            '<tr>'
            f'<td class="ep-meta-left" width="{half}" valign="top">{match.group(1)}</td>'
            f'<td class="ep-meta-right" width="{w - half}" valign="top">{match.group(2)}</td>'
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
