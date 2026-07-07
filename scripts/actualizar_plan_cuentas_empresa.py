#!/usr/bin/env python3
"""Actualiza el plan de cuentas de una empresa (reemplazo o merge seguro)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from app import app  # noqa: E402
from extensions import db  # noqa: E402
from models import CentroCosto, CuentaContable, Empresa, LineaComprobante  # noqa: E402
from services.plan_cuentas_seed import (  # noqa: E402
    PLAN_CUENTAS_TEMPLATES,
    _sembrar_centro_costo_administracion,
    plan_cuentas_por_template,
    sembrar_plan_cuentas,
)

SAMPLE_CODES = {
    'sociedad_profesionales': ("1.1.01.01", "2.1.01.01", "4.1.01.01", "5.2.01.03"),
    'saas': ("1.1.01.03", "2.1.03.01", "4.1.01.01", "5.1.01.01", "6.3.01.02"),
}


def _find_empresa(empresa_id: int | None, empresa_nombre: str | None) -> Empresa:
    if empresa_id is not None:
        emp = db.session.get(Empresa, empresa_id)
        if emp is None:
            raise SystemExit(f"Empresa no encontrada: id={empresa_id}")
        return emp
    if not empresa_nombre:
        raise SystemExit("Indique --empresa-id o --empresa-nombre")
    needle = empresa_nombre.strip().lower()
    candidatos = Empresa.query.all()
    matches = [
        e for e in candidatos
        if needle in (e.nombre or "").lower()
        or needle.replace("-", " ") in (e.nombre or "").lower().replace("-", " ")
    ]
    if not matches:
        raise SystemExit(f"Ninguna empresa coincide con: {empresa_nombre!r}")
    if len(matches) > 1:
        nombres = ", ".join(f"{e.id}={e.nombre!r}" for e in matches)
        raise SystemExit(f"Varias empresas coinciden ({nombres}); use --empresa-id")
    return matches[0]


def _cuenta_ids_empresa(empresa_id: int) -> list[int]:
    return [
        row[0]
        for row in db.session.query(CuentaContable.id).filter_by(empresa_id=empresa_id).all()
    ]


def _lineas_en_cuentas_empresa(empresa_id: int) -> int:
    ids = _cuenta_ids_empresa(empresa_id)
    if not ids:
        return 0
    return LineaComprobante.query.filter(LineaComprobante.cuenta_contable_id.in_(ids)).count()


def _delete_cuentas_empresa(empresa_id: int) -> int:
    eliminadas = 0
    while True:
        cuentas = CuentaContable.query.filter_by(empresa_id=empresa_id).all()
        if not cuentas:
            break
        ids_con_hijos = {c.id_padre for c in cuentas if c.id_padre is not None}
        hojas = [c for c in cuentas if c.id not in ids_con_hijos]
        if not hojas:
            for c in cuentas:
                c.id_padre = None
            db.session.flush()
            continue
        for c in hojas:
            db.session.delete(c)
            eliminadas += 1
        db.session.flush()
    return eliminadas


def _merge_plan(empresa_id: int, template: str) -> tuple[int, int]:
    """Actualiza/crea cuentas del plan; no elimina cuentas existentes."""
    plan = plan_cuentas_por_template(template)
    existentes = {
        c.codigo: c
        for c in CuentaContable.query.filter_by(empresa_id=empresa_id).all()
    }
    ids_por_codigo: dict[str, int] = {cod: c.id for cod, c in existentes.items()}
    creadas = 0
    actualizadas = 0

    for codigo, nombre, tipo, es_imputable, codigo_padre, clasificacion_sii in plan:
        id_padre = ids_por_codigo.get(codigo_padre) if codigo_padre else None
        cuenta = existentes.get(codigo)
        if cuenta is None:
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
            existentes[codigo] = cuenta
            creadas += 1
        else:
            changed = False
            if cuenta.nombre != nombre:
                cuenta.nombre = nombre
                changed = True
            if cuenta.tipo != tipo:
                cuenta.tipo = tipo
                changed = True
            if cuenta.es_imputable != es_imputable:
                cuenta.es_imputable = es_imputable
                changed = True
            if cuenta.clasificacion_sii != clasificacion_sii:
                cuenta.clasificacion_sii = clasificacion_sii
                changed = True
            if cuenta.id_padre != id_padre:
                cuenta.id_padre = id_padre
                changed = True
            if not cuenta.activa:
                cuenta.activa = True
                changed = True
            if changed:
                actualizadas += 1
            ids_por_codigo[codigo] = cuenta.id

    return creadas, actualizadas


def _sample_accounts(empresa_id: int, template: str) -> list[tuple[str, str, str]]:
    rows = []
    for cod in SAMPLE_CODES.get(template, ()):
        c = CuentaContable.query.filter_by(empresa_id=empresa_id, codigo=cod).first()
        if c:
            rows.append((c.codigo, c.nombre, c.tipo))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Actualizar plan de cuentas por empresa")
    parser.add_argument("--empresa-id", type=int, default=None)
    parser.add_argument("--empresa-nombre", type=str, default=None)
    parser.add_argument(
        "--template",
        choices=sorted(PLAN_CUENTAS_TEMPLATES),
        default=None,
        help="Plantilla a aplicar (default: plan_cuentas_template de la empresa o sociedad_profesionales)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo informar; no modifica BD")
    args = parser.parse_args()

    with app.app_context():
        emp = _find_empresa(args.empresa_id, args.empresa_nombre)
        empresa_id = emp.id
        template = args.template or emp.plan_cuentas_template or 'sociedad_profesionales'
        if template not in PLAN_CUENTAS_TEMPLATES:
            raise SystemExit(f"Plantilla inválida: {template!r}")
        plan = plan_cuentas_por_template(template)

        antes = CuentaContable.query.filter_by(empresa_id=empresa_id).count()
        lineas = _lineas_en_cuentas_empresa(empresa_id)
        admin = CentroCosto.query.filter_by(empresa_id=empresa_id, codigo="ADMIN").first()

        print(f"Empresa: id={empresa_id} nombre={emp.nombre!r}")
        print(f"Plantilla: {template}")
        print(f"Cuentas contables antes: {antes}")
        print(f"Líneas comprobante en cuentas de la empresa: {lineas}")
        print(f"Centro ADMIN existe: {admin is not None}")

        if args.dry_run:
            modo = "merge" if lineas else "reemplazo (delete + seed)"
            print(f"Modo que se aplicaría: {modo}")
            print(f"Cuentas esperadas tras actualización: {len(plan)}")
            return

        if lineas == 0:
            if antes:
                borradas = _delete_cuentas_empresa(empresa_id)
                print(f"Cuentas eliminadas: {borradas}")
            creadas = sembrar_plan_cuentas(empresa_id, template)
            print(f"Cuentas creadas por seed ({template}): {creadas}")
            emp.plan_cuentas_template = template
        else:
            creadas, actualizadas = _merge_plan(empresa_id, template)
            _sembrar_centro_costo_administracion(empresa_id)
            print(f"Merge: creadas={creadas} actualizadas={actualizadas}")

        db.session.commit()

        despues = CuentaContable.query.filter_by(empresa_id=empresa_id).count()
        admin_despues = CentroCosto.query.filter_by(empresa_id=empresa_id, codigo="ADMIN").first()
        print(f"Cuentas contables después: {despues}")
        print(f"Centro ADMIN después: {admin_despues is not None}")
        if admin_despues:
            print(f"  ADMIN: {admin_despues.codigo!r} {admin_despues.nombre!r}")

        print("Muestra de códigos:")
        for cod, nom, tipo in _sample_accounts(empresa_id, template):
            print(f"  {cod} | {nom} | {tipo}")

        codigos_plan = {c[0] for c in plan}
        faltantes = sorted(codigos_plan - {
            c.codigo for c in CuentaContable.query.filter_by(empresa_id=empresa_id).all()
        })
        if faltantes:
            print(f"ADVERTENCIA: faltan códigos del plan: {faltantes}")
        elif despues >= len(plan):
            print(f"OK: plan completo ({len(plan)} cuentas definidas; total en BD={despues})")


if __name__ == "__main__":
    main()
