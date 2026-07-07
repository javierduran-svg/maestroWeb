# Despliegue beta en Hetzner (Docker)

Guía para publicar MaestroWeb con **datos reales** en un VPS Hetzner, accesible por HTTPS para el equipo.

## Resumen del stack

| Servicio | Rol |
|---|---|
| **nginx (host)** | HTTPS (Let's Encrypt) + proxy a la app; comparte el VPS con la landing |
| **app** | Flask + Gunicorn en `127.0.0.1:8000` |
| **db** | PostgreSQL 16 (solo red interna Docker) |

## Requisitos previos

- Cuenta Hetzner Cloud
- Dominio con acceso al DNS
- Datos locales en PostgreSQL (`docker compose up -d` en tu PC)
- Misma `SECRET_ENCRYPTION_KEY` que usaste al cifrar credenciales SII/banco (si aplica)

## 1. Crear el VPS en Hetzner

1. **Cloud Console → Add Server**
2. Ubicación: la más cercana al equipo (ej. Falkenstein o Helsinki)
3. Imagen: **Ubuntu 24.04**
4. Tipo: **CX22** (2 vCPU, 4 GB RAM) es suficiente para beta
5. Red: IPv4 pública
6. SSH key: añade tu clave pública
7. Crear servidor y anotar la **IP pública**

## 2. Configurar DNS

En tu registrador de dominio, crea un registro **A**:

```
erp.bgreenchile.cl  →  167.233.237.116
```

El dominio raíz (`bgreenchile.cl`) y `www` pueden seguir sirviendo la landing page existente (`index.html`). Este stack solo atiende el subdominio **erp**.

Espera propagación (minutos a 1 hora). Caddy no obtendrá certificado hasta que el dominio resuelva a ese servidor.

## 3. Preparar el servidor

Conéctate por SSH:

```bash
ssh root@IP_DEL_VPS
```

Opción A — script incluido:

```bash
git clone <URL_DE_TU_REPO> /opt/maestroweb
cd /opt/maestroweb
chmod +x scripts/*.sh
sudo ./scripts/setup_hetzner_server.sh
```

Opción B — manual: instalar Docker Engine + Compose plugin y abrir puertos 22, 80, 443 en el firewall.

## 4. Exportar datos desde tu PC (Windows)

En la carpeta del proyecto, con PostgreSQL local corriendo:

```powershell
.\scripts\export_beta_data.ps1
```

Genera:

- `deploy_export\maestroweb.dump` — base de datos completa
- `deploy_export\uploads.tar.gz` — logos, fotos, certificados

Sube el proyecto y `deploy_export/` al servidor (SCP, SFTP, rsync):

```powershell
scp -r deploy_export root@IP_DEL_VPS:/opt/maestroweb/
scp -r . root@IP_DEL_VPS:/opt/maestroweb/
```

(O usa git en el servidor y solo sube `deploy_export/` por SCP.)

## 5. Configurar `.env` en el servidor

```bash
cd /opt/maestroweb
cp .env.production.example .env
nano .env
```

Complete obligatoriamente:

| Variable | Cómo generarla |
|---|---|
| `APP_DOMAIN` | `erp.bgreenchile.cl` |
| `ACME_EMAIL` | Email válido para Let's Encrypt |
| `POSTGRES_PASSWORD` | Contraseña fuerte aleatoria |
| `FLASK_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `SECRET_ENCRYPTION_KEY` | **La misma de tu `.env` local** si restaura BD cifrada |
| `SEED_DEV_ADMIN` | `0` (no resetea contraseñas al arrancar) |

No incluya credenciales SII, Fintoc ni datos de empresa en `.env`: van en la base de datos (restaurada con el dump) y se gestionan desde la app por cada empresa.

## 6. Importar datos y levantar la app

```bash
cd /opt/maestroweb
chmod +x scripts/import_beta_data.sh
./scripts/import_beta_data.sh ./deploy_export
```

El script:

1. Levanta PostgreSQL
2. Restaura el dump
3. Restaura `uploads/`
4. Construye y levanta app + Caddy

Primera vez puede tardar unos minutos (build de imagen + certificado SSL).

## 7. Verificar

Abre `https://erp.bgreenchile.cl` e inicia sesión con las **mismas credenciales que en local** (no se resetean si `SEED_DEV_ADMIN=0`).

Comprobar logs:

```bash
docker compose -f docker-compose.prod.yml logs -f app
docker compose -f docker-compose.prod.yml ps
```

## Operación diaria

```bash
# Ver estado
docker compose -f docker-compose.prod.yml ps

# Logs
docker compose -f docker-compose.prod.yml logs -f

# Reiniciar app tras cambio de código
docker compose -f docker-compose.prod.yml up -d --build app

# Backup manual de BD
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U maestroweb -Fc maestroweb > backup_$(date +%Y%m%d).dump
```

## Actualizar versión (nuevo deploy)

1. Exportar backup en servidor (comando anterior)
2. Subir código nuevo
3. `docker compose -f docker-compose.prod.yml up -d --build`
4. Las migraciones Alembic corren solas al arrancar el contenedor

## Seguridad beta

- PostgreSQL **no** expone el puerto 5432 al exterior
- Use contraseñas fuertes y `SEED_DEV_ADMIN=0`
- Limite acceso SSH por clave, no por contraseña
- Considere restringir beta por VPN o IP si maneja datos sensibles

## Solución de problemas

**Certificado SSL no se emite**

- Compruebe que el DNS apunta a la IP correcta: `dig +short erp.bgreenchile.cl`
- Puertos 80/443 abiertos en Hetzner Firewall y UFW

**Error de conexión a BD al arrancar**

- `docker compose -f docker-compose.prod.yml logs db`
- Verifique `POSTGRES_PASSWORD` coherente en `.env`

**Credenciales SII/banco no funcionan tras restore**

- `SECRET_ENCRYPTION_KEY` debe ser **idéntica** a la del entorno donde se cifraron

**La app resetea la contraseña del admin**

- Confirme `SEED_DEV_ADMIN=0` en `.env` del servidor

## Interfaz de la app

La SPA vive en `app.html` (no `index.html`) para no chocar con la landing page del sitio principal. Flask la sirve en la ruta `/` de `erp.bgreenchile.cl`.

## Desarrollo local vs producción

| | Local | Hetzner |
|---|---|---|
| Compose | `docker-compose.yml` | `docker-compose.prod.yml` |
| Servidor | `python app.py` | Gunicorn en contenedor |
| HTTPS | No | Caddy |
| Admin seed | `SEED_DEV_ADMIN=1` (default) | `SEED_DEV_ADMIN=0` |
