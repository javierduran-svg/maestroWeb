"""Plantillas y helpers del documento Estado de Pago (cobro preliminar)."""
from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Movimiento, PlantillaEstadoPago
from propuestas_service import generar_docx_propuesta, generar_pdf_propuesta

IVA_EP = 0.19

TEMPLATE_ESTADO_PAGO = r"""<div class="prop-doc ep-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0">
<tr>
  <td class="prop-doc-header-text" valign="top">
    <p class="ep-doc-empresa" data-prop="empresa_nombre"><strong>{{EMPRESA}}</strong></p>
    <p class="ep-doc-meta" data-prop="empresa_rut">{{RUT}}</p>
    <p class="ep-doc-meta" data-prop="empresa_direccion">{{DIRECCION}}</p>
  </td>
  <td class="prop-doc-logo-wrap" valign="top" align="right" data-prop="logo">{{LOGO}}</td>
</tr>
</table>

<h1 class="prop-doc-titulo" data-prop="titulo_ep">Estado de Pago N°{{NUMERO_EP}}</h1>
<p class="ep-doc-fecha">Fecha <span data-prop="fecha">{{FECHA}}</span></p>

<table class="prop-doc-meta ep-doc-meta-grid">
  <tr>
    <th>A la atención de</th>
    <td data-prop="atencion">{{ATENCION}}</td>
    <th>Proyecto</th>
    <td data-prop="proyecto">{{PROYECTO}}</td>
  </tr>
  <tr>
    <th>Servicio</th>
    <td data-prop="servicio">{{SERVICIO}}</td>
    <th>Total Servicio</th>
    <td data-prop="total_servicio_uf">{{TOTAL_SERVICIO_UF}} UF</td>
  </tr>
</table>

<div id="ep-bloque-tabla">{{TABLA_EP}}</div>

<table class="ep-doc-totales" cellpadding="0" cellspacing="0">
  <tr>
    <td class="ep-doc-notas" valign="top">
      <strong>Notas:</strong>
      <span data-prop="notas">{{NOTAS}}</span>
    </td>
    <td class="ep-doc-totales-col" valign="top" align="right">
      <table class="ep-doc-totales-tabla" cellpadding="0" cellspacing="0">
        <tr><td>Subtotal</td><td class="text-end" data-prop="subtotal">{{SUBTOTAL}}</td></tr>
        <tr><td>IVA 19%</td><td class="text-end" data-prop="iva">{{IVA}}</td></tr>
        <tr class="ep-doc-total-row"><td></td><td class="text-end fw-bold" data-prop="total">{{TOTAL}}</td></tr>
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
    contenido = row.contenido_html if row else plantilla_default()
    return {
        'contenido_html': contenido,
        'personalizada': bool(row),
        'updated_at': row.updated_at.isoformat() if row and row.updated_at else None,
        'empresa_id': empresa_id,
    }


def siguiente_numero_ep(proyecto_id: int, empresa_id: int) -> int:
    max_num = (
        Movimiento.query.filter_by(
            empresa_id=empresa_id,
            proyecto_id=proyecto_id,
            clase='estado_pago',
        )
        .with_entities(Movimiento.numero_ep)
        .order_by(Movimiento.numero_ep.desc())
        .limit(1)
        .scalar()
    )
    return (max_num or 0) + 1


def generar_pdf_estado_pago(titulo: str, contenido: str, logo_path: str | None = None) -> bytes:
    return generar_pdf_propuesta(titulo, contenido, logo_path=logo_path)


def generar_docx_estado_pago(titulo: str, contenido: str, logo_path: str | None = None) -> tuple[bytes, str]:
    return generar_docx_propuesta(titulo, contenido, logo_path=logo_path)
