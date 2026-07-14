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
    'CEV',
    'RT',
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

# ---------------------------------------------------------------------------
# RT — Verificación de Reglamentación Térmica (sin CEV)
# ---------------------------------------------------------------------------
# Reutiliza las tarifas por unidad/superficie de CEV+RT (TARIFAS_CEV_RT). No
# existen tarifas específicas por tipo de proyecto: el "tipo de proyecto" es
# descriptivo (aparece en el documento) y NO altera el cálculo de honorarios.
TIPOS_PROYECTO_RT = [
    'Residencial vivienda',
    'Residencial Hotel',
    'Salud',
    'Educación',
]

# Etapas de pago para RT: solo el/los entregable(s) de Reglamentación Térmica
# (informe DOM), ya que en este servicio no hay componente CEV.
ETAPAS_PAGO_RT = [
    {'codigo': '1', 'nombre': 'Anticipo (inicio de servicio)', 'porcentaje': 50.0},
    {'codigo': '1.1', 'nombre': 'Informe cumplimiento RT [DOM]', 'porcentaje': 50.0},
]

# ---------------------------------------------------------------------------
# CEV — Calificación Energética de Viviendas (sin Reglamentación Térmica)
# ---------------------------------------------------------------------------
# Servicio análogo a CEV+RT pero SIN el componente de Reglamentación Térmica:
# solo se realiza la evaluación energética CEV (precalificación + calificación).
# Reutiliza el cálculo por unidad/superficie de CEV+RT (tarifaCevRtPorM2 en el
# frontend); los valores UF por unidad son editables por fila en la calculadora,
# de modo que el usuario puede ajustarlos al no incluir el informe RT.
TARIFAS_CEV = [
    {'label': 'Casas 140 m²', 'm2': 140, 'uf_unidad': 5},
    {'label': 'Casas 200 m²', 'm2': 200, 'uf_unidad': 7},
    {'label': 'Casas 320 m²', 'm2': 320, 'uf_unidad': 11},
]

# Etapas de pago para CEV: precalificación (etapa de diseño) y calificación
# (posterior a la Recepción Final). Sin entregable de Reglamentación Térmica.
ETAPAS_PAGO_CEV = [
    {'codigo': '1', 'nombre': 'Pre Calificación CEV', 'porcentaje': 50.0},
    {'codigo': '2', 'nombre': 'Calificación CEV', 'porcentaje': 50.0},
]

# ---------------------------------------------------------------------------
# CES — Certificación Edificio Sustentable
# ---------------------------------------------------------------------------
# NOTA: los honorarios CES se calculan en el frontend con una fórmula de
# potencia: total = 22,516 * (superficie^0,3571) * factor_nivel * factor_tipo.
# Estos brackets quedan solo como referencia histórica / retrocompatibilidad y
# ya NO se usan para el cálculo (ver cesBasePorM2 en app.html).
TARIFAS_CES = [
    {'m2': 500, 'uf': 180},
    {'m2': 1000, 'uf': 280},
    {'m2': 1250, 'uf': 320},   # ancla propuesta P2070 (nivel Certificado)
    {'m2': 2500, 'uf': 480},
    {'m2': 5000, 'uf': 700},
    {'m2': 10000, 'uf': 1000},
]

# Niveles de certificación CES (puntaje: Certificado ≥30, Destacado ≥54,5,
# Sobresaliente ≥69,5). Los factores están anclados a la fórmula de honorarios
# entregada por el usuario, donde la base (22,516 * m²^0,3571) corresponde al
# nivel Destacado: Certificado = base*0,85, Destacado = base*1,0,
# Sobresaliente = base*1,15.
NIVELES_CES = [
    {'label': 'Edificio Certificado', 'factor': 0.85},
    {'label': 'Certificación Destacada', 'factor': 1.00},
    {'label': 'Certificación Sobresaliente', 'factor': 1.15},
]

# Tipos/versiones de certificación CES (fuente: certificacionsustentable.cl).
# El factor es REFERENCIAL: usos más complejos (hospitales, aeropuertos)
# implican mayor esfuerzo de asesoría.
TIPOS_CES = [
    {'label': 'CES Edificios de Uso Público v1.2', 'factor': 1.00},
    {'label': 'CES Edificios de Uso Público v1.1', 'factor': 1.00},
    {'label': 'CES Edificios de Uso Público v1.0', 'factor': 1.00},
    {'label': 'CES Hospitales v1.1', 'factor': 1.30},
    {'label': 'CES Hospitales v1.0', 'factor': 1.30},
    {'label': 'CES Aeropuertos', 'factor': 1.35},
    {'label': 'CES Edificios Existentes v1', 'factor': 1.10},
]

# Etapas/entregables de honorarios CES. Proporciones ancladas a la referencia
# P2070 (Precertificación 140 / Acompañamiento 90 / Certificación 90 de 320 UF).
ETAPAS_PAGO_CES = [
    {'codigo': 'A', 'nombre': 'Precertificación CES', 'porcentaje': 43.75},
    {'codigo': 'B', 'nombre': 'Acompañamiento en Obra', 'porcentaje': 28.125},
    {'codigo': 'C', 'nombre': 'Certificación CES', 'porcentaje': 28.125},
]

# IVA aplicable (Chile). Solo afecta al arancel de la Entidad Administradora.
IVA_CES = 0.19

# "Otros gastos a considerar" CES — aranceles oficiales de las entidades del
# sistema, ajenos a los honorarios profesionales. Se calculan por interpolación
# lineal por tramos según la superficie total (m²): bajo el primer punto se
# usa el primer valor (clamp) y sobre el último se extrapola con la pendiente
# del último tramo.
#
# A) Entidad Administradora (arancel CES EA). AFECTO A IVA (19%).
#    El punto de 100.000 m² del tarifario se obtiene extrapolando sobre la
#    pendiente del último tramo (40.000→80.000), por lo que se omite y se deja
#    que la extrapolación lo resuelva automáticamente.
ARANCEL_CES_EA = [
    {'m2': 0, 'uf': 20},
    {'m2': 700, 'uf': 30},
    {'m2': 1500, 'uf': 45},
    {'m2': 3000, 'uf': 60},
    {'m2': 5000, 'uf': 75},
    {'m2': 10000, 'uf': 150},
    {'m2': 20000, 'uf': 300},
    {'m2': 40000, 'uf': 400},
    {'m2': 80000, 'uf': 550},
]

# B) Entidad Evaluadora (arancel CES EE). NO afecto a IVA y NO incluye visita
#    de obra.
ARANCEL_CES_EE = [
    {'m2': 1, 'uf': 29},
    {'m2': 200, 'uf': 32},
    {'m2': 700, 'uf': 37},
    {'m2': 1500, 'uf': 42},
    {'m2': 2500, 'uf': 50},
    {'m2': 5000, 'uf': 63},
    {'m2': 10000, 'uf': 76},
    {'m2': 20000, 'uf': 93},
    {'m2': 30000, 'uf': 110},
    {'m2': 40000, 'uf': 120},
    {'m2': 50000, 'uf': 130},
    {'m2': 100000, 'uf': 170},
]

