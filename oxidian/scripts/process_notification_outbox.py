"""Procesa notificaciones pendientes del outbox.

Uso:
  FLASK_ENV=production python scripts/process_notification_outbox.py --limit 50
  # Modo long-lived (recomendado para producción — evita el coste de rearrancar Flask):
  FLASK_ENV=production python scripts/process_notification_outbox.py --loop --interval 2
"""
import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from services import (
    autocancelar_confirmaciones_expiradas,
    procesar_notificaciones_pendientes,
    purgar_registros_antiguos,
    rebalancear_pedidos_huerfanos,
)

logger = logging.getLogger("outbox_worker")


def _run_once(app, limit):
    with app.app_context():
        return procesar_notificaciones_pendientes(limit=limit)


def _run_purge(app):
    """Poda periódica de notification_outbox y idempotency_keys.
    Best-effort: nunca lanza excepción — el worker debe seguir procesando.
    """
    try:
        with app.app_context():
            return purgar_registros_antiguos()
    except Exception:
        logger.exception("outbox worker: purga falló")
        return None


def _run_rebalanceo(app):
    """Rebalanceo periódico de pedidos huérfanos (empleado offline/inactivo).

    Ejecutable cada N segundos (default 300 = 5 min). Sin esto, pedidos
    activos de un preparador que se cae quedan bloqueados hasta que un admin
    lo tome manualmente. Best-effort — errores no interrumpen el worker.
    """
    try:
        with app.app_context():
            return rebalancear_pedidos_huerfanos()
    except Exception:
        logger.exception("outbox worker: rebalanceo falló")
        return None


def _run_autocancel_confirmaciones(app):
    """Auto-cancela pedidos HIGH sin respuesta del cliente pasado el TTL.

    Best-effort — el worker debe seguir ejecutándose aunque un pedido
    concreto falle. Se ejecuta con menor frecuencia que rebalanceo porque
    el TTL default es de horas.
    """
    try:
        with app.app_context():
            return autocancelar_confirmaciones_expiradas()
    except Exception:
        logger.exception("outbox worker: autocancel confirmaciones falló")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.environ.get("OUTBOX_LIMIT", "25")))
    parser.add_argument("--env", default=os.environ.get("FLASK_ENV", "production"))
    parser.add_argument("--loop", action="store_true",
                        help="Bucle long-lived (mantiene Flask cargado y sondea la BD)")
    parser.add_argument("--interval", type=float,
                        default=float(os.environ.get("OUTBOX_INTERVAL_SECONDS", "2")),
                        help="Segundos entre sondeos en modo --loop (por defecto 2)")
    args = parser.parse_args()

    app = create_app(args.env)

    if not args.loop:
        print(_run_once(app, args.limit))
        return

    stop = {"flag": False}

    def _stop(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Purga periódica cada N ciclos (default cada hora si interval=2s).
    purge_every_seconds = max(300, int(os.environ.get("OUTBOX_PURGE_EVERY_SECONDS", "3600") or 3600))
    # Rebalanceo de pedidos huérfanos (empleado offline) — frecuencia más
    # agresiva porque afecta operativa activa. Default 5 min.
    rebalanceo_every_seconds = max(60, int(os.environ.get("REBALANCEO_EVERY_SECONDS", "300") or 300))
    # Auto-cancel de pedidos HIGH sin respuesta del cliente. Frecuencia
    # conservadora (default 10 min) porque el TTL suele ser de horas —
    # no vale correrlo cada minuto. Cap mínimo 5 min.
    autocancel_every_seconds = max(300, int(os.environ.get("AUTOCANCEL_EVERY_SECONDS", "600") or 600))
    logger.info(
        "outbox worker: loop cada %.1fs (limit=%d) purga cada %ds rebalanceo cada %ds autocancel cada %ds",
        args.interval, args.limit, purge_every_seconds, rebalanceo_every_seconds, autocancel_every_seconds,
    )
    last_purge = time.monotonic()
    last_rebalanceo = time.monotonic()
    last_autocancel = time.monotonic()
    while not stop["flag"]:
        try:
            _run_once(app, args.limit)
        except Exception:
            logger.exception("outbox worker: error procesando lote")
        # Poda periódica separada del procesamiento — no retrasa las notificaciones.
        if time.monotonic() - last_purge >= purge_every_seconds:
            _run_purge(app)
            last_purge = time.monotonic()
        # Rebalanceo periódico — devuelve la carga al pool cuando alguien cae.
        if time.monotonic() - last_rebalanceo >= rebalanceo_every_seconds:
            _run_rebalanceo(app)
            last_rebalanceo = time.monotonic()
        # Auto-cancel de HIGH sin confirmación — cierra la puerta de
        # entrada a pedidos que llevan horas esperando respuesta.
        if time.monotonic() - last_autocancel >= autocancel_every_seconds:
            _run_autocancel_confirmaciones(app)
            last_autocancel = time.monotonic()
        # Sleep interrumpible para no retrasar SIGTERM
        for _ in range(max(1, int(args.interval * 10))):
            if stop["flag"]:
                break
            time.sleep(0.1)


if __name__ == "__main__":
    main()
