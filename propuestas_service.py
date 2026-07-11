"""Servicios de propuestas comerciales: numeración, plantillas y exportación."""
from __future__ import annotations

import io
import os
from datetime import date

from fpdf import FPDF

from models import Propuesta

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

# Tarifas UF por unidad según superficie (CEV+RT)
TARIFAS_CEV_RT = [
    {'label': 'Casas 140 m²', 'm2': 140, 'uf_unidad': 7},
    {'label': 'Casas 200 m²', 'm2': 200, 'uf_unidad': 10},
    {'label': 'Casas 320 m²', 'm2': 320, 'uf_unidad': 16},
]

ETAPAS_CEV_RT = [
    {'codigo': '1.1', 'nombre': 'Informe cumplimiento RT [DOM]'},
    {'codigo': '2.1', 'nombre': 'Pre Calificación'},
    {'codigo': '2.2', 'nombre': 'Calificación'},
]

TEMPLATE_CEV_RT = """Calificación energética de viviendas CEV + Verificación Reglamentación térmica.
{{PROYECTO}}.

Cliente: {{CLIENTE}}
Presentada por: {{PRESENTADO_POR}}
Fecha: {{FECHA}}
ID Propuesta: P{{NUMERO}}

═══════════════════════════════════════════════════════════════
INTRODUCCIÓN
═══════════════════════════════════════════════════════════════

La presente Propuesta Técnica se desarrolla para el proyecto {{PROYECTO}}, y tiene por objetivo dar cumplimiento a los requerimientos normativos vigentes en materia de Reglamentación Térmica y Calificación Energética de Viviendas (CEV).

El encargo considera la elaboración de los informes técnicos exigidos por la Dirección de Obras Municipales (DOM) para el ingreso y aprobación de modificaciones de proyecto, así como la evaluación energética integral del conjunto habitacional.

Adicionalmente, la propuesta incluye la Precalificación y Calificación Energética CEV de cada una de las viviendas del condominio, correspondiente a {{UNIDADES_DESCRIPCION}}, a evaluar con el objetivo de optimizar su desempeño energético y alcanzar la mejor calificación posible dentro del marco normativo vigente.

═══════════════════════════════════════════════════════════════
PROPUESTA TÉCNICA
═══════════════════════════════════════════════════════════════

La presente propuesta técnica se estructura en dos componentes principales, orientados a verificar el cumplimiento normativo y evaluar el desempeño energético del proyecto en etapa de diseño.

1. CUMPLIMIENTO DE LA REGLAMENTACIÓN TÉRMICA

Se elaborará un Informe de Cumplimiento de Reglamentación Térmica válido para presentación ante la Dirección de Obras Municipales (DOM), en el cual se verificará el cumplimiento del Artículo 4.1.10 de la OGUC, aplicable a edificaciones de uso residencial.

El informe considerará los siguientes aspectos prescriptivos:

A. Desempeño térmico de la envolvente
Se verificará el cumplimiento de los requisitos de transmitancia térmica máxima (U) o resistencia térmica mínima (Rt) exigidos para los distintos elementos de la envolvente térmica, incluyendo techumbres, muros perimetrales, pisos ventilados sobre exterior, sobrecimientos, puertas opacas y ventanas.

Para ello, se entregará una memoria de cálculo detallada con la caracterización completa de los materiales que componen la envolvente térmica, considerando espesores, tipos de aislación térmica, soluciones constructivas, tipos de carpintería y especificaciones de vidrios.

Asimismo, se realizará el cálculo de la transmitancia térmica (U) y de la resistencia térmica (Rt o R100) de todos los elementos de la envolvente, verificando adicionalmente los indicadores térmicos de los cristales según la orientación de las fachadas del proyecto.

B. Ausencia de riesgo de condensación
Se desarrollará una memoria de cálculo de condensación superficial e intersticial, aplicando el método de Glaser, para todos los cerramientos del proyecto (muros exteriores, cubiertas y pisos ventilados).

C. Permeabilidad al aire e infiltraciones
Se realizará una revisión de la permeabilidad al aire de puertas y ventanas, considerando clasificación de ventanas según infiltraciones de aire, evaluación de detalles constructivos de sellado y revisión de barreras de vapor.

La prueba de hermeticidad (blower door) no se encuentra incluida en la presente propuesta, pero podrá ser considerada como un servicio adicional si el mandante lo requiere.

D. Ventilación mínima según normativa vigente
Se propondrá un diseño conceptual de soluciones de ventilación adecuadas al proyecto, orientadas a asegurar el cumplimiento de la reglamentación térmica y a mejorar las condiciones de confort y calidad del aire interior.

2. CALIFICACIÓN ENERGÉTICA DE VIVIENDAS CEV

La propuesta incluye la evaluación en la etapa de diseño de los aspectos definidos en la normativa vigente y los parámetros de la calificación energética de viviendas CEV.

2.1. Análisis Preliminar CEV y propuestas de mejora.
Se llevará a cabo la simulación de la vivienda para determinar tempranamente la calificación energética. Se entregará un informe con los resultados preliminares calculados según los planos y especificaciones técnicas de arquitectura.

2.2. Precalificación CEV.
Para la precalificación CEV de la vivienda, es requisito contar con el permiso de edificación aprobado. Se realizará la simulación de cada una de las unidades utilizando la Herramienta de Cálculo de la Calificación Energética de Viviendas (PBDT) del MINVU.

2.3. Calificación energética de viviendas.
Durante la fase de construcción, se realizará una visita obligatoria para verificar que la envolvente especificada se esté implementando conforme al proyecto.

2.4. Calificación CEV.
Una vez finalizada la construcción y obtenida la Recepción Final, se procederá a la Calificación CEV.

═══════════════════════════════════════════════════════════════
HONORARIOS PROFESIONALES
═══════════════════════════════════════════════════════════════

Para definir el monto de los honorarios profesionales se asume que se contratan los 2 servicios descritos en la propuesta:

{{HONORARIOS_TABLA}}

FORMA DE PAGO

{{PAGO_TABLA}}

TOTAL: UF {{TOTAL_UF}}

───────────────────────────────────────────────────────────────
{{PRESENTADO_POR}}
Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |
LEED AP | Asesor CES | Calificador Energético CEV.
B-green Chile

Información de la Empresa.
Nombre: B-green Chile Ltda.
Rut.: 77.748.415-k
Dirección: Obispo Donoso 5 Oficina 62. Providencia.
"""

