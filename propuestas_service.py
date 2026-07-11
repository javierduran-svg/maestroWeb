"""Servicios de propuestas comerciales: numeración, plantillas y exportación."""
from __future__ import annotations

import base64
import io
import json
import os
import re
from datetime import date
from pathlib import Path

from extensions import db
from models import PlantillaPropuesta, Propuesta

SERVICIOS_PROPUESTA = [
    'CES',
    'CEV+RT',
    'CVS',
    'CES Evaluadora',
    'Consultoría',
    'Proyectos Concesiones hospitalarias',
    'Medición Huella de Carbono',
    'Eficiencia energética',
    'PGSEE',
    'Simulación',
    'Seguimiento en obra',
]

MESES_ES = (
    '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
)

TARIFAS_CEV_RT = [
    {'label': 'Casas 140 m²', 'm2': 140, 'uf_unidad': 7},
    {'label': 'Casas 200 m²', 'm2': 200, 'uf_unidad': 10},
    {'label': 'Casas 320 m²', 'm2': 320, 'uf_unidad': 16},
]

ETAPAS_CEV_RT = [
    {'codigo': '1', 'nombre': 'INFORME VERIFICACION REGLAMENTACION TERMICA', 'porcentaje': 33.33},
    {'codigo': '1.1', 'nombre': 'Informe cumplimiento RT [DOM]', 'porcentaje': 33.33},
    {'codigo': '2', 'nombre': 'CALIFICACION ENERGETICA DE VIVIENDAS', 'porcentaje': 33.34},
    {'codigo': '2.1', 'nombre': 'Pre Calificación', 'porcentaje': 16.67},
    {'codigo': '2.2', 'nombre': 'Calificación', 'porcentaje': 16.67},
]

# Etapas simplificadas para la calculadora (3 ítems de pago)
ETAPAS_PAGO_CEV_RT = [
    {'codigo': '1.1', 'nombre': 'Informe cumplimiento RT [DOM]', 'porcentaje': 33.33},
    {'codigo': '2.1', 'nombre': 'Pre Calificación', 'porcentaje': 33.33},
    {'codigo': '2.2', 'nombre': 'Calificación', 'porcentaje': 33.34},
]