# ---------------------------------------------------------------------------
# CES Evaluadora — Entidad Evaluadora Externa (EEV) del sistema CES
# ---------------------------------------------------------------------------
# En este servicio B-green actúa como Entidad Evaluadora (EE) acreditada ante
# el Instituto de la Construcción. A diferencia del servicio de asesoría CES,
# los honorarios NO se derivan de la fórmula de potencia sobre la superficie,
# sino directamente del arancel de la Entidad Evaluadora (mismo tarifario
# ARANCEL_CES_EE), interpolado por tramos según la superficie del proyecto.
#
# El honorario se estructura en tres filas:
#   A) Evaluación Precertificación: 63% del arancel de la Entidad Evaluadora,
#      calculado automáticamente desde la superficie (m²) con ARANCEL_CES_EE
#      (tipo 'arancel_ee', factor 0.63).
#   B) Certificación: 37% del arancel de la Entidad Evaluadora, calculado
#      automáticamente desde la superficie (tipo 'arancel_ee', factor 0.37).
#      A + B = arancel EE completo, dividido en las dos etapas del proceso CES.
#   C) Visita de obra: valor en UF definido manualmente por el usuario. NO se
#      calcula automáticamente (tipo 'manual').
#
# NOTA: al ser B-green la propia Entidad Evaluadora, NO se incluye el bloque de
# "otros gastos" (aranceles EA/EE) que sí aplica al servicio de asesoría CES:
# el arancel de la Entidad Evaluadora ES el honorario de esta propuesta.
FILAS_CES_EVALUADORA = [
    {'label': 'Evaluación Precertificación', 'uf_unidad': 18.27, 'tipo': 'arancel_ee', 'factor': 0.63},
    {'label': 'Certificación', 'uf_unidad': 10.73, 'tipo': 'arancel_ee', 'factor': 0.37},
    {'label': 'Visita de obra', 'uf_unidad': 50, 'tipo': 'manual'},
]

TEMPLATE_CEV_RT = r"""<div class="prop-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0">
<tr>
  <td class="prop-doc-header-text" valign="top">
    <h1 class="prop-doc-titulo">Calificación energética de viviendas CEV + Verificación Reglamentación térmica.</h1>
    <h2 class="prop-doc-subtitulo" data-prop="proyecto">{{PROYECTO}}</h2>
    <p class="prop-doc-detalle" data-prop="detalle">{{DETALLE}}</p>
  </td>
  <td class="prop-doc-logo-wrap" valign="top" align="right" data-prop="logo">{{LOGO}}</td>
</tr>
</table>
<table class="prop-doc-meta">
  <tr><th>Cliente:</th><td data-prop="cliente">{{CLIENTE}}</td></tr>
  <tr><th>Presentada por:</th><td data-prop="presentado_por">{{PRESENTADO_POR}}</td></tr>
  <tr><th>Fecha:</th><td data-prop="fecha">{{FECHA}}</td></tr>
  <tr><th>ID Propuesta:</th><td data-prop="numero">P{{NUMERO}}</td></tr>
</table>

<h3 class="prop-doc-seccion">Introducción</h3>
<p>La presente Propuesta Técnica se desarrolla para el proyecto <strong data-prop="proyecto">{{PROYECTO}}</strong>, y tiene por objetivo dar cumplimiento a los requerimientos normativos vigentes en materia de Reglamentación Térmica y Calificación Energética de Viviendas (CEV).</p>
<p>El encargo considera la elaboración de los informes técnicos exigidos por la Dirección de Obras Municipales (DOM) para el ingreso y aprobación de modificaciones de proyecto, así como la evaluación energética integral del conjunto habitacional.</p>
<p>Adicionalmente, la propuesta incluye la Precalificación y Calificación Energética CEV de cada una de las viviendas del condominio, correspondiente a <strong data-prop="unidades">{{UNIDADES_DESCRIPCION}}</strong>, a evaluar con el objetivo de optimizar su desempeño energético y alcanzar la mejor calificación posible dentro del marco normativo vigente.</p>

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
<p class="prop-doc-total" data-prop="total_uf"><strong>TOTAL: UF {{TOTAL_UF}}</strong></p>

<div class="prop-doc-firma">
  <table class="prop-doc-firma-tabla" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td class="prop-doc-firma-espaciador"></td>
    <td class="prop-doc-firma-bloque" width="250" align="center" valign="top">
      <div class="prop-doc-firma-space" data-prop="firma_img"></div>
      <div class="prop-doc-firma-linea">&nbsp;</div>
      <p class="prop-doc-firma-nombre"><strong data-prop="presentado_por">{{PRESENTADO_POR}}</strong></p>
    </td>
  </tr></table>
  <p class="prop-doc-firma-cargo">Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |<br>
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

TEMPLATE_RT = r"""<div class="prop-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0">
<tr>
  <td class="prop-doc-header-text" valign="top">
    <h1 class="prop-doc-titulo">Verificación de Reglamentación Térmica.</h1>
    <h2 class="prop-doc-subtitulo" data-prop="proyecto">{{PROYECTO}}</h2>
    <p class="prop-doc-detalle" data-prop="detalle">{{DETALLE}}</p>
  </td>
  <td class="prop-doc-logo-wrap" valign="top" align="right" data-prop="logo">{{LOGO}}</td>
</tr>
</table>
<table class="prop-doc-meta">
  <tr><th>Cliente:</th><td data-prop="cliente">{{CLIENTE}}</td></tr>
  <tr><th>Presentada por:</th><td data-prop="presentado_por">{{PRESENTADO_POR}}</td></tr>
  <tr><th>Tipo de proyecto:</th><td data-prop="tipo_proyecto">{{TIPO_PROYECTO}}</td></tr>
  <tr><th>Fecha:</th><td data-prop="fecha">{{FECHA}}</td></tr>
  <tr><th>ID Propuesta:</th><td data-prop="numero">P{{NUMERO}}</td></tr>
</table>

<h3 class="prop-doc-seccion">Introducción</h3>
<p>La presente Propuesta Técnica se desarrolla para el proyecto <strong data-prop="proyecto">{{PROYECTO}}</strong>, correspondiente a un proyecto de tipo <strong data-prop="tipo_proyecto">{{TIPO_PROYECTO}}</strong>, y tiene por objetivo dar cumplimiento a los requerimientos normativos vigentes en materia de Reglamentación Térmica.</p>
<p>El encargo considera la elaboración de los informes técnicos exigidos por la Dirección de Obras Municipales (DOM) para el ingreso y aprobación del proyecto, verificando el cumplimiento de la envolvente térmica de <strong data-prop="unidades">{{UNIDADES_DESCRIPCION}}</strong> conforme al marco normativo vigente.</p>

