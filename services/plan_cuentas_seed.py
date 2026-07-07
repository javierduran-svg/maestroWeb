"""Semillas de plan de cuentas por empresa (Sociedad de Profesionales / SaaS)."""

from __future__ import annotations

from extensions import db
from models import CentroCosto, CuentaContable, Empresa

_CUENTA_SPEC = tuple[str, str, str, bool, str | None, str | None]

PLAN_CUENTAS_TEMPLATES = frozenset({'sociedad_profesionales', 'saas'})

# (codigo, nombre, tipo, es_imputable, codigo_padre, clasificacion_sii)
_PLAN_CUENTAS_SII: list[_CUENTA_SPEC] = [
    # 1 Activos
    ('1', 'Activos', 'Activo', False, None, None),
    ('1.1', 'Activos Circulantes', 'Activo', False, '1', None),
    ('1.1.01.01', 'Banco (CLP)', 'Activo', True, '1.1', None),
    ('1.1.01.02', 'Banco (USD)', 'Activo', True, '1.1', None),
    ('1.1.02.01', 'Clientes Nacionales', 'Activo', True, '1.1', None),
    ('1.1.03.01', 'PPM por Recuperar', 'Activo', True, '1.1', None),
    # 2 Pasivos
    ('2', 'Pasivos', 'Pasivo', False, None, None),
    ('2.1', 'Pasivos Circulantes', 'Pasivo', False, '2', None),
    ('2.1.01.01', 'Proveedores', 'Pasivo', True, '2.1', None),
    ('2.1.02.01', 'Honorarios por Pagar', 'Pasivo', True, '2.1', None),
    ('2.1.03.01', 'Remuneraciones por Pagar', 'Pasivo', True, '2.1', None),
    ('2.1.03.02', 'Leyes Sociales por Pagar (Previred)', 'Pasivo', True, '2.1', None),
    ('2.1.04.01', 'Impuestos por Pagar F29 (Retenciones, IVA)', 'Pasivo', True, '2.1', None),
    # 3 Patrimonio
    ('3', 'Patrimonio', 'Patrimonio', False, None, None),
    ('3.1', 'Capital y Retiros', 'Patrimonio', False, '3', None),
    ('3.1.01.01', 'Capital Social', 'Patrimonio', True, '3.1', None),
    ('3.1.02.01', 'Utilidades Acumuladas', 'Patrimonio', True, '3.1', None),
    ('3.1.03.01', 'Cuenta Particular Socios (Retiros)', 'Patrimonio', True, '3.1', None),
    # 4 Ingresos
    ('4', 'Ingresos', 'Ingreso', False, None, None),
    ('4.1', 'Ingresos Operacionales', 'Ingreso', False, '4', None),
    ('4.1.01.01', 'Ingresos por Servicios de Consultoría', 'Ingreso', True, '4.1', None),
    # 5 Egresos
    ('5', 'Egresos', 'Egreso', False, None, None),
    ('5.1', 'Personal y Subcontratos', 'Egreso', False, '5', None),
    ('5.1.01.01', 'Sueldos y Salarios', 'Egreso', True, '5.1', None),
    ('5.1.02.01', 'Honorarios Profesionales', 'Egreso', True, '5.1', None),
    ('5.1.02.02', 'Sueldo Empresarial', 'Egreso', True, '5.1', None),
    ('5.2', 'Administración', 'Egreso', False, '5', None),
    ('5.2.01.01', 'Arriendos', 'Egreso', True, '5.2', None),
    ('5.2.01.02', 'Software y Licencias', 'Egreso', True, '5.2', None),
    ('5.2.01.03', 'Gastos Bancarios', 'Egreso', True, '5.2', None),
]