TEMPLATES_POR_SERVICIO = {
    'CEV+RT': TEMPLATE_CEV_RT,
}


def siguiente_numero_propuesta(empresa_id: int) -> int:
    max_num = (
        Propuesta.query.filter_by(empresa_id=empresa_id)
        .with_entities(Propuesta.numero)
        .order_by(Propuesta.numero.desc())
        .limit(1)
        .scalar()
    )
    return (max_num or 0) + 1


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


def _fmt_fecha_larga(fecha_str: str | None) -> str:
    if not fecha_str:
        hoy = date.today()
        return f'{hoy.day:02d} de {MESES_ES[hoy.month]} de {hoy.year}'
    try:
        partes = str(fecha_str)[:10].split('-')
        if len(partes) == 3:
            anio, mes, dia = int(partes[0]), int(partes[1]), int(partes[2])
            if 1 <= mes <= 12:
                return f'{dia:02d} de {MESES_ES[mes]} de {anio}'
    except (ValueError, IndexError):
        pass
    return str(fecha_str)


class PropuestaPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_margins(20, 20, 20)
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        pass


def generar_pdf_propuesta(titulo: str, contenido: str) -> bytes:
    pdf = PropuestaPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 14)
    pdf.multi_cell(0, 8, _texto_seguro(titulo))
    pdf.ln(4)
    pdf.set_font('Helvetica', '', 10)
    for linea in contenido.split('\n'):
        pdf.multi_cell(0, 5, _texto_seguro(linea))
    return bytes(pdf.output())


def generar_docx_propuesta(titulo: str, contenido: str) -> tuple[bytes, str]:
    """Retorna (bytes, extension: 'docx' | 'doc')."""
    try:
        from docx import Document
        from docx.shared import Pt

        doc = Document()
        titulo_p = doc.add_heading(titulo, level=1)
        titulo_p.runs[0].font.size = Pt(16)
        for linea in contenido.split('\n'):
            p = doc.add_paragraph(linea)
            p.paragraph_format.space_after = Pt(2)
            for run in p.runs:
                run.font.size = Pt(10)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue(), 'docx'
    except ImportError:
        return _generar_doc_html(titulo, contenido), 'doc'


def _generar_doc_html(titulo: str, contenido: str) -> bytes:
    import html
    body = html.escape(contenido).replace('\n', '<br>')
    titulo_esc = html.escape(titulo)
    doc_html = (
        f'<html xmlns:o="urn:schemas-microsoft-com:office:office" '
        f'xmlns:w="urn:schemas-microsoft-com:office:word">'
        f'<head><meta charset="utf-8"><title>{titulo_esc}</title></head>'
        f'<body><h1>{titulo_esc}</h1><div style="font-family:Calibri;font-size:11pt">{body}</div></body></html>'
    )
    return doc_html.encode('utf-8')


def get_config_calculadora(servicio: str) -> dict | None:
    if servicio == 'CEV+RT':
        return {
            'tarifas': TARIFAS_CEV_RT,
            'etapas': ETAPAS_CEV_RT,
            'template': TEMPLATE_CEV_RT,
        }
    return None
