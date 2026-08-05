#!/usr/bin/env python3
"""Cierre diario automático de caja.

Diseñado para ejecutarse via cron todas las noches. Persiste el
`DailyClosure` del día anterior de negocio (usando la fecha real de
`business_today`, respetando el desplazamiento horario si el negocio
opera cruzando medianoche).

Uso:
    python3 scripts/daily_closure.py           # cierra día anterior
    python3 scripts/daily_closure.py 2026-08-04  # cierra fecha concreta

Idempotente: si el cierre ya existe, sale con código 0 sin errores.
Errores → código 1 + log a stderr.

Cron sugerido (crontab -e en el contenedor / host):
    5 4 * * * cd /app && python3 scripts/daily_closure.py >> /var/log/oxidian-cierre.log 2>&1

Se ejecuta a las 04:05 — ventana segura después del cruce de medianoche
para asegurar que `business_today()` ya rotó y no hay pedidos activos
que puedan cambiar los totales del día anterior.
"""
import os
import sys
from datetime import date

# Auto-inyecta el directorio de la app al sys.path para que `from app import
# create_app` funcione sin necesidad de PYTHONPATH ni cwd específico.
# Estructura esperada: `scripts/daily_closure.py` dentro del root de la app.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


def main():
    fecha_arg = None
    if len(sys.argv) > 1:
        try:
            fecha_arg = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"[error] Fecha inválida: {sys.argv[1]!r}. Formato: YYYY-MM-DD", file=sys.stderr)
            sys.exit(2)

    # Import lazy para que un error de la app no afecte el parseo de argv.
    from app import create_app
    app = create_app("production")
    with app.app_context():
        from services import cerrar_dia_automatico
        result = cerrar_dia_automatico(fecha_arg)

    if not result.get("ok"):
        print(f"[error] {result.get('mensaje')}", file=sys.stderr)
        sys.exit(1)

    prefix = "[skip]" if result.get("skipped") else "[ok]"
    print(f"{prefix} fecha={result.get('fecha')} cierre_id={result.get('cierre_id')} "
          f"{result.get('mensaje')}")
    sys.exit(0)


if __name__ == "__main__":
    main()