<h3 class="prop-doc-seccion">Propuesta Técnica</h3>
<p>La presente propuesta técnica está orientada a verificar el cumplimiento normativo del proyecto en etapa de diseño en materia de Reglamentación Térmica.</p>

<h4>Cumplimiento de la Reglamentación Térmica</h4>
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

<h3 class="prop-doc-seccion">Honorarios Profesionales</h3>
<p>Para definir el monto de los honorarios profesionales se considera el servicio de Verificación de Reglamentación Térmica descrito en la presente propuesta:</p>
<div id="prop-bloque-honorarios">{{HONORARIOS_TABLA}}</div>

<h4>Forma de pago</h4>
<div id="prop-bloque-pago">{{PAGO_TABLA}}</div>
<p class="prop-doc-total" data-prop="total_uf"><strong>TOTAL: UF {{TOTAL_UF}}</strong></p>

<div class="prop-doc-firma">
  <table class="prop-doc-firma-tabla" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td class="prop-doc-firma-espaciador"></td>
    <td class="prop-doc-firma-bloque" width="250" align="center" valign="top">
      <div class="prop-doc-firma-space" data-prop="firma_img"></div>
      <div class="prop-doc-firma-linea">&nbsp;</div>
      <p class="prop-doc-firma-nombre"><strong data-prop="presentado_por">{{PRESENTADO_POR}}</strong></p>
    </td>
  </tr></table>
  <p class="prop-doc-firma-cargo">Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |<br>
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

TEMPLATE_CEV = r"""<div class="prop-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0">
<tr>
  <td class="prop-doc-header-text" valign="top">
    <h1 class="prop-doc-titulo">Calificación Energética de Viviendas (CEV).</h1>
    <h2 class="prop-doc-subtitulo" data-prop="proyecto">{{PROYECTO}}</h2>
    <p class="prop-doc-detalle" data-prop="detalle">{{DETALLE}}</p>
  </td>
  <td class="prop-doc-logo-wrap" valign="top" align="right" data-prop="logo">{{LOGO}}</td>
</tr>
</table>
<table class="prop-doc-meta">
  <tr><th>Cliente:</th><td data-prop="cliente">{{CLIENTE}}</td></tr>
  <tr><th>Presentada por:</th><td data-prop="presentado_por">{{PRESENTADO_POR}}</td></tr>
  <tr><th>Fecha:</th><td data-prop="fecha">{{FECHA}}</td></tr>
  <tr><th>ID Propuesta:</th><td data-prop="numero">P{{NUMERO}}</td></tr>
</table>

<h3 class="prop-doc-seccion">Introducción</h3>
<p>La presente Propuesta Técnica se desarrolla para el proyecto <strong data-prop="proyecto">{{PROYECTO}}</strong>, y tiene por objetivo realizar la evaluación energética integral del conjunto habitacional en el marco de la Calificación Energética de Viviendas (CEV) del Ministerio de Vivienda y Urbanismo (MINVU).</p>
<p>El encargo considera la Precalificación y Calificación Energética CEV de cada una de las viviendas del proyecto, correspondiente a <strong data-prop="unidades">{{UNIDADES_DESCRIPCION}}</strong>, con el objetivo de optimizar su desempeño energético y alcanzar la mejor calificación posible dentro del marco normativo vigente.</p>
<p>La Calificación Energética de Viviendas es un instrumento que informa sobre el desempeño energético de una vivienda en su etapa de uso, entregando una etiqueta de eficiencia energética de conocimiento público que constituye un valor agregado para el proyecto tanto en su comercialización como en el confort de sus futuros ocupantes.</p>

<h3 class="prop-doc-seccion">Propuesta Técnica</h3>
<p>La presente propuesta técnica está orientada a evaluar el desempeño energético del proyecto en etapa de diseño y a gestionar su Calificación Energética de Viviendas ante la plataforma del MINVU.</p>

<h4>Calificación energética de viviendas CEV</h4>
<p>La propuesta incluye la evaluación en la etapa de diseño de los aspectos definidos en la normativa vigente, recientemente actualizada, y los parámetros de la calificación energética de viviendas CEV. Producto de esa evaluación se busca obtener la calificación óptima para el proyecto.</p>
<p><strong>1. Análisis Preliminar CEV y propuestas de mejora.</strong><br>
Se llevará a cabo la simulación de la vivienda para determinar tempranamente la calificación energética. Se entregará un informe con los resultados preliminares calculados según los planos y especificaciones técnicas de arquitectura. En caso de no cumplir con el objetivo trazado por el mandante, se propondrán mejoras con el fin de alcanzar la calificación deseada.</p>
<p><strong>2. Precalificación CEV.</strong><br>
Para la precalificación CEV de la vivienda, es requisito contar con el permiso de edificación aprobado. Se realizará la simulación de cada una de las unidades utilizando la Herramienta de Cálculo de la Calificación Energética de Viviendas (PBDT) del MINVU, obteniendo el informe de precalificación que acompaña al proyecto durante la etapa de construcción.</p>
<p><strong>3. Verificación en obra.</strong><br>
Durante la fase de construcción, se realizará una visita obligatoria para verificar que la envolvente especificada se esté implementando conforme al proyecto. Para ello, se solicitará a la constructora la entrega de una copia de las facturas de compra de los elementos de la envolvente (aislantes térmicos, cristales, etc.).</p>
<p><strong>4. Calificación CEV.</strong><br>
Una vez finalizada la construcción y obtenida la Recepción Final, se procederá a la Calificación CEV. En esta etapa será necesario subir las planillas de simulación a la página del MINVU. Para las viviendas existentes, se deberá considerar la envolvente construida. El resultado de esta etapa es la etiqueta de eficiencia energética definitiva de cada vivienda.</p>

<h3 class="prop-doc-seccion">Honorarios Profesionales</h3>
<p>Para definir el monto de los honorarios profesionales se considera el servicio de Calificación Energética de Viviendas descrito en la presente propuesta:</p>
<div id="prop-bloque-honorarios">{{HONORARIOS_TABLA}}</div>

<h4>Forma de pago</h4>
<div id="prop-bloque-pago">{{PAGO_TABLA}}</div>
<p class="prop-doc-total" data-prop="total_uf"><strong>TOTAL: UF {{TOTAL_UF}}</strong></p>

<div class="prop-doc-firma">
  <table class="prop-doc-firma-tabla" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td class="prop-doc-firma-espaciador"></td>
    <td class="prop-doc-firma-bloque" width="250" align="center" valign="top">
      <div class="prop-doc-firma-space" data-prop="firma_img"></div>
      <div class="prop-doc-firma-linea">&nbsp;</div>
      <p class="prop-doc-firma-nombre"><strong data-prop="presentado_por">{{PRESENTADO_POR}}</strong></p>
    </td>
  </tr></table>
  <p class="prop-doc-firma-cargo">Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |<br>
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

TEMPLATE_CES = r"""<div class="prop-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0">
<tr>
  <td class="prop-doc-header-text" valign="top">
    <h1 class="prop-doc-titulo">Certificación Edificio Sustentable (CES)</h1>
    <h2 class="prop-doc-subtitulo" data-prop="proyecto">{{PROYECTO}}</h2>
    <p class="prop-doc-detalle" data-prop="detalle">{{DETALLE}}</p>
  </td>
  <td class="prop-doc-logo-wrap" valign="top" align="right" data-prop="logo">{{LOGO}}</td>
