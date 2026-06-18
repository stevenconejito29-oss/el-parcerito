"""Procesa notificaciones pendientes del outbox.

Uso:
  FLASK_ENV=production .venv/bin/python scripts/process_notification_outbox.py --limit 50
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from services import procesar_notificaciones_pendientes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.environ.get("OUTBOX_LIMIT", "25")))
    parser.add_argument("--env", default=os.environ.get("FLASK_ENV", "production"))
    args = parser.parse_args()

    app = create_app(args.env)
    with app.app_context():
        resultado = procesar_notificaciones_pendientes(limit=args.limit)
        print(resultado)


if __name__ == "__main__":
    main()