TEMPLATE_CEV_RT = r"""<div class="prop-doc">
<div class="prop-doc-header">
  <div class="prop-doc-header-text">
    <h1 class="prop-doc-titulo">Calificación energética de viviendas CEV + Verificación Reglamentación térmica.</h1>
    <h2 class="prop-doc-subtitulo">{{PROYECTO}}</h2>
  </div>
  <div class="prop-doc-logo-wrap">{{LOGO}}</div>
</div>
<table class="prop-doc-meta">
  <tr><th>Cliente:</th><td>{{CLIENTE}}</td></tr>
  <tr><th>Presentada por:</th><td>{{PRESENTADO_POR}}</td></tr>
  <tr><th>Fecha:</th><td>{{FECHA}}</td></tr>
  <tr><th>ID Propuesta:</th><td>P{{NUMERO}}</td></tr>
</table>

<h3 class="prop-doc-seccion">Introducción</h3>
<p>La presente Propuesta Técnica se desarrolla para el proyecto <strong>{{PROYECTO}}</strong>, y tiene por objetivo dar cumplimiento a los requerimientos normativos vigentes en materia de Reglamentación Térmica y Calificación Energética de Viviendas (CEV).</p>
<p>El encargo considera la elaboración de los informes técnicos exigidos por la Dirección de Obras Municipales (DOM) para el ingreso y aprobación de modificaciones de proyecto, así como la evaluación energética integral del conjunto habitacional.</p>
<p>Adicionalmente, la propuesta incluye la Precalificación y Calificación Energética CEV de cada una de las viviendas del condominio, correspondiente a <strong>{{UNIDADES_DESCRIPCION}}</strong>, a evaluar con el objetivo de optimizar su desempeño energético y alcanzar la mejor calificación posible dentro del marco normativo vigente.</p>

<h3 class="prop-doc-seccion">Propuesta Técnica</h3>
<p>La presente propuesta técnica se estructura en dos componentes principales, orientados a verificar el cumplimiento normativo y evaluar el desempeño energético del proyecto en etapa de diseño.</p>

<h4>1. Cumplimiento de la Reglamentación Térmica</h4>
<p>Se elaborará un Informe de Cumplimiento de Reglamentación Térmica válido para presentación ante la Dirección de Obras Municipales (DOM), en el cual se verificará el cumplimiento del Artículo 4.1.10 de la OGUC, aplicable a edificaciones de uso residencial.</p>
<p>El informe considerará los siguientes aspectos prescriptivos:</p>
<p><strong>A. Desempeño térmico de la envolvente</strong><br>
Se verificará el cumplimiento de los requisitos de transmitancia térmica máxima (U) o resistencia térmica mínima (Rt) exigidos para los distintos elementos de la envolvente térmica, incluyendo techumbres, muros perimetrales, pisos ventilados sobre exterior, sobrecimientos, puertas opacas y ventanas.</p>
<p>Para ello, se entregará una memoria de cálculo detallada, que incluirá la caracterización completa de los materiales que componen la envolvente térmica (muros, techumbres, pisos, ventanas y puertas), considerando espesores, tipos de aislación térmica, soluciones constructivas, tipos de carpintería y especificaciones de vidrios.</p>
<p>Asimismo, se realizará el cálculo de la transmitancia térmica (U) y de la resistencia térmica (Rt o R100) de todos los elementos de la envolvente, verificando adicionalmente los indicadores térmicos de los cristales según la orientación de las fachadas del proyecto, conforme a la normativa vigente.</p>
<p><strong>B. Ausencia de riesgo de condensación</strong><br>
Se desarrollará una memoria de cálculo de condensación superficial e intersticial, aplicando el método de Glaser, para todos los cerramientos del proyecto, incluyendo muros exteriores, cubiertas y pisos ventilados.</p>
<p>El análisis permitirá verificar la ausencia de riesgo de condensación, asegurando el correcto desempeño higrotérmico de las soluciones constructivas propuestas.</p>
<p><strong>C. Permeabilidad al aire e infiltraciones</strong><br>
Se realizará una revisión de la permeabilidad al aire de puertas y ventanas, considerando clasificación de ventanas según infiltraciones de aire, evaluación de detalles constructivos de sellado y revisión de barreras de vapor y continuidad de la envolvente.</p>
<p>La prueba de hermeticidad (blower door) no se encuentra incluida en la presente propuesta, pero podrá ser considerada como un servicio adicional si el mandante lo requiere.</p>
<p><strong>D. Ventilación mínima según normativa vigente</strong><br>
De acuerdo con la normativa actualizada, las viviendas deberán incorporar sistemas de ventilación activos, pasivos o mixtos, cumpliendo con las tasas mínimas de renovación de aire establecidas en la NCh 3308, así como con los requerimientos de extracción de aire en recintos húmedos.</p>
<p>En este contexto, se propondrá un diseño conceptual de soluciones de ventilación adecuadas al proyecto, orientadas a asegurar el cumplimiento de la reglamentación térmica y a mejorar las condiciones de confort y calidad del aire interior de las viviendas.</p>

<h4>2. Calificación energética de viviendas CEV</h4>
<p>La propuesta incluye la evaluación en la etapa de diseño de los aspectos definidos en la normativa vigente, recientemente actualizada y los parámetros de la calificación energética de viviendas CEV. Producto de esa evaluación se busca obtener la calificación óptima para el proyecto.</p>
<p><strong>2.1. Análisis Preliminar CEV y propuestas de mejora.</strong><br>
Se llevará a cabo la simulación de la vivienda para determinar tempranamente la calificación energética. Se entregará un informe con los resultados preliminares calculados según los planos y especificaciones técnicas de arquitectura. En caso de no cumplir con el objetivo trazado por el mandante, se propondrán mejoras con el fin de alcanzar la calificación deseada.</p>
<p><strong>2.2. Precalificación CEV.</strong><br>
Para la precalificación CEV de la vivienda, es requisito contar con el permiso de edificación aprobado. Se realizará la simulación de cada una de las unidades utilizando la Herramienta de Cálculo de la Calificación Energética de Viviendas (PBDT) del MINVU.</p>
<p><strong>2.3. Calificación energética de viviendas.</strong><br>
Durante la fase de construcción, se realizará una visita obligatoria para verificar que la envolvente especificada se esté implementando conforme al proyecto. Para ello, se solicitará a la constructora la entrega de una copia de las facturas de compra de los elementos de la envolvente (aislantes térmicos, cristales, etc.).</p>
<p><strong>2.4. Calificación CEV.</strong><br>
Una vez finalizada la construcción y obtenida la Recepción Final, se procederá a la Calificación CEV. En esta etapa, será necesario subir las planillas de simulación a la página del MINVU. Para las viviendas existentes, se deberá considerar la envolvente construida.</p>

<h3 class="prop-doc-seccion">Honorarios Profesionales</h3>
<p>Para definir el monto de los honorarios profesionales se asume que se contratan los 2 servicios descritos en la propuesta:</p>
<div id="prop-bloque-honorarios">{{HONORARIOS_TABLA}}</div>

<h4>Forma de pago</h4>
<div id="prop-bloque-pago">{{PAGO_TABLA}}</div>
<p class="prop-doc-total"><strong>TOTAL: UF {{TOTAL_UF}}</strong></p>

<div class="prop-doc-firma">
  <p><strong>{{PRESENTADO_POR}}</strong></p>
  <p>Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |<br>
  LEED AP | Asesor CES | Calificador Energético CEV.<br>
  B-green Chile</p>
</div>
<div class="prop-doc-empresa">
  <p><strong>Información de la Empresa</strong></p>
  <p>Nombre: B-green Chile Ltda.<br>
  Rut.: 77.748.415-k<br>
  Dirección: Obispo Donoso 5 Oficina 62. Providencia.</p>
</div>
</div>"""

