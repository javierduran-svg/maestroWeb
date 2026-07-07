#!/usr/bin/env python3
"""
Migrate all application data from SQLite (gestion_proyectos.db) to PostgreSQL.

The SQLite file is read-only and is never modified or deleted.

Prerequisites:
    docker compose up -d
    flask db upgrade
    python scripts/migrate_sqlite_to_postgres.py

Environment:
    DATABASE_URL (or SQLALCHEMY_DATABASE_URI) must point to PostgreSQL.
    SECRET_ENCRYPTION_KEY — if set, SII/bank credential columns are encrypted
    on insert; otherwise stored with plain: prefix (EncryptedString legacy mode).

Options:
    --sqlite-path PATH   SQLite source file (default: gestion_proyectos.db in project root)
    --dry-run            Print row counts only; no writes to PostgreSQL
    --no-truncate        Skip TRUNCATE; merge/upsert is NOT supported — use only for debugging
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

sys.path.insert(0, str(PROJECT_ROOT))

from bootstrap import get_database_url  # noqa: E402
from security import encrypt_value  # noqa: E402

SQLITE_DEFAULT = PROJECT_ROOT / "gestion_proyectos.db"

# Insert order respects foreign keys (session_replication_role also used as safety net).
MIGRATION_TABLES: list[str] = [
    "empresas",
    "valores_uf",
    "clientes",
    "cuentas",
    "cuentas_contables",
    "centros_costo",
    "proyectos",
    "propuestas",
    "trabajadores",
    "movimientos",
    "liquidaciones",
    "entregas_programadas",
    "tareas_entrega",
    "empresa_sii_config",
    "empresa_banco_conexiones",
    "comprobantes",
    "lineas_comprobante",
]

ENCRYPTED_COLUMNS: dict[str, set[str]] = {
    "empresa_sii_config": {"api_key", "password", "certificado_password"},
    "empresa_banco_conexiones": {"fintoc_api_key", "fintoc_link_token"},
}


def _require_postgres_url() -> str:
    url = get_database_url()
    if not url.startswith("postgresql"):
        print(
            "ERROR: DATABASE_URL must point to PostgreSQL.\n"
            "Set DATABASE_URL=postgresql://maestroweb:maestroweb@localhost:5432/maestroweb in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    return url


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return [row[1] for row in cur.fetchall()]


def _pg_columns(engine, table: str) -> list[str]:
    insp = inspect(engine)
    if not insp.has_table(table):
        return []
    return [c["name"] for c in insp.get_columns(table)]


def _pg_boolean_columns(engine, table: str) -> set[str]:
    insp = inspect(engine)
    if not insp.has_table(table):
        return set()
    return {
        c["name"]
        for c in insp.get_columns(table)
        if str(c["type"]).upper().startswith("BOOL") or "BOOLEAN" in str(c["type"]).upper()
    }


def _count_sqlite(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(1) FROM "{table}"').fetchone()[0]


def _count_pg(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(1) FROM "{table}"')).scalar_one()


def _transform_row(table: str, row: dict, bool_cols: set[str]) -> dict:
    encrypted = ENCRYPTED_COLUMNS.get(table, set())
    out: dict = {}
    for key, value in row.items():
        if key in encrypted and value is not None and value != "":
            out[key] = encrypt_value(str(value))
        elif key in bool_cols and value is not None:
            out[key] = bool(value)
        else:
            out[key] = value
    return out


def _fetch_sqlite_rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[dict]:
    cols_sql = ", ".join(f'"{c}"' for c in columns)
    cur = conn.execute(f'SELECT {cols_sql} FROM "{table}"')
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _truncate_postgres(engine) -> None:
    tables = ", ".join(f'"{t}"' for t in reversed(MIGRATION_TABLES))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


def _insert_rows(conn, table: str, columns: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    cols_sql = ", ".join(f'"{c}"' for c in columns)
    params_sql = ", ".join(f":{c}" for c in columns)
    stmt = text(f'INSERT INTO "{table}" ({cols_sql}) VALUES ({params_sql})')
    conn.execute(stmt, rows)
    return len(rows)


def _reset_sequences(engine) -> None:
    with engine.begin() as conn:
        for table in MIGRATION_TABLES:
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:tbl, 'id')"),
                {"tbl": table},
            ).scalar_one_or_none()
            if not seq:
                continue
            conn.execute(
                text(
                    f"SELECT setval(:seq, COALESCE((SELECT MAX(id) FROM \"{table}\"), 1), true)"
                ),
                {"seq": seq},
            )


def _list_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def migrate(sqlite_path: Path, dry_run: bool = False, truncate: bool = True) -> dict[str, dict[str, int]]:
    if not sqlite_path.is_file():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        sys.exit(1)

    pg_url = _require_postgres_url()
    sqlite_conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    sqlite_conn.row_factory = sqlite3.Row
    pg_engine = create_engine(pg_url, future=True)

    sqlite_tables = set(_list_sqlite_tables(sqlite_conn))
    app_tables = [t for t in MIGRATION_TABLES if t in sqlite_tables]

    print(f"SQLite: {sqlite_path}")
    print(f"PostgreSQL: {pg_url.split('@')[-1] if '@' in pg_url else pg_url}")
    print()

    counts: dict[str, dict[str, int]] = {"sqlite": {}, "postgres_before": {}, "postgres_after": {}}

    print("=== SQLite row counts ===")
    for table in app_tables:
        n = _count_sqlite(sqlite_conn, table)
        counts["sqlite"][table] = n
        print(f"  {table}: {n}")

    extra = sqlite_tables - set(MIGRATION_TABLES) - {"alembic_version"}
    if extra:
        print(f"\n  (skipped non-app tables: {', '.join(sorted(extra))})")

    if dry_run:
        print("\nDry run — no changes written to PostgreSQL.")
        sqlite_conn.close()
        pg_engine.dispose()
        return counts

    try:
        with pg_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"ERROR: Cannot connect to PostgreSQL: {exc}", file=sys.stderr)
        sys.exit(1)

    missing_pg = [t for t in app_tables if not inspect(pg_engine).has_table(t)]
    if missing_pg:
        print(
            f"ERROR: PostgreSQL schema missing tables: {', '.join(missing_pg)}\n"
            "Run `flask db upgrade` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("\n=== PostgreSQL row counts (before) ===")
    for table in app_tables:
        n = _count_pg(pg_engine, table)
        counts["postgres_before"][table] = n
        print(f"  {table}: {n}")

    if truncate:
        print("\nTruncating PostgreSQL application tables (keeping alembic_version)...")
        _truncate_postgres(pg_engine)

    print("\nMigrating data...")
    migrated: dict[str, int] = {}

    with pg_engine.begin() as conn:
        conn.execute(text("SET session_replication_role = 'replica'"))
        try:
            for table in app_tables:
                sqlite_cols = _sqlite_columns(sqlite_conn, table)
                pg_cols = _pg_columns(pg_engine, table)
                columns = [c for c in sqlite_cols if c in pg_cols]
                skipped = set(sqlite_cols) - set(columns)
                if skipped:
                    print(f"  {table}: ignoring SQLite-only columns {sorted(skipped)}")

                rows_raw = _fetch_sqlite_rows(sqlite_conn, table, columns)
                bool_cols = _pg_boolean_columns(pg_engine, table)
                rows = [_transform_row(table, r, bool_cols) for r in rows_raw]

                if table == "cuentas_contables" and rows:
                    rows.sort(key=lambda r: (r.get("id_padre") is not None, r.get("id") or 0))

                n = _insert_rows(conn, table, columns, rows)
                migrated[table] = n
                print(f"  {table}: {n} rows inserted")
        finally:
            conn.execute(text("SET session_replication_role = 'origin'"))

    print("\nResetting PostgreSQL sequences...")
    _reset_sequences(pg_engine)

    print("\n=== PostgreSQL row counts (after) ===")
    mismatches: list[str] = []
    for table in app_tables:
        pg_n = _count_pg(pg_engine, table)
        counts["postgres_after"][table] = pg_n
        sq_n = counts["sqlite"][table]
        status = "OK" if pg_n == sq_n else "MISMATCH"
        print(f"  {table}: {pg_n} ({status})")
        if pg_n != sq_n:
            mismatches.append(f"{table}: sqlite={sq_n}, postgres={pg_n}")

    sqlite_conn.close()
    pg_engine.dispose()

    print("\n=== Summary ===")
    total = sum(migrated.values())
    print(f"  Tables migrated: {len(migrated)}")
    print(f"  Total rows inserted: {total}")
    if mismatches:
        print("\n  WARN — count mismatches:")
        for m in mismatches:
            print(f"    {m}")
    else:
        print("  All row counts match.")

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate MaestroWeb SQLite data to PostgreSQL.")
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=SQLITE_DEFAULT,
        help=f"Path to SQLite database (default: {SQLITE_DEFAULT.name})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show counts only")
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not TRUNCATE PostgreSQL tables before import",
    )
    args = parser.parse_args()

    migrate(args.sqlite_path, dry_run=args.dry_run, truncate=not args.no_truncate)


if __name__ == "__main__":
    main()