</tr>
</table>
<table class="prop-doc-meta">
  <tr><th>Cliente:</th><td data-prop="cliente">{{CLIENTE}}</td></tr>
  <tr><th>Presentada por:</th><td data-prop="presentado_por">{{PRESENTADO_POR}}</td></tr>
  <tr><th>Fecha:</th><td data-prop="fecha">{{FECHA}}</td></tr>
  <tr><th>ID Propuesta:</th><td data-prop="numero">P{{NUMERO}}</td></tr>
</table>

<h3 class="prop-doc-seccion">Resumen</h3>
<p>La presente propuesta tiene por objetivo la asesoría integral para la obtención de la <strong>Certificación Edificio Sustentable (CES)</strong> del proyecto <strong data-prop="proyecto">{{PROYECTO}}</strong>, correspondiente a <strong data-prop="unidades">{{UNIDADES_DESCRIPCION}}</strong>.</p>

<h3 class="prop-doc-seccion">Propuesta Técnica</h3>
<p>La Certificación Edificio Sustentable (CES) es un sistema nacional que permite evaluar, calificar y certificar el comportamiento ambiental de edificios de uso público en Chile (tanto nuevos como existentes). El sistema es administrado por el Instituto de la Construcción (IC) como entidad independiente.</p>
<p>El sistema evalúa el diseño y la operación de las edificaciones en base a los siguientes aspectos ambientales fundamentales:</p>
<p>
&#9679; <strong>Calidad Ambiental Interior</strong> (confort térmico, lumínico, acústico y calidad del aire).<br>
&#9679; <strong>Uso de Energía</strong> (eficiencia energética, sistemas de climatización e iluminación).<br>
&#9679; <strong>Uso del Agua</strong> (reducción del consumo de agua potable).<br>
&#9679; <strong>Gestión</strong> (residuos, operación y mantención).<br>
&#9679; <strong>Innovación</strong> (estrategias sustentables adicionales).
</p>
<p>El proceso de certificación se divide formalmente en dos grandes etapas consecutivas.</p>

<h4>Etapa 1: Precertificación (Fase de Diseño)</h4>
<p>Tiene como objetivo evaluar el proyecto en su etapa de diseño (arquitectura y especialidades) antes de iniciar la construcción, asegurando que las estrategias de sustentabilidad queden correctamente plasmadas en los planos y especificaciones técnicas.</p>
<p><strong>Paso 1.1: Diagnóstico Inicial y Planificación</strong><br>
Revisión de los antecedentes del proyecto de arquitectura y definición de la meta de puntaje objetivo (Edificio Certificado, Certificación Destacada o Certificación Sobresaliente).</p>
<p><strong>Paso 1.2: Modelaciones y Evaluaciones Técnicas</strong><br>
Desarrollo de las simulaciones energéticas y de iluminación natural requeridas por la metodología CES, además de la evaluación de los requerimientos de agua, calidad de aire y confort acústico.</p>
<p><strong>Paso 1.3: Ingreso y Validación ante la Entidad Evaluadora</strong><br>
Recopilación y ordenamiento de las evidencias de diseño, ingreso del expediente a la Entidad Evaluadora asignada por el Administrador CES y respuesta a observaciones hasta la obtención del Certificado de Precertificación.</p>

<h4>Etapa 2: Certificación (Fase de Construcción y Término de Obra)</h4>
<p>Busca verificar que todo lo proyectado y aprobado en la precertificación se ejecute fielmente en la obra, controlando los cambios o modificaciones que puedan surgir en la construcción.</p>
<p><strong>Paso 2.1: Acompañamiento en Obra y Control de Cambios</strong><br>
Inducción al contratista principal sobre las exigencias CES, revisión de fichas técnicas de materiales y equipos adquiridos y su correspondencia con lo aprobado en diseño, e inspecciones periódicas a la obra.</p>
<p><strong>Paso 2.2: Recopilación de Antecedentes "As-Built" (Como Construido)</strong><br>
Preparación del expediente final con planos definitivos, fotografías de respaldo y carpetas de especialidades ejecutadas.</p>
<p><strong>Paso 2.3: Auditoría Final y Certificación</strong><br>
Ingreso del expediente de construcción a la Entidad Evaluadora, coordinación de la visita inspectiva del evaluador de ser necesario, levantamiento de observaciones y obtención de la Placa de Certificación CES.</p>

<h4>Entregables Principales</h4>
<p>
1. <strong>Informe de Diagnóstico Inicial:</strong> matriz de puntos objetivo y brechas respecto al diseño base.<br>
2. <strong>Informes de Modelación:</strong> reportes de simulación térmica, energética y lumínica.<br>
3. <strong>Expedientes de Ingreso:</strong> carpetas ordenadas por requerimiento exigidas por la plataforma CES (Precertificación y Certificación).<br>
4. <strong>Informes de Visita de Obra:</strong> minutas de control durante la etapa de construcción.
</p>

<h3 class="prop-doc-seccion">Honorarios Profesionales</h3>
<p>Los honorarios se determinan en función de la superficie del proyecto, el nivel de certificación objetivo y el tipo/versión de certificación CES seleccionado.</p>
<div id="prop-bloque-honorarios">{{HONORARIOS_TABLA}}</div>
<p class="text-muted">*El pago de la inscripción CES se realiza al inicio del proceso de Precertificación.</p>

<h4>Forma de pago</h4>
<div id="prop-bloque-pago">{{PAGO_TABLA}}</div>
<p class="prop-doc-total" data-prop="total_uf"><strong>TOTAL: UF {{TOTAL_UF}}</strong></p>

<h4>Otros gastos a considerar</h4>
<p>Los siguientes aranceles corresponden a las entidades del sistema CES y son ajenos a los honorarios profesionales. Se estiman en función de la superficie total del proyecto.</p>
<div id="prop-bloque-otros-gastos">{{OTROS_GASTOS_TABLA}}</div>
<p class="text-muted">A. Entidad Administradora (arancel CES EA): valor afecto a IVA (19%). B. Entidad Evaluadora (arancel CES EE): no incluye visita de obra.</p>

