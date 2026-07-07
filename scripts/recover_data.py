"""Full data recovery: Excel import + row count report."""
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from sqlalchemy import create_engine, text
from bootstrap import get_database_url
from importar_excel import (
    DEFAULT_XLSX,
    importar_desde_excel,
    importar_trabajadores_desde_excel,
    importar_propuestas_desde_excel,
)

EMPRESA_ID = 1
TABLES = [
    "empresas", "valores_uf", "clientes", "cuentas", "cuentas_contables",
    "centros_costo", "proyectos", "propuestas", "trabajadores", "movimientos",
    "liquidaciones", "entregas_programadas", "tareas_entrega",
    "empresa_sii_config", "empresa_banco_conexiones", "comprobantes", "lineas_comprobante",
]


def count_all(engine):
    counts = {}
    with engine.connect() as conn:
        for t in TABLES:
            counts[t] = conn.execute(text(f'SELECT COUNT(1) FROM "{t}"')).scalar_one()
    return counts


def main():
    engine = create_engine(get_database_url())
    before = count_all(engine)
    print("=== BEFORE ===")
    for t in TABLES:
        print(f"  {t}: {before[t]}")

    print(f"\nExcel: {DEFAULT_XLSX} (exists={DEFAULT_XLSX.exists()})")

    print("\n--- importar_desde_excel (clientes, proyectos, movimientos) ---")
    stats_fin = importar_desde_excel(DEFAULT_XLSX, reset=True, empresa_id=EMPRESA_ID)
    print(stats_fin)

    print("\n--- importar_trabajadores_desde_excel (RRHH) ---")
    stats_rrhh = importar_trabajadores_desde_excel(
        DEFAULT_XLSX, actualizar=True, empresa_id=EMPRESA_ID,
    )
    print(stats_rrhh)

    print("\n--- importar_propuestas_desde_excel ---")
    stats_prop = importar_propuestas_desde_excel(
        DEFAULT_XLSX, actualizar=True, empresa_id=EMPRESA_ID,
    )
    print(stats_prop)

    after = count_all(engine)
    print("\n=== AFTER ===")
    for t in TABLES:
        delta = after[t] - before[t]
        sign = f"+{delta}" if delta > 0 else str(delta)
        print(f"  {t}: {after[t]} ({sign})")

    print("\n=== IMPORT STATS ===")
    print(f"  finanzas: {stats_fin}")
    print(f"  rrhh: {stats_rrhh}")
    print(f"  propuestas: {stats_prop}")


if __name__ == "__main__":
    main()
