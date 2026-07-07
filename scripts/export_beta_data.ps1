#Exporta PostgreSQL (Docker local) y la carpeta uploads/ para subir a Hetzner.
# Uso (PowerShell, desde la raíz del proyecto):
#   .\scripts\export_beta_data.ps1
# Genera: deploy_export\maestroweb.dump y deploy_export\uploads.tar.gz

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$OutDir = Join-Path $Root "deploy_export"
$DumpFile = Join-Path $OutDir "maestroweb.dump"
$UploadsArchive = Join-Path $OutDir "uploads.tar.gz"
$UploadsDir = Join-Path $Root "uploads"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host "Comprobando contenedor PostgreSQL..."
$running = docker compose -f (Join-Path $Root "docker-compose.yml") ps --status running --services db 2>$null
if (-not $running) {
    Write-Host "Levantando PostgreSQL local..."
    docker compose -f (Join-Path $Root "docker-compose.yml") up -d db
    Start-Sleep -Seconds 5
}

Write-Host "Exportando base de datos..."
docker compose -f (Join-Path $Root "docker-compose.yml") exec -T db `
    pg_dump -U maestroweb -Fc --no-owner --no-acl -f /tmp/maestroweb.dump maestroweb
docker compose -f (Join-Path $Root "docker-compose.yml") cp `
    "db:/tmp/maestroweb.dump" $DumpFile

$bytes = (Get-Item $DumpFile).Length
if ($bytes -lt 100) {
    throw "El dump parece vacio ($bytes bytes). Revise que docker compose db este activo y tenga datos."
}
Write-Host "Dump OK: $DumpFile ($([math]::Round($bytes/1KB, 1)) KB)"

if (Test-Path $UploadsDir) {
    Write-Host "Empaquetando uploads/..."
    if (Test-Path $UploadsArchive) { Remove-Item $UploadsArchive -Force }
    tar -czf $UploadsArchive -C $Root uploads
    Write-Host "Uploads OK: $UploadsArchive"
} else {
    Write-Host "Aviso: no existe uploads/; se omitio el archivo."
}

Write-Host ""
Write-Host 'Listo. Suba deploy_export/ a Hetzner y ejecute scripts/import_beta_data.sh'