<div class="prop-doc-firma">
  <table class="prop-doc-firma-tabla" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td class="prop-doc-firma-espaciador"></td>
    <td class="prop-doc-firma-bloque" width="250" align="center" valign="top">
      <div class="prop-doc-firma-space" data-prop="firma_img"></div>
      <div class="prop-doc-firma-linea">&nbsp;</div>
      <p class="prop-doc-firma-nombre"><strong data-prop="presentado_por">{{PRESENTADO_POR}}</strong></p>
    </td>
  </tr></table>
  <p class="prop-doc-firma-cargo">Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |<br>
  LEED AP | Asesor CES.<br>
  B-green Chile</p>
</div>
<div class="prop-doc-empresa">
  <p><strong>Información de la Empresa</strong></p>
  <p>Nombre: B-green Chile Ltda.<br>
  Giro: Desarrollo de Consultorías y Arquitectura<br>
  Rut.: 77.748.415-k<br>
  Dirección: Obispo Donoso 5 Oficina 62. Providencia.</p>
</div>
</div>"""

TEMPLATE_CES_EVALUADORA = r"""<div class="prop-doc">
<table class="prop-doc-header" cellpadding="0" cellspacing="0">
<tr>
  <td class="prop-doc-header-text" valign="top">
    <h1 class="prop-doc-titulo">Propuesta de Evaluación — Entidad Evaluadora CES (EEV)</h1>
    <h2 class="prop-doc-subtitulo" data-prop="proyecto">{{PROYECTO}}</h2>
    <p class="prop-doc-detalle" data-prop="detalle">{{DETALLE}}</p>
  </td>
  <td class="prop-doc-logo-wrap" valign="top" align="right" data-prop="logo">{{LOGO}}</td>
</tr>
</table>
<table class="prop-doc-meta">
  <tr><th>Cliente:</th><td data-prop="cliente">{{CLIENTE}}</td></tr>
  <tr><th>Presentada por:</th><td data-prop="presentado_por">{{PRESENTADO_POR}}</td></tr>
  <tr><th>Fecha:</th><td data-prop="fecha">{{FECHA}}</td></tr>
  <tr><th>ID Propuesta:</th><td data-prop="numero">P{{NUMERO}}</td></tr>
</table>

<h3 class="prop-doc-seccion">Resumen</h3>
<p>La presente propuesta tiene por objetivo la <strong>evaluación como Entidad Evaluadora (EE)</strong> del proyecto <strong data-prop="proyecto">{{PROYECTO}}</strong> dentro del proceso de Certificación Edificio Sustentable (CES), correspondiente a <strong data-prop="unidades">{{UNIDADES_DESCRIPCION}}</strong>.</p>
<p><strong>Descripción de la propuesta</strong></p>
<p>
&#9679; <strong>Servicio:</strong> Entidad Evaluadora CES.<br>
&#9679; <strong>Formato:</strong> Hospitales.<br>
&#9679; <strong>Nivel:</strong> Certificación Destacada.
</p>

<h3 class="prop-doc-seccion">Propuesta Técnica de Evaluación</h3>
<p>Sistema Nacional de Certificación de Calidad Ambiental y Eficiencia Energética para Edificios de Uso Público (CES).</p>

<h4>1. Introducción</h4>
<p>La Certificación Edificio Sustentable (CES), otorgada por el Instituto de la Construcción, es un sistema nacional voluntario de certificación de edificios, tanto de uso público como privado, cuyo propósito es evaluar, calificar y certificar el nivel de sustentabilidad ambiental de una edificación abordando las siguientes seis categorías:</p>
<p>
&#9679; <strong>Calidad del Ambiente Interior</strong> (confort térmico, lumínico, acústico y calidad del aire).<br>
&#9679; <strong>Energía</strong> (demanda y consumo, eficiencia de los sistemas activos).<br>
&#9679; <strong>Agua</strong> (reducción del consumo de agua potable y riego).<br>
&#9679; <strong>Residuos</strong> (gestión de residuos de la construcción y de la operación).<br>
&#9679; <strong>Gestión</strong> (operación, mantención y control del edificio).<br>
&#9679; <strong>Innovación</strong> (estrategias sustentables adicionales).
</p>
<p>bgreen Chile es Entidad Evaluadora (EE) del sistema de certificación CES, somos una empresa acreditada ante la Entidad Administradora del sistema (Instituto de la Construcción). Contamos con las competencias técnicas, experiencia institucional y profesional exigidas por el Manual de Operación.</p>
<p>De acuerdo con las exigencias del sistema CES, nuestra labor se rige bajo el <strong>Principio de Neutralidad</strong>: Actuamos de forma estrictamente neutral respecto al cumplimiento de los requerimientos. Acotamos nuestro quehacer a verificar y comunicar de manera imparcial si el proyecto cumple o no cumple con las exigencias.</p>

<h4>2. Alcance de los Servicios por Etapa</h4>
<p>La evaluación se estructurará utilizando la Herramienta de Evaluación oficial en formato Microsoft&reg; Excel, verificando el universo de 23 variables del sistema (15 requerimientos obligatorios y 33 voluntarios).</p>

<p><strong>Etapa 1: Precertificación (Fase de Diseño o Construcción)</strong></p>
<p>Aplica a proyectos con diseño de arquitectura y especialidades terminado o en etapa de construcción. Se evalúa el desempeño proyectado de la arquitectura pasiva y los sistemas activos.</p>
<p>&#9679; <strong>Revisión de Requerimientos Obligatorios:</strong> Evaluación del cumplimiento técnico binario ("Línea Base" o Paso/No Paso). Incluye verificar el factor de luz día mínimo (FLD), las tasas mínimas de ventilación mecánica, la transmitancia térmica de la envolvente (U), los sellos exteriores de carpintería, la reducción del 20% en consumo de agua potable y riego, y las medidas de mitigación en construcción (Art. 5.8.3 OGUC).</p>
<p>&#9679; <strong>Revisión de Requerimientos Voluntarios (Estrategias de Puntaje):</strong> Auditoría mediante la Herramienta de Cálculo oficial o simulaciones dinámicas entregadas por el Asesor. Se validan los puntos solicitados en confort térmico pasivo, autonomía de iluminación (sDA), reducción avanzada de demanda energética, uso de Energías Renovables No Convencionales (ERNC) y materiales con eco-etiquetas (declaración de energía y agua incorporada).</p>

