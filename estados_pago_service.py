"""Plantillas y helpers del documento Estado de Pago (cobro preliminar)."""
from __future__ import annotations

from datetime import datetime

from extensions import db
from models import Movimiento, PlantillaEstadoPago
from propuestas_service import generar_docx_propuesta, generar_pdf_propuesta

IVA_EP = 0.19

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


def generar_pdf_estado_pago(titulo: str, contenido: str, logo_path: str | None = None) -> bytes:
    return generar_pdf_propuesta(titulo, contenido, logo_path=logo_path)


def generar_docx_estado_pago(titulo: str, contenido: str, logo_path: str | None = None) -> tuple[bytes, str]:
    return generar_docx_propuesta(titulo, contenido, logo_path=logo_path)
