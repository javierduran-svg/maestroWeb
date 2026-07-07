#!/usr/bin/env bash
# Restaura dump PostgreSQL y uploads/ en el servidor Hetzner.
# Uso (desde la raíz del proyecto en el servidor):
#   chmod +x scripts/import_beta_data.sh
#   ./scripts/import_beta_data.sh ./deploy_export

set -euo pipefail

EXPORT_DIR="${1:-./deploy_export}"
DUMP="${EXPORT_DIR}/maestroweb.dump"
UPLOADS="${EXPORT_DIR}/uploads.tar.gz"
COMPOSE="docker compose -f docker-compose.prod.yml"

if [[ ! -f "$DUMP" ]]; then
  echo "No se encontró $DUMP" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Cree .env desde .env.production.example antes de importar." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

echo "Levantando solo PostgreSQL..."
$COMPOSE up -d db

echo "Esperando PostgreSQL..."
for i in $(seq 1 30); do
  if $COMPOSE exec -T db pg_isready -U "${POSTGRES_USER:-maestroweb}" -d "${POSTGRES_DB:-maestroweb}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "Restaurando base de datos..."
$COMPOSE exec -T db pg_restore \
  -U "${POSTGRES_USER:-maestroweb}" \
  -d "${POSTGRES_DB:-maestroweb}" \
  --clean --if-exists --no-owner --no-acl \
  < "$DUMP"

if [[ -f "$UPLOADS" ]]; then
  echo "Restaurando uploads/..."
  $COMPOSE up -d app
  sleep 3
  $COMPOSE exec -T app mkdir -p /app/uploads
  cat "$UPLOADS" | $COMPOSE exec -T app tar -xzf - -C /app
  echo "Uploads restaurados."
else
  echo "Aviso: no hay uploads.tar.gz — se omitió."
fi

echo "Levantando app + PostgreSQL (sin Caddy; nginx del host hace HTTPS)..."
$COMPOSE up -d --build --remove-orphans

echo "Importación completada."
echo "Si aún no configuró nginx para ${APP_DOMAIN:-erp.bgreenchile.cl}:"
echo "  sudo cp docker/nginx-erp.conf /etc/nginx/sites-available/erp.bgreenchile.cl"
echo "  sudo ln -sf /etc/nginx/sites-available/erp.bgreenchile.cl /etc/nginx/sites-enabled/"
echo "  sudo nginx -t && sudo systemctl reload nginx"
echo "  sudo certbot --nginx -d ${APP_DOMAIN:-erp.bgreenchile.cl}"
echo "Luego abra: https://${APP_DOMAIN:-erp.bgreenchile.cl}"
