#!/bin/sh
set -e

echo "Esperando PostgreSQL..."
python <<'PY'
import os
import sys
import time
from urllib.parse import urlparse

import psycopg2

url = os.environ.get("DATABASE_URL", "")
if not url:
    print("DATABASE_URL no configurada", file=sys.stderr)
    sys.exit(1)

parsed = urlparse(url)
for attempt in range(60):
    try:
        psycopg2.connect(
            host=parsed.hostname or "db",
            port=parsed.port or 5432,
            user=parsed.username,
            password=parsed.password,
            dbname=(parsed.path or "/maestroweb").lstrip("/"),
        )
        break
    except psycopg2.OperationalError:
        if attempt == 59:
            raise
        time.sleep(2)
PY

echo "Ejecutando migraciones Alembic..."
flask db upgrade

echo "Bootstrap de esquema..."
python <<'PY'
from app import app
from common import _migrar_schema

with app.app_context():
    _migrar_schema()
PY

WORKERS="${GUNICORN_WORKERS:-2}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"

echo "Iniciando Gunicorn (workers=${WORKERS})..."
exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers "${WORKERS}" \
    --threads 4 \
    --timeout "${TIMEOUT}" \
    --access-logfile - \
    --error-logfile - \
    app:app