TEMPLATES_POR_SERVICIO = {'CEV+RT': TEMPLATE_CEV_RT}

PROP_DOC_CSS = """
body { font-family: Roboto, Calibri, Arial, sans-serif; font-size: 11pt; color: #222; line-height: 1.45; }
.prop-doc-header { display: table; width: 100%; border-bottom: 2px solid #008080; margin-bottom: 12px; padding-bottom: 8px; }
.prop-doc-header-text { display: table-cell; vertical-align: top; width: 75%; }
.prop-doc-logo-wrap { display: table-cell; vertical-align: top; text-align: right; width: 25%; }
.prop-doc-titulo { font-size: 14pt; font-weight: bold; margin: 0 0 4px; color: #111; }
.prop-doc-subtitulo { font-size: 12pt; font-weight: 500; margin: 0; color: #008080; }
.prop-doc-logo { max-height: 56px; max-width: 120px; }
.prop-doc-meta { width: 100%; border-collapse: collapse; margin-bottom: 14px; font-size: 10pt; }
.prop-doc-meta th { text-align: left; padding: 2px 10px 2px 0; font-weight: bold; white-space: nowrap; vertical-align: top; }
.prop-doc-meta td { padding: 2px 0; }
.prop-doc-seccion { font-size: 11pt; font-weight: bold; color: #008080; margin: 14px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #ddd; text-transform: uppercase; }
h4 { font-size: 10.5pt; font-weight: bold; margin: 10px 0 6px; color: #222; }
p { margin: 0 0 8px; text-align: justify; }
.prop-tabla { width: 100%; border-collapse: collapse; margin: 8px 0 12px; font-size: 10pt; }
.prop-tabla th, .prop-tabla td { border: 1px solid #ccc; padding: 5px 8px; }
.prop-tabla th { background: #f1f3f5; font-weight: bold; text-align: left; }
.prop-tabla .text-end { text-align: right; }
.prop-doc-total { margin-top: 10px; font-size: 11pt; }
.prop-doc-firma, .prop-doc-empresa { margin-top: 14px; padding-top: 10px; border-top: 1px solid #e9ecef; font-size: 9.5pt; }
"""


def siguiente_numero_propuesta(empresa_id: int) -> int:
    max_num = (
        Propuesta.query.filter_by(empresa_id=empresa_id)
        .with_entities(Propuesta.numero)
        .order_by(Propuesta.numero.desc())
        .limit(1)
        .scalar()
    )
    return (max_num or 0) + 1


