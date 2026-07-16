"""Smoke test: generar PDF de Estado de Pago con tabla multi-fila."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from estados_pago_service import (  # noqa: E402
    INTRO_EP_DEFAULT,
    TEMPLATE_ESTADO_PAGO,
    _preparar_html_ep_para_pdf,
    generar_pdf_estado_pago,
)

SAMPLE_ROWS = [
    ['Descripción', 'valor UF', 'UF', 'Fecha', 'Precio total', 'Estado', 'N° Fact.'],
    ['Anticipo inicio de servicio', '38.500', '5,00', '15/01/2026', '$192.500', 'Pagado', 'F-1001'],
    ['Informe cumplimiento RT [DOM]', '38.500', '10,00', '20/02/2026', '$385.000', 'Facturado', 'F-1002'],
    ['Pre Calificación energética', '38.500', '8,50', '10/03/2026', '$327.250', 'Enviado', ''],
    ['Calificación energética', '38.500', '8,50', '15/04/2026', '$327.250', 'Por enviar', ''],
    ['Seguimiento en obra / visita', '38.500', '3,00', '01/05/2026', '$115.500', 'Programado', ''],
]


def _tabla_html(rows: list[list[str]]) -> str:
    widths = [32, 10, 10, 12, 14, 12, 10]
    aligns = ['left', 'right', 'right', 'center', 'right', 'left', 'center']
    head = ''.join(
        f'<th width="{widths[i]}%" align="{aligns[i]}" bgcolor="#d9d9d9" '
        f'style="border:1px solid #888888;padding:2px 3px;font-size:8pt;white-space:nowrap;">'
        f'{h}</th>'
        for i, h in enumerate(rows[0])
    )
    body = ''
    for ri, row in enumerate(rows[1:], start=1):
        bg = ' bgcolor="#f2f2f2"' if ri % 2 == 0 else ''
        cells = ''.join(
            f'<td width="{widths[i]}%" align="{aligns[i]}"{bg} '
            f'style="border:1px solid #888888;padding:2px 3px;font-size:8pt;white-space:nowrap;">'
            f'{c}</td>'
            for i, c in enumerate(row)
        )
        body += f'<tr>{cells}</tr>'
    return (
        '<table class="ep-tabla" border="1" cellpadding="2" cellspacing="0" width="100%" '
        'style="width:100%;border-collapse:collapse;font-size:8pt;table-layout:fixed;">'
        f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    )


def main() -> None:
    html = TEMPLATE_ESTADO_PAGO
    repl = {
        '{{LOGO}}': '',
        '{{EMPRESA}}': 'B-green Chile Limitada',
        '{{RUT}}': '76.123.456-7',
        '{{DIRECCION}}': 'Av. Providencia 1234, Santiago',
        '{{INTRO}}': INTRO_EP_DEFAULT,
        '{{NUMERO_EP}}': '3',
        '{{FECHA}}': '15 de julio de 2026',
        '{{ATENCION}}': 'Constructora Ejemplo SpA',
        '{{SERVICIO}}': 'CEV+RT',
        '{{PROYECTO}}': 'Edificio Los Aromos',
        '{{TOTAL_SERVICIO_UF}}': '35,00',
        '{{TABLA_EP}}': _tabla_html(SAMPLE_ROWS),
        '{{NOTAS}}': 'Valores referenciales según UF del día del envío.',
        '{{SUBTOTAL}}': '$1.347.500',
        '{{IVA}}': '$256.025',
        '{{TOTAL}}': '$1.603.525',
    }
    for k, v in repl.items():
        html = html.replace(k, v)

    prepared = _preparar_html_ep_para_pdf(html)
    assert 'width="480"' in prepared, 'tabla principal debe usar ancho absoluto'
    assert 'ep-tabla' not in prepared or 'width="155"' in prepared or 'width="480"' in prepared
    assert INTRO_EP_DEFAULT[:20] in prepared
    assert 'negative' not in prepared.lower()

    out = ROOT / '_test_ep_out.pdf'
    pdf = generar_pdf_estado_pago('Estado de Pago N3', html)
    out.write_bytes(pdf)
    print(f'OK pdf={len(pdf)} bytes -> {out}')
    print(f'prepared_len={len(prepared)} has_abs_table={"width=" + chr(34) + "480" in prepared}')


if __name__ == '__main__':
    main()
