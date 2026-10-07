#!/usr/bin/env python3
"""Envía el resumen semanal de Gestión. Lo dispara cron los lunes a las 9:00 (Chile)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402
from services.notificaciones_gestion import enviar_resumen_semanal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='Resumen semanal de etapas y tareas')
    parser.add_argument('--force', action='store_true', help='Enviar aunque no sea lunes o ya se haya enviado')
    parser.add_argument('--dry-run', action='store_true', help='Armar el correo sin enviarlo')
    args = parser.parse_args()
    with app.app_context():
        return enviar_resumen_semanal(force=args.force, dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main())
