"""Cálculo de rentabilidad por proyecto (ingresos, gastos, costo HH)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from extensions import db
from models import EntregaProgramada, Movimiento, Proyecto, RegistroTiempo, Trabajador

ESTADOS_REGISTRO_TIEMPO_CUENTA = ('activo', 'pausado', 'finalizado')


def _duracion_efectiva_segundos(reg: RegistroTiempo, ahora: datetime | None = None) -> int:
    base = int(reg.duracion_segundos or 0)
    if reg.estado == 'activo' and reg.ultimo_inicio:
        ref = ahora or datetime.utcnow()
        base += max(0, int((ref - reg.ultimo_inicio).total_seconds()))
    return base


def _sumar_movimientos_proyecto(
    empresa_id: int,
    proyecto_id: int,
    clase: str,
    *,
    status_pago: str | None = None,
) -> float:
    query = (
        db.session.query(func.coalesce(func.sum(Movimiento.monto_pesos), 0.0))
        .filter(
            Movimiento.empresa_id == empresa_id,
            Movimiento.proyecto_id == proyecto_id,
            Movimiento.clase == clase,
            Movimiento.estado == 'Activo',
        )
    )
    if status_pago is not None:
        query = query.filter(Movimiento.status_pago == status_pago)
    total = query.scalar()
    return float(total or 0.0)


def _costo_hh_proyecto(empresa_id: int, proyecto_id: int) -> float:
    ahora = datetime.utcnow()
    registros = RegistroTiempo.query.filter(
        RegistroTiempo.empresa_id == empresa_id,
        RegistroTiempo.proyecto_id == proyecto_id,
        RegistroTiempo.estado.in_(ESTADOS_REGISTRO_TIEMPO_CUENTA),
    ).all()
    if not registros:
        return 0.0

    trabajador_ids = {r.trabajador_id for r in registros}
    trabajadores = {
        t.id: t
        for t in Trabajador.query.filter(
            Trabajador.empresa_id == empresa_id,
            Trabajador.id.in_(trabajador_ids),
        ).all()
    }

    total = 0.0
    for reg in registros:
        trab = trabajadores.get(reg.trabajador_id)
        if not trab:
            continue
        horas = _duracion_efectiva_segundos(reg, ahora) / 3600.0
        total += horas * trab.costo_hh_real
    return round(total, 2)


def _avance_entregas_pct(empresa_id: int, proyecto_id: int) -> float | None:
    total = EntregaProgramada.query.filter_by(
        empresa_id=empresa_id,
        proyecto_id=proyecto_id,
    ).count()
    if total == 0:
        return None
    hechas = EntregaProgramada.query.filter_by(
        empresa_id=empresa_id,
        proyecto_id=proyecto_id,
        status='Hecho',
    ).count()
    return round((hechas / total) * 100, 1)


def calcular_rentabilidad_proyectos(empresa_id: int) -> list[dict]:
    """Rentabilidad por proyecto activo de la empresa."""
    proyectos = (
        Proyecto.query.filter_by(empresa_id=empresa_id, status='Activo')
        .order_by(Proyecto.nombre)
        .all()
    )
    resultado: list[dict] = []
    for p in proyectos:
        ingresos = _sumar_movimientos_proyecto(
            empresa_id, p.id, 'estado_pago', status_pago='Pagado',
        )
        gastos_directos = _sumar_movimientos_proyecto(empresa_id, p.id, 'gasto')
        costo_hh = _costo_hh_proyecto(empresa_id, p.id)
        margen_bruto = round(ingresos - gastos_directos - costo_hh, 2)
        if ingresos > 0:
            rentabilidad_pct = round((margen_bruto / ingresos) * 100, 1)
        else:
            rentabilidad_pct = None
        avance_entregas_pct = _avance_entregas_pct(empresa_id, p.id)

        resultado.append({
            'proyecto_id': p.id,
            'proyecto': p.nombre,
            'cliente': p.cliente_rel.razon_social if p.cliente_rel else '',
            'ingresos': round(ingresos, 2),
            'gastos_directos': round(gastos_directos, 2),
            'costo_hh': costo_hh,
            'margen_bruto': margen_bruto,
            'rentabilidad_pct': rentabilidad_pct,
            'avance_entregas_pct': avance_entregas_pct,
        })
    return resultado
