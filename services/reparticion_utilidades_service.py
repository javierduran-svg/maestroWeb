"""Repartición de utilidades (profit sharing) por sueldo base y desempeño."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime

from sqlalchemy import or_

from extensions import db
from models import (
    CentroCosto,
    EntregaProgramada,
    Proyecto,
    RegistroTiempo,
    TareaEntrega,
    Trabajador,
)

HORAS_MES_TRABAJABLE = 160
ESTADOS_REGISTRO_TIEMPO_CUENTA = ('activo', 'pausado', 'finalizado')

# TODO: TareaEntrega.fecha_cierre — cuando exista, validar fecha_cierre <= fecha_limite
#       para tareas con status='Hecho'. Mientras no exista el campo, Hecho cuenta como cumplida.


def _normalizar_texto(texto: str) -> str:
    if not texto:
        return ''
    nfkd = unicodedata.normalize('NFKD', texto.strip().casefold())
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _nombre_trabajador(trabajador: Trabajador) -> str:
    partes = [trabajador.nombres, trabajador.apellido_paterno]
    if trabajador.apellido_materno:
        partes.append(trabajador.apellido_materno)
    return ' '.join(partes)


def _duracion_efectiva_segundos(reg: RegistroTiempo, ahora: datetime | None = None) -> int:
    base = int(reg.duracion_segundos or 0)
    if reg.estado == 'activo' and reg.ultimo_inicio:
        ref = ahora or datetime.utcnow()
        base += max(0, int((ref - reg.ultimo_inicio).total_seconds()))
    return base


def _meses_calendario_inclusivos(fecha_inicio: date, fecha_fin: date) -> int:
    if fecha_fin < fecha_inicio:
        return 0
    return (fecha_fin.year - fecha_inicio.year) * 12 + (fecha_fin.month - fecha_inicio.month) + 1


def _horas_trabajables_periodo(fecha_inicio: date, fecha_fin: date) -> float:
    """Horas teóricas del periodo: meses calendario inclusivos × 160 h/mes."""
    return float(_meses_calendario_inclusivos(fecha_inicio, fecha_fin) * HORAS_MES_TRABAJABLE)


def _nombres_administracion(empresa_id: int) -> set[str]:
    """Nombres normalizados del centro ADMIN / Administración de la empresa."""
    centros = CentroCosto.query.filter(
        CentroCosto.empresa_id == empresa_id,
        or_(
            CentroCosto.codigo == 'ADMIN',
            CentroCosto.nombre.ilike('%administraci%'),
        ),
    ).all()
    nombres = {_normalizar_texto('administracion')}
    for centro in centros:
        nombres.add(_normalizar_texto(centro.nombre))
        nombres.add(_normalizar_texto(centro.codigo))
    return nombres


def _es_proyecto_administracion(proyecto: Proyecto, admin_nombres: set[str]) -> bool:
    return _normalizar_texto(proyecto.nombre) in admin_nombres


def _proyecto_ids_para_utilizacion(empresa_id: int) -> set[int]:
    """Proyectos activos cuyo tiempo cuenta para utilización (excluye Administración)."""
    admin_nombres = _nombres_administracion(empresa_id)
    proyectos = Proyecto.query.filter_by(empresa_id=empresa_id, status='Activo').all()
    return {
        p.id for p in proyectos
        if not _es_proyecto_administracion(p, admin_nombres)
    }


def _tasa_utilizacion(
    empresa_id: int,
    trabajador_id: int,
    fecha_inicio: date,
    fecha_fin: date,
) -> float:
    """Horas en proyectos activos (sin Administración) / horas trabajables del periodo."""
    horas_trabajables = _horas_trabajables_periodo(fecha_inicio, fecha_fin)
    if horas_trabajables <= 0:
        return 0.0

    proyecto_ids = _proyecto_ids_para_utilizacion(empresa_id)
    if not proyecto_ids:
        return 0.0

    inicio_dt = datetime.combine(fecha_inicio, datetime.min.time())
    fin_dt = datetime.combine(fecha_fin, datetime.max.time())
    registros = RegistroTiempo.query.filter(
        RegistroTiempo.empresa_id == empresa_id,
        RegistroTiempo.trabajador_id == trabajador_id,
        RegistroTiempo.proyecto_id.in_(proyecto_ids),
        RegistroTiempo.estado.in_(ESTADOS_REGISTRO_TIEMPO_CUENTA),
        RegistroTiempo.inicio >= inicio_dt,
        RegistroTiempo.inicio <= fin_dt,
    ).all()

    if not registros:
        return 0.0

    ahora = datetime.utcnow()
    segundos = sum(_duracion_efectiva_segundos(r, ahora) for r in registros)
    horas = segundos / 3600.0
    tasa = (horas / horas_trabajables) * 100.0
    return round(min(100.0, max(0.0, tasa)), 2)


def _tareas_trabajador_en_periodo(
    empresa_id: int,
    trabajador_id: int,
    fecha_inicio: date,
    fecha_fin: date,
) -> list[TareaEntrega]:
    return (
        db.session.query(TareaEntrega)
        .join(EntregaProgramada, TareaEntrega.entrega_id == EntregaProgramada.id)
        .filter(
            TareaEntrega.empresa_id == empresa_id,
            TareaEntrega.asignado_id == trabajador_id,
            or_(
                TareaEntrega.fecha_limite.between(fecha_inicio, fecha_fin),
                (
                    TareaEntrega.fecha_limite.is_(None)
                    & EntregaProgramada.fecha_entrega.between(fecha_inicio, fecha_fin)
                ),
            ),
        )
        .all()
    )


def _tarea_cumple_plazo(tarea: TareaEntrega) -> bool:
    if tarea.status != 'Hecho':
        return False
    # Sin fecha_cierre en el modelo: tarea Hecho se considera cumplida.
    # TODO: cuando exista fecha_cierre, exigir fecha_cierre <= fecha_limite.
    if tarea.fecha_limite is None:
        return True
    return True


def _tasa_cumplimiento(
    empresa_id: int,
    trabajador_id: int,
    fecha_inicio: date,
    fecha_fin: date,
) -> float:
    """% de tareas asignadas cumplidas a plazo en el periodo.

    Sin tareas asignadas en el periodo → 0 % (no se premia ni penaliza con 100 %).
    """
    tareas = _tareas_trabajador_en_periodo(empresa_id, trabajador_id, fecha_inicio, fecha_fin)
    if not tareas:
        return 0.0

    cumplidas = sum(1 for t in tareas if _tarea_cumple_plazo(t))
    return round((cumplidas / len(tareas)) * 100.0, 2)


def calcular_score_desempeno(
    trabajador_id: int,
    fecha_inicio: date,
    fecha_fin: date,
    empresa_id: int | None = None,
) -> dict:
    """Calcula score de desempeño 0-100 (promedio 50/50 utilización y cumplimiento)."""
    trabajador = Trabajador.query.get(trabajador_id)
    if not trabajador:
        return {
            'score': 0.0,
            'tasa_utilizacion': 0.0,
            'tasa_cumplimiento': 0.0,
        }

    eid = empresa_id if empresa_id is not None else trabajador.empresa_id
    if trabajador.empresa_id != eid:
        return {
            'score': 0.0,
            'tasa_utilizacion': 0.0,
            'tasa_cumplimiento': 0.0,
        }

    tasa_util = _tasa_utilizacion(eid, trabajador_id, fecha_inicio, fecha_fin)
    tasa_cumpl = _tasa_cumplimiento(eid, trabajador_id, fecha_inicio, fecha_fin)
    score = round(min(100.0, max(0.0, (tasa_util + tasa_cumpl) / 2.0)), 2)

    return {
        'score': score,
        'tasa_utilizacion': tasa_util,
        'tasa_cumplimiento': tasa_cumpl,
    }


def _trabajadores_activos_semestre(
    empresa_id: int,
    semestre_inicio: date,
    semestre_fin: date,
) -> list[Trabajador]:
    """Trabajadores de la empresa vigentes al cierre del semestre.

    El modelo Trabajador no tiene campo ``activo`` ni ``fecha_egreso``;
    se consideran activos quienes ingresaron en o antes de ``semestre_fin``.
    """
    return (
        Trabajador.query.filter(
            Trabajador.empresa_id == empresa_id,
            Trabajador.fecha_ingreso <= semestre_fin,
        )
        .order_by(Trabajador.apellido_paterno, Trabajador.nombres)
        .all()
    )


def _distribuir_proporcional(monto_total: float, pesos: list[float]) -> list[float]:
    """Reparte monto_total según pesos; si la suma es 0, reparte en partes iguales."""
    n = len(pesos)
    if n == 0:
        return []
    if monto_total <= 0:
        return [0.0] * n

    suma = sum(pesos)
    if suma <= 0:
        parte = round(monto_total / n, 2)
        montos = [parte] * n
        montos[-1] = round(monto_total - sum(montos[:-1]), 2)
        return montos

    montos = [round((p / suma) * monto_total, 2) for p in pesos]
    montos[-1] = round(monto_total - sum(montos[:-1]), 2)
    return montos


def simular_reparticion_utilidades(
    pozo_total: float,
    semestre_inicio: date,
    semestre_fin: date,
    empresa_id: int,
    porcentaje_base: float = 60.0,
    porcentaje_desempeno: float = 40.0,
) -> dict:
    """Simula reparto del pozo entre trabajadores activos del semestre."""
    pozo_total = float(pozo_total or 0)
    pozo_base = round(pozo_total * (porcentaje_base / 100.0), 2)
    pozo_desempeno = round(pozo_total * (porcentaje_desempeno / 100.0), 2)

    trabajadores = _trabajadores_activos_semestre(empresa_id, semestre_inicio, semestre_fin)
    if not trabajadores:
        return {
            'empresa_id': empresa_id,
            'semestre_inicio': semestre_inicio.isoformat(),
            'semestre_fin': semestre_fin.isoformat(),
            'pozo_total': pozo_total,
            'porcentaje_base': porcentaje_base,
            'porcentaje_desempeno': porcentaje_desempeno,
            'pozo_base': pozo_base,
            'pozo_desempeno': pozo_desempeno,
            'trabajadores': [],
        }

    sueldos = [max(0.0, float(t.sueldo_base or 0)) for t in trabajadores]
    metricas = [
        calcular_score_desempeno(t.id, semestre_inicio, semestre_fin, empresa_id=empresa_id)
        for t in trabajadores
    ]
    scores = [m['score'] for m in metricas]

    bonos_base = _distribuir_proporcional(pozo_base, sueldos)
    bonos_desempeno = _distribuir_proporcional(pozo_desempeno, scores)

    resultado_trabajadores = []
    for idx, trabajador in enumerate(trabajadores):
        bono_base = bonos_base[idx]
        bono_desempeno = bonos_desempeno[idx]
        resultado_trabajadores.append({
            'trabajador_id': trabajador.id,
            'nombre': _nombre_trabajador(trabajador),
            'bono_base': bono_base,
            'bono_desempeno': bono_desempeno,
            'bono_total': round(bono_base + bono_desempeno, 2),
            'score': metricas[idx]['score'],
            'tasa_utilizacion': metricas[idx]['tasa_utilizacion'],
            'tasa_cumplimiento': metricas[idx]['tasa_cumplimiento'],
        })

    return {
        'empresa_id': empresa_id,
        'semestre_inicio': semestre_inicio.isoformat(),
        'semestre_fin': semestre_fin.isoformat(),
        'pozo_total': pozo_total,
        'porcentaje_base': porcentaje_base,
        'porcentaje_desempeno': porcentaje_desempeno,
        'pozo_base': pozo_base,
        'pozo_desempeno': pozo_desempeno,
        'trabajadores': resultado_trabajadores,
    }
