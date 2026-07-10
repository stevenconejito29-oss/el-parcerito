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
from services import procesar_notificaciones_pendientes

logger = logging.getLogger("outbox_worker")


def _run_once(app, limit):
    with app.app_context():
        return procesar_notificaciones_pendientes(limit=limit)


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

    logger.info("outbox worker: loop cada %.1fs (limit=%d)", args.interval, args.limit)
    while not stop["flag"]:
        try:
            _run_once(app, args.limit)
        except Exception:
            logger.exception("outbox worker: error procesando lote")
        # Sleep interrumpible para no retrasar SIGTERM
        for _ in range(max(1, int(args.interval * 10))):
            if stop["flag"]:
                break
            time.sleep(0.1)


if __name__ == "__main__":
    main()