def _plantilla_default(servicio: str) -> str | None:
    return TEMPLATES_POR_SERVICIO.get(servicio)


def obtener_plantilla_servicio(empresa_id: int, servicio: str) -> str | None:
    row = PlantillaPropuesta.query.filter_by(empresa_id=empresa_id, servicio=servicio).first()
    if row:
        return row.contenido_html
    return _plantilla_default(servicio)


def guardar_plantilla_servicio(empresa_id: int, servicio: str, contenido_html: str) -> PlantillaPropuesta:
    row = PlantillaPropuesta.query.filter_by(empresa_id=empresa_id, servicio=servicio).first()
    if row:
        row.contenido_html = contenido_html
    else:
        row = PlantillaPropuesta(empresa_id=empresa_id, servicio=servicio, contenido_html=contenido_html)
        db.session.add(row)
    db.session.commit()
    return row


def _logo_data_uri(logo_path: str | None) -> str:
    if not logo_path or not os.path.isfile(logo_path):
        return ''
    path = Path(logo_path)
    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'
    try:
        data = base64.b64encode(path.read_bytes()).decode('ascii')
        return f'data:{mime};base64,{data}'
    except OSError:
        return ''


def _envolver_html_export(contenido: str, titulo: str, logo_path: str | None = None) -> str:
    logo_uri = _logo_data_uri(logo_path)
    if logo_uri and 'prop-doc-logo' not in contenido and '<img' not in contenido[:500]:
        pass
    html = contenido
    if logo_uri:
        html = re.sub(
            r'(<div class="prop-doc-logo-wrap">)\s*(</div>)',
            rf'\1<img src="{logo_uri}" class="prop-doc-logo" alt="Logo"/>\2',
            html,
            count=1,
        )
        html = html.replace('src="/api/empresas/', f'src="{logo_uri}" data-orig="/api/empresas/')
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{titulo}</title>
<style>{PROP_DOC_CSS}</style></head><body>{html}</body></html>"""


def generar_pdf_propuesta(titulo: str, contenido: str, logo_path: str | None = None) -> bytes:
    doc_html = _envolver_html_export(contenido, titulo, logo_path)
    try:
        from xhtml2pdf import pisa

        buf = io.BytesIO()
        status = pisa.CreatePDF(doc_html, dest=buf, encoding='utf-8')
        if status.err:
            raise RuntimeError('Error al generar PDF')
        return buf.getvalue()
    except Exception:
        from fpdf import FPDF

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 12)
        pdf.multi_cell(0, 7, titulo[:200])
        pdf.ln(4)
        pdf.set_font('Helvetica', '', 9)
        texto = _html_a_texto(contenido)
        for linea in texto.split('\n'):
            linea = linea.strip()
            if linea:
                pdf.multi_cell(0, 5, linea[:500])
            else:
                pdf.ln(2)
        out = pdf.output()
        return out if isinstance(out, (bytes, bytearray)) else out.encode('latin-1', errors='replace')


def generar_docx_propuesta(
    titulo: str, contenido: str, logo_path: str | None = None,
) -> tuple[bytes, str]:
    doc_html = _envolver_html_export(contenido, titulo, logo_path)
    return doc_html.encode('utf-8'), 'doc'


def _html_a_texto(html: str) -> str:
    import html as html_mod

    texto = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
    texto = re.sub(r'</p>', '\n\n', texto, flags=re.I)
    texto = re.sub(r'</h[1-6]>', '\n\n', texto, flags=re.I)
    texto = re.sub(r'</tr>', '\n', texto, flags=re.I)
    texto = re.sub(r'</t[dh]>', '\t', texto, flags=re.I)
    texto = re.sub(r'<[^>]+>', '', texto)
    texto = html_mod.unescape(texto)
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    return texto.strip()


def get_config_calculadora(servicio: str) -> dict | None:
    if servicio == 'CEV+RT':
        return {
            'tarifas': TARIFAS_CEV_RT,
            'etapas': ETAPAS_PAGO_CEV_RT,
            'template': None,
            'format': 'html',
        }
    return None


def plantilla_a_dict(row: PlantillaPropuesta) -> dict:
    return {
        'servicio': row.servicio,
        'contenido_html': row.contenido_html,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }
