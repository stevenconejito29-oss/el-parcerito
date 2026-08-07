#!/usr/bin/env python3
"""Purga filas expiradas de ``idempotency_keys``.

La tabla `idempotency_keys` crece indefinidamente porque cada checkout
web/POS/bot escribe una fila con `expira_en` (30s típico) y NADA la
limpia después. En producción con 100 checkouts/día son ~36 500 filas
al año — no bloquea nada pero infla backups y ralentiza queries a
largo plazo. Este script borra las que ya vencieron.

Diseñado para ejecutarse via cron:

    # Cada noche a las 04:15 (ventana tras el cierre diario)
    15 4 * * * cd /app && python3 scripts/purge_expired_idempotency.py \
        >> /var/log/oxidian-purge.log 2>&1

También se ejecuta como fail-safe al arrancar el contenedor (ver
`apply_schema_migrations._purge_expired_idempotency_keys`) para
garantizar que aunque no exista el cron externo, cada restart limpia.

Idempotente: si no hay filas expiradas sale con código 0 sin escribir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Auto-inyecta el directorio de la app al sys.path
_APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APP_DIR))

from sqlalchemy import text  # noqa: E402
from app import create_app  # noqa: E402
from extensions import db  # noqa: E402


def purge_expired() -> int:
    """Borra filas de idempotency_keys con expira_en < NOW().

    Devuelve el número de filas borradas. No hace commit por sesión
    porque un cron corre en su propio contexto y el ``with`` cierra;
    aquí sí commiteamos porque somos entry point standalone.
    """
    # Batch de borrado con LIMIT implícito por la propia cláusula: si
    # hay millones de filas expiradas, un DELETE sin límite podría
    # bloquear la tabla durante segundos. En la práctica no debería
    # llegar a esos volúmenes, pero para tenerlo bajo cota bloqueamos
    # el DELETE por ventana temporal — solo lo expirado hasta ahora,
    # nuevas filas expiradas en 30s se borrarán en la próxima corrida.
    result = db.session.execute(
        text("DELETE FROM idempotency_keys WHERE expira_en < NOW()")
    )
    borradas = result.rowcount or 0
    db.session.commit()
    return borradas


def main() -> int:
    app = create_app()
    with app.app_context():
        try:
            borradas = purge_expired()
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] purge_expired_idempotency: {exc}", file=sys.stderr)
            db.session.rollback()
            return 1
    if borradas:
        print(f"[OK] {borradas} idempotency_keys expiradas borradas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