<p><strong>Etapa 2: Certificación (Fase de Edificio Terminado)</strong></p>
<p>Aplica una vez que el edificio cuenta con la Recepción Definitiva Total por parte de la Dirección de Obras Municipales (DOM). Consiste en la recopilación de antecedentes finales y la ejecución de la auditoría en obra.</p>
<p>&#9679; <strong>Evaluación en Obra (Metodología de Verificación):</strong> Dividimos el proceso de inspección según la visibilidad de las partidas, excluyendo el área vital primaria (estructuras o impermeabilizaciones ajenas al manual):</p>
<p style="margin-left:24px">&#9675; <strong>Área Visible:</strong> Partidas expuestas al examen visual (luminarias, griferías, termostatos, ventanas). Se auditarán en terreno mediante un muestreo estadístico basado en el tamaño del lote (según tablas de adaptación ISO 2859-1 y NCh440).</p>
<p style="margin-left:24px">&#9675; <strong>Área No Visible:</strong> Partidas que quedan ocultas (aislación térmica en muros/cubiertas, ductos). Su verificación se realizará mediante la revisión documental de las Medidas de Autocontrol y/o Inspección (MAI) de la constructora o de la ITO (cartillas de control, libro de obras, ensayos, registros fotográficos). Se exigirá el certificado del instalador según el Reglamento de Instalaciones Térmicas (RITCH).</p>

<p><strong>Servicio Opcional: Sello "Plus Operación"</strong></p>
<p>Sello voluntario e independiente para evaluar la gestión durante la fase operativa del edificio. Requiere el compromiso del cliente de entregar anualmente Informes de Auto-diagnóstico detallando consumos mensuales de energía, agua y encuestas de satisfacción de usuarios. Al 3er año de operación se gestionará la renovación del sello ante la Entidad Administradora tras demostrar el mejoramiento respecto a la línea base del año 1.</p>

<p><strong>Contenido Detallado de los Informes de Revisión</strong></p>
<p>Cada informe contiene las siguientes secciones:</p>
<p>
1. <strong>Cuadro Resumen de Evaluación:</strong> Estado consolidado de los requerimientos obligatorios e indicación del nivel de certificación proyectado (Edificio Certificado, Destacada o Sobresaliente).<br>
2. <strong>Matriz de Requerimientos Obligatorios:</strong> Desglose pormenorizado con el indicador verificado, la metodología de cálculo seleccionada (prescriptiva o prestacional) y la declaración explícita de Cumple o No Cumple.<br>
3. <strong>Matriz de Asignación de Puntaje (Voluntarios):</strong> Tabla comparativa entre el puntaje al que opta el cliente y el puntaje neto validado por la Entidad Evaluadora tras auditar las memorias y modelaciones dinámicas.<br>
4. <strong>Anexo Documental de Respaldo:</strong> Recopilación e indexación de las solicitudes, actas de observaciones emitidas, bitácoras fotográficas de terreno (para certificación) y los oficios de respuesta enviados por el cliente.
</p>

<h4>3. Plazos Oficiales de Revisión (Días Hábiles)</h4>
<p>Los tiempos de procesamiento están regulados por el Manual de Operación CES. No se consideran días corridos, sino días hábiles:</p>
<table class="prop-tabla">
<thead>
<tr><th>Fase del Proceso</th><th class="text-end">Plazo Entidad Evaluadora</th><th class="text-end">Plazo Cliente / Asesor</th><th class="text-end">Plazo Entidad Administradora</th></tr>
</thead>
<tbody>
<tr><td colspan="4"><strong>Etapa de Pre Certificación</strong></td></tr>
<tr><td>Generación de Acta de Observaciones (R1)</td><td class="text-end">20 días</td><td class="text-end">-</td><td class="text-end">-</td></tr>
<tr><td>Respuesta a observaciones (Oficio de Respuestas)</td><td class="text-end">-</td><td class="text-end">20 días</td><td class="text-end">-</td></tr>
<tr><td>Elaboración de Informe de Evaluación de Diseño (R2)</td><td class="text-end">10 días</td><td class="text-end">-</td><td class="text-end">-</td></tr>
<tr><td>Período para interponer Apelación técnica</td><td class="text-end">-</td><td class="text-end">9 días</td><td class="text-end">-</td></tr>
<tr><td>Resolución de Apelación y Emisión de Pre-certificado</td><td class="text-end">-</td><td class="text-end">-</td><td class="text-end">10 días</td></tr>
<tr><td colspan="4"><strong>Etapa de Certificación Final</strong></td></tr>
<tr><td>Visita en obra y emisión de Acta de Observaciones</td><td class="text-end">20 días</td><td class="text-end">-</td><td class="text-end">-</td></tr>
<tr><td>Respuesta a observaciones de terreno</td><td class="text-end">-</td><td class="text-end">20 días</td><td class="text-end">-</td></tr>
<tr><td>Elaboración de Informe Final de Diseño y Construcción</td><td class="text-end">10 días</td><td class="text-end">-</td><td class="text-end">-</td></tr>
<tr><td>Período para interponer Apelación final</td><td class="text-end">-</td><td class="text-end">9 días</td><td class="text-end">-</td></tr>
<tr><td>Emisión de Certificado Final de Proyecto Construido</td><td class="text-end">-</td><td class="text-end">-</td><td class="text-end">10 días</td></tr>
</tbody>
</table>
<p><strong>Nota de Vigencia y Modificaciones:</strong> El Pre-certificado de Diseño expira automáticamente a los 6 meses de obtenida la recepción municipal o al emitirse el certificado final. Si las obras no se inician en 2 años, deberá renovarse. Durante la operación, el cliente se obliga a declarar cualquier cambio menor o mayor (umbrales del 5% y 20% de la superficie o instalaciones) que pueda alterar el puntaje u obligar a repetir la certificación.</p>

<h3 class="prop-doc-seccion">Propuesta Económica — Honorarios Profesionales</h3>
<p>Los honorarios de evaluación se derivan del arancel de la Entidad Evaluadora según la superficie del proyecto, dividido en sus dos etapas: <strong>Evaluación Precertificación</strong> (63% del arancel, fase de diseño) y <strong>Certificación</strong> (37% del arancel, fase de edificio terminado), más la visita de obra. Se considera el pago anticipado de los costos y honorarios en cada revisión.</p>
<div id="prop-bloque-honorarios">{{HONORARIOS_TABLA}}</div>
<p class="prop-doc-total" data-prop="total_uf"><strong>TOTAL: UF {{TOTAL_UF}}</strong></p>
<p class="text-muted">&#8226; El valor anterior no incluye una eventual revisión en etapa de apelación. En caso de requerirse una revisión de apelación, el costo es de 6 UF.<br>
&#8226; Se considera emisión de factura exenta.</p>

<div class="prop-doc-firma">
  <table class="prop-doc-firma-tabla" cellpadding="0" cellspacing="0" width="100%"><tr>
    <td class="prop-doc-firma-espaciador"></td>
    <td class="prop-doc-firma-bloque" width="250" align="center" valign="top">
      <div class="prop-doc-firma-space" data-prop="firma_img"></div>
      <div class="prop-doc-firma-linea">&nbsp;</div>
      <p class="prop-doc-firma-nombre"><strong data-prop="presentado_por">{{PRESENTADO_POR}}</strong></p>
    </td>
  </tr></table>
  <p class="prop-doc-firma-cargo">Socio B-green Chile<br>
  Arquitecto PUC | Master en Medio Ambiente y Arquitectura Bioclimática U. Politécnica de Madrid |<br>
  LEED AP | Asesor CES.<br>
  B-green Chile</p>