_PLAN_CUENTAS_SAAS: list[_CUENTA_SPEC] = [
    # 1 Activos
    ('1', 'Activos', 'Activo', False, None, None),
    ('1.1', 'Activos Circulantes', 'Activo', False, '1', None),
    ('1.1.01.01', 'Banco (CLP)', 'Activo', True, '1.1', None),
    ('1.1.01.02', 'Banco (USD)', 'Activo', True, '1.1', None),
    ('1.1.01.03', 'Fondos en Tránsito (Stripe/Fintoc)', 'Activo', True, '1.1', None),
    ('1.1.02.01', 'Clientes por Cobrar', 'Activo', True, '1.1', None),
    ('1.2', 'Activos No Circulantes', 'Activo', False, '1', None),
    ('1.2.01.01', 'Software Propio (Intangibles)', 'Activo', True, '1.2', None),
    # 2 Pasivos
    ('2', 'Pasivos', 'Pasivo', False, None, None),
    ('2.1', 'Pasivos Circulantes', 'Pasivo', False, '2', None),
    ('2.1.01.01', 'Proveedores Internacionales', 'Pasivo', True, '2.1', None),
    ('2.1.02.01', 'Impuestos por Pagar (F29)', 'Pasivo', True, '2.1', None),
    ('2.1.03.01', 'Ingresos Diferidos (Suscripciones Anticipadas)', 'Pasivo', True, '2.1', None),
    # 3 Patrimonio
    ('3', 'Patrimonio', 'Patrimonio', False, None, None),
    ('3.1', 'Capital', 'Patrimonio', False, '3', None),
    ('3.1.01.01', 'Capital Social', 'Patrimonio', True, '3.1', None),
    ('3.1.02.01', 'Utilidades Acumuladas', 'Patrimonio', True, '3.1', None),
    # 4 Ingresos
    ('4', 'Ingresos', 'Ingreso', False, None, None),
    ('4.1', 'Ingresos Operacionales', 'Ingreso', False, '4', None),
    ('4.1.01.01', 'Ingresos por Suscripciones SaaS', 'Ingreso', True, '4.1', None),
    ('4.1.01.02', 'Ingresos por Implementación / Setup', 'Ingreso', True, '4.1', None),
    # 5 Costos Directos (COGS)
    ('5', 'Costos Directos (COGS)', 'Costo', False, None, None),
    ('5.1', 'Costos de Infraestructura', 'Costo', False, '5', None),
    ('5.1.01.01', 'Hosting y Cloud (AWS/Hetzner)', 'Costo', True, '5.1', None),
    ('5.1.01.02', 'Consumo de APIs y Pasarelas de Pago', 'Costo', True, '5.1', None),
    # 6 Gastos Operativos (OPEX)
    ('6', 'Gastos Operativos (OPEX)', 'Egreso', False, None, None),
    ('6.1', 'Investigación y Desarrollo (R&D)', 'Egreso', False, '6', None),
    ('6.1.01.01', 'Sueldos Equipo Desarrollo', 'Egreso', True, '6.1', None),
    ('6.1.01.02', 'Software de Desarrollo', 'Egreso', True, '6.1', None),
    ('6.2', 'Ventas y Marketing (S&M)', 'Egreso', False, '6', None),
    ('6.2.01.01', 'Publicidad y Marketing', 'Egreso', True, '6.2', None),
    ('6.2.01.02', 'Sueldos y Comisiones Ventas', 'Egreso', True, '6.2', None),
    ('6.3', 'Administración (G&A)', 'Egreso', False, '6', None),
    ('6.3.01.01', 'Sueldos Administración', 'Egreso', True, '6.3', None),
    ('6.3.01.02', 'Asesorías Legales y Contables', 'Egreso', True, '6.3', None),
]

_PLANES: dict[str, list[_CUENTA_SPEC]] = {
    'sociedad_profesionales': _PLAN_CUENTAS_SII,
    'saas': _PLAN_CUENTAS_SAAS,
}


def plan_cuentas_por_template(template: str) -> list[_CUENTA_SPEC]:
    if template not in PLAN_CUENTAS_TEMPLATES:
        raise ValueError(
            f'Plantilla inválida: {template!r}; use uno de: {", ".join(sorted(PLAN_CUENTAS_TEMPLATES))}',
        )
    return _PLANES[template]


def _sembrar_centro_costo_administracion(empresa_id: int) -> None:
    if CentroCosto.query.filter_by(empresa_id=empresa_id, codigo='ADMIN').first():
        return
    db.session.add(CentroCosto(
        empresa_id=empresa_id,
        codigo='ADMIN',
        nombre='Administración',
        activo=True,
    ))


def sembrar_plan_cuentas(empresa_id: int, template: str = 'sociedad_profesionales') -> int:
    """
    Siembra plan de cuentas según plantilla y centro de costo ADMIN.

    Idempotente: si ya existen cuentas contables para la empresa, no crea cuentas y retorna 0.
    El centro de costo ADMIN se siembra solo si no existe ese código.
    Retorna la cantidad de cuentas creadas.
    """
    if db.session.get(Empresa, empresa_id) is None:
        raise ValueError(f'Empresa no encontrada: id={empresa_id}')

    plan = plan_cuentas_por_template(template)
    creadas = 0
    if not CuentaContable.query.filter_by(empresa_id=empresa_id).first():
        ids_por_codigo: dict[str, int] = {}
        for codigo, nombre, tipo, es_imputable, codigo_padre, clasificacion_sii in plan:
            id_padre = ids_por_codigo.get(codigo_padre) if codigo_padre else None
            cuenta = CuentaContable(
                empresa_id=empresa_id,
                codigo=codigo,
                nombre=nombre,
                tipo=tipo,
                clasificacion_sii=clasificacion_sii,
                id_padre=id_padre,
                es_imputable=es_imputable,
                activa=True,
            )
            db.session.add(cuenta)
            db.session.flush()
            ids_por_codigo[codigo] = cuenta.id
            creadas += 1

    _sembrar_centro_costo_administracion(empresa_id)
    return creadas


def _sembrar_plan_cuentas_sii(empresa_id: int) -> int:
    """Siembra plan Sociedad de Profesionales (SII)."""
    return sembrar_plan_cuentas(empresa_id, 'sociedad_profesionales')


def _sembrar_plan_cuentas_saas(empresa_id: int) -> int:
    """Siembra plan SaaS."""
    return sembrar_plan_cuentas(empresa_id, 'saas')


def _sembrar_contabilidad_basica(empresa_id: int) -> int:
    """Alias retrocompatible de _sembrar_plan_cuentas_sii."""
    return _sembrar_plan_cuentas_sii(empresa_id)


def sembrar_plan_cuentas_sii(empresa_id: int) -> int:
    """Siembra plan de cuentas SII (Sociedad de Profesionales) por empresa."""
    return _sembrar_plan_cuentas_sii(empresa_id)
