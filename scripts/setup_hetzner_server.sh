#!/usr/bin/env bash
# Preparación inicial de un VPS Ubuntu en Hetzner para MaestroWeb beta.
# Ejecutar como root o con sudo en el servidor:
#   curl -fsSL ... | bash
# o tras clonar el repo:
#   sudo ./scripts/setup_hetzner_server.sh

set -euo pipefail

if [[ "${EUID:-0}" -ne 0 ]]; then
  echo "Ejecute con sudo." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg ufw

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

systemctl enable docker
systemctl start docker

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
echo "y" | ufw enable

mkdir -p /opt/maestroweb
echo ""
echo "Servidor listo."
echo "Siguiente:"
echo "  1. Copie el proyecto a /opt/maestroweb"
echo "  2. cp .env.production.example .env && nano .env"
echo "  3. Suba deploy_export/ y ejecute ./scripts/import_beta_data.sh"