</div>
<div class="prop-doc-empresa">
  <p><strong>Información de la Empresa</strong></p>
  <p>Nombre: B-green Chile Ltda.<br>
  Giro: Desarrollo de Consultorías y Arquitectura<br>
  Rut.: 77.748.415-k<br>
  Dirección: Obispo Donoso 5 Oficina 62. Providencia.</p>
</div>
</div>"""

TEMPLATES_POR_SERVICIO = {
    'CEV+RT': TEMPLATE_CEV_RT,
    'CEV': TEMPLATE_CEV,
    'RT': TEMPLATE_RT,
    'CES': TEMPLATE_CES,
    'CES Evaluadora': TEMPLATE_CES_EVALUADORA,
}

PROP_DOC_CSS = """
body { font-family: Roboto, Helvetica, Arial, sans-serif; font-size: 9pt; color: #222222; line-height: 1.45; margin: 0; padding: 0; }
.prop-doc { width: 100%; font-size: 9pt; }
table.prop-doc-header { width: 100%; border-collapse: collapse; border-bottom: 2px solid #008080; margin-bottom: 12px; }
table.prop-doc-header td { vertical-align: top; padding: 0 0 8px 0; border: none; }
.prop-doc-header-text { width: 72%; }
.prop-doc-logo-wrap { width: 28%; text-align: right; vertical-align: top; }
.prop-doc-titulo { font-family: Roboto, Helvetica, Arial, sans-serif; font-size: 13pt; font-weight: bold; margin: 0 0 4px 0; padding: 0; color: #111111; }
.prop-doc-subtitulo { font-family: Roboto, Helvetica, Arial, sans-serif; font-size: 11pt; font-weight: bold; margin: 0; padding: 0; color: #008080; }
h1.prop-doc-titulo { font-size: 13pt; }
h2.prop-doc-subtitulo { font-size: 11pt; }
.prop-doc-detalle { font-family: Roboto, Helvetica, Arial, sans-serif; font-size: 9.5pt; font-weight: normal; margin: 2px 0 0 0; padding: 0; color: #444444; }
.prop-doc-logo { max-height: 56px; max-width: 120px; height: auto; display: inline-block; }
table.prop-doc-meta { width: 100%; border-collapse: collapse; margin-bottom: 14px; font-size: 8.5pt; }
table.prop-doc-meta th { text-align: left; padding: 2px 10px 2px 0; font-weight: bold; vertical-align: top; border: none; }
table.prop-doc-meta td { padding: 2px 0; border: none; vertical-align: top; }
h3.prop-doc-seccion { font-family: Roboto, Helvetica, Arial, sans-serif; font-size: 10.5pt; font-weight: bold; color: #008080; margin: 14px 0 8px 0; padding: 0 0 4px 0; border-bottom: 1px solid #dddddd; text-transform: uppercase; }
h4 { font-family: Roboto, Helvetica, Arial, sans-serif; font-size: 10pt; font-weight: bold; margin: 10px 0 6px 0; color: #222222; }
p { margin: 0 0 8px 0; text-align: justify; font-size: 9pt; }
strong { font-weight: bold; }
table.prop-tabla { width: 100%; border-collapse: collapse; margin: 8px 0 12px 0; font-size: 8.5pt; }
table.prop-tabla th, table.prop-tabla td { border: 1px solid #cccccc; padding: 5px 8px; vertical-align: top; }
table.prop-tabla th { background-color: #f1f3f5; font-weight: bold; text-align: left; }
table.prop-tabla tfoot td { font-weight: bold; }
.text-end { text-align: right; }
.fw-bold { font-weight: bold; }
.text-muted { color: #6c757d; }
.prop-doc-total { margin-top: 10px; font-size: 10pt; }
.prop-doc-firma, .prop-doc-empresa { margin-top: 14px; padding-top: 10px; border-top: 1px solid #e9ecef; font-size: 8.5pt; }
/* Bloque de firma alineado a la derecha mediante una tabla: la celda derecha
   tiene ancho fijo (250px) y la izquierda actúa de espaciador. xhtml2pdf respeta
   anchos de celda pero no el shrink-wrap de inline-block, por eso se usa tabla. */
table.prop-doc-firma-tabla { width: 100%; border-collapse: collapse; }
table.prop-doc-firma-tabla td { border: none; padding: 0; }
.prop-doc-firma-espaciador { border: none; }
td.prop-doc-firma-bloque { width: 250px; text-align: center; vertical-align: top; }
/* La línea va debajo de la imagen (sin margen negativo): xhtml2pdf ignora
   márgenes negativos de forma impredecible y además descarta divs vacíos
   con solo border-top, por lo que la línea debe contener &nbsp;. */
.prop-doc-firma-space { min-height: 40px; height: auto; line-height: 0; text-align: center; }
.prop-doc-firma-img { display: block; margin: 0 auto 0 auto; max-height: 70px; max-width: 220px; width: auto; height: auto; }
.prop-doc-firma-linea { width: 100%; border: 0; border-top: 1px solid #333333; margin: 2px 0 4px 0; height: 1px; font-size: 1px; line-height: 1px; }
.prop-doc-firma-nombre { margin: 0 0 4px 0; text-align: center; }
.prop-doc-firma-cargo { text-align: right; margin: 4px 0 0 0; }
"""

PROP_PDF_CSS = """
@page {
  size: a4;
  margin: 2cm 2cm 2.5cm 2cm;
  @frame footer_frame {
    -pdf-frame-content: footerContent;
    bottom: 1cm;
    margin-left: 2cm;
    margin-right: 2cm;
    height: 1cm;
  }
}
#footerContent {
  font-family: Roboto, Helvetica, Arial, sans-serif;
  font-size: 9pt;
  color: #666666;
  text-align: right;
}
""" + PROP_DOC_CSS

_FONTS_DIR = Path(__file__).parent / 'fonts'


def _registrar_fuentes_pdf() -> None:
    regular = _FONTS_DIR / 'Roboto-Regular.ttf'
    bold = _FONTS_DIR / 'Roboto-Bold.ttf'
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        if regular.is_file() and 'Roboto' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('Roboto', str(regular)))
        if bold.is_file() and 'Roboto-Bold' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('Roboto-Bold', str(bold)))
    except Exception:
        pass


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


def _normalizar_html_para_pdf(html: str) -> str:
    import html as html_mod

    out = html_mod.unescape(str(html or ''))
    # Auto-reparación de la corrupción "char-por-char": un mismo token N,N
    # (p.ej. el total UF "10,0"/"7,0") quedó insertado entre CADA carácter. Se
    # elimina el token repetido (mismo token, 4+ veces, sin cruzar etiquetas),
    # restaurando el texto. No toca números legítimos ("UF 10,0", "33,33%").
    out = re.sub(r'[^<>](\d{1,4},\d)(?:[^<>]\1){3,}', lambda m: m.group(0).replace(m.group(1), ''), out)
    out = re.sub(r'\{7,0(?:\{7,0|[LG]7,0|\})+\}', '', out)
    out = re.sub(r'\{\{LOGO\}\}', '', out)
    out = re.sub(r'<div[^>]*id=["\']footerContent["\'][^>]*>[\s\S]*?</div>', '', out, flags=re.I)
    out = re.sub(r'<span[^>]*class="[^"]*prop-col-resize-grip[^"]*"[^>]*></span>', '', out, flags=re.I)
    out = re.sub(r'\scontenteditable="[^"]*"', '', out, flags=re.I)
    out = re.sub(r'\sdata-(?:prop|resize-key|editado|servicio)="[^"]*"', '', out, flags=re.I)
    # El resaltado de campos automáticos nunca debe aparecer en el PDF/Word.
    out = re.sub(r'\s*\bprop-campo-auto\b\s*', ' ', out)
    out = re.sub(r'\sclass="\s*"', '', out, flags=re.I)
    out = re.sub(r'<(br)([^>]*)>', r'<\1\2/>', out, flags=re.I)
    out = re.sub(r'<img([^>]*?)(?<!/)>', r'<img\1/>', out, flags=re.I)
    # xhtml2pdf no dibuja bordes de divs vacíos: la línea de firma necesita
    # contenido (&nbsp;) o desaparece del PDF.
    out = re.sub(
        r'(<div[^>]*class="[^"]*prop-doc-firma-linea[^"]*"[^>]*>)\s*(</div>)',
        r'\1&nbsp;\2',
        out,
        flags=re.I,
    )
    out = re.sub(r'<colgroup>.*?</colgroup>', '', out, flags=re.I | re.S)
    out = re.sub(r'\sclass="([^"]*\s)?prop-tabla-resize(\s[^"]*)?"', ' class="prop-tabla"', out, flags=re.I)
    out = re.sub(r'<div class="prop-doc-header">\s*<div class="prop-doc-header-text">', '<table class="prop-doc-header" cellpadding="0" cellspacing="0"><tr><td class="prop-doc-header-text" valign="top">', out, flags=re.I | re.S)
    out = re.sub(r'</div>\s*<div class="prop-doc-logo-wrap"([^>]*)>', r'</td><td class="prop-doc-logo-wrap"\1 valign="top" align="right">', out, flags=re.I)
    out = re.sub(r'</div>\s*</div>\s*<table class="prop-doc-meta"', '</td></tr></table><table class="prop-doc-meta"', out, flags=re.I)
    return out.strip()


def _inyectar_logo_html(html: str, logo_path: str | None) -> str:
    logo_uri = _logo_data_uri(logo_path)
    if not logo_uri:
        return html
    if re.search(r'<img[^>]+class="[^"]*prop-doc-logo', html, flags=re.I):
        html = re.sub(
            r'(<img[^>]*class="[^"]*prop-doc-logo[^"]*"[^>]*src=")([^"]*)(")',
            rf'\1{logo_uri}\3',
            html,
            count=1,
            flags=re.I,
        )
        html = re.sub(r'(<img[^>]*src=")([^"]*)("[^>]*class="[^"]*prop-doc-logo)', rf'\1{logo_uri}\3', html, count=1, flags=re.I)
        return html
    html = re.sub(
        r'(<td[^>]*class="[^"]*prop-doc-logo-wrap[^"]*"[^>]*>)\s*(</td>)',
        rf'\1<img src="{logo_uri}" class="prop-doc-logo" alt="Logo"/>\2',
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'(<div[^>]*class="[^"]*prop-doc-logo-wrap[^"]*"[^>]*>)\s*(</div>)',
        rf'\1<img src="{logo_uri}" class="prop-doc-logo" alt="Logo"/>\2',
        html,
        count=1,
        flags=re.I,
    )
    return html


def _envolver_html_export(contenido: str, titulo: str, logo_path: str | None = None, pdf: bool = False) -> str:
    html = _normalizar_html_para_pdf(contenido)
    html = _inyectar_logo_html(html, logo_path)
    titulo_safe = titulo.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    css = PROP_PDF_CSS if pdf else PROP_DOC_CSS
    footer = ''
    if pdf:
        footer = '<div id="footerContent">Página <pdf:pagenumber> de <pdf:pagecount></div>'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/><title>{titulo_safe}</title>
<style>{css}</style></head><body>{html}{footer}</body></html>"""


def generar_pdf_propuesta(titulo: str, contenido: str, logo_path: str | None = None) -> bytes:
    _registrar_fuentes_pdf()
    doc_html = _envolver_html_export(contenido, titulo, logo_path, pdf=True)
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:
        raise RuntimeError('xhtml2pdf no está instalado en el servidor') from exc

    buf = io.BytesIO()
    status = pisa.CreatePDF(doc_html, dest=buf, encoding='utf-8')
    if status.err:
        raise RuntimeError('Error al renderizar PDF desde HTML')
    pdf = buf.getvalue()
    if not pdf:
        raise RuntimeError('PDF vacío')
    return pdf


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
            'servicio': 'CEV+RT',
            'tarifas': TARIFAS_CEV_RT,
            'etapas': ETAPAS_PAGO_CEV_RT,
            'template': None,
            'format': 'html',
        }
    if servicio == 'CEV':
        return {
            'servicio': 'CEV',
            'tarifas': TARIFAS_CEV,
            'etapas': ETAPAS_PAGO_CEV,
            'template': None,
            'format': 'html',
        }
    if servicio == 'RT':
        return {
            'servicio': 'RT',
            'tarifas': TARIFAS_CEV_RT,
            'etapas': ETAPAS_PAGO_RT,
            'tipos_proyecto': TIPOS_PROYECTO_RT,
            'template': None,
            'format': 'html',
        }
    if servicio == 'CES':
        return {
            'servicio': 'CES',
            'brackets': TARIFAS_CES,
            'niveles': NIVELES_CES,
            'tipos': TIPOS_CES,
            'etapas': ETAPAS_PAGO_CES,
            'arancel_ea': ARANCEL_CES_EA,
            'arancel_ee': ARANCEL_CES_EE,
            'iva': IVA_CES,
            'template': None,
            'format': 'html',
        }
    if servicio == 'CES Evaluadora':
        return {
            'servicio': 'CES Evaluadora',
            'filas': FILAS_CES_EVALUADORA,
            'arancel_ee': ARANCEL_CES_EE,
            'etapas': [],
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
