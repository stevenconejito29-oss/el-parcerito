#!/usr/bin/env python3
"""Verifica atomicidad, concesiones y recuperación del outbox."""

from __future__ import annotations

import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from extensions import db
from models import NotificationOutbox, SiteConfig, utcnow
from services import enviar_whatsapp_generico, procesar_notificaciones_pendientes


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    app = create_app("production")
    marker = f"qa-outbox-{uuid.uuid4().hex}"
    original_simulation = None
    simulation_existed = False

    with app.app_context():
        try:
            config = SiteConfig.query.filter_by(clave="WHATSAPP_SIMULATE_SEND").first()
            simulation_existed = config is not None
            original_simulation = config.valor if config else None
            SiteConfig.set("WHATSAPP_SIMULATE_SEND", "1")
            db.session.commit()

            require(
                enviar_whatsapp_generico("+34990000001", marker, evento=marker),
                "No se pudo crear el mensaje transaccional",
            )
            db.session.rollback()
            require(
                NotificationOutbox.query.filter_by(evento=marker).count() == 0,
                "El rollback dejó una notificación huérfana",
            )

            require(
                enviar_whatsapp_generico("+34990000001", marker, evento=marker),
                "No se pudo encolar el mensaje confirmado",
            )
            db.session.commit()
            job = NotificationOutbox.query.filter_by(evento=marker).one()

            job.estado = "processing"
            job.siguiente_intento_en = utcnow() + timedelta(minutes=5)
            db.session.commit()
            result = procesar_notificaciones_pendientes(limit=10, only_ids=[job.id])
            require(result["procesadas"] == 0, "Se procesó una concesión todavía vigente")

            job.estado = "processing"
            job.siguiente_intento_en = utcnow() - timedelta(seconds=1)
            db.session.commit()
            result = procesar_notificaciones_pendientes(limit=10, only_ids=[job.id])
            db.session.refresh(job)
            require(result["procesadas"] == 1, "No se recuperó la concesión vencida")
            require(job.estado == "sent", f"Estado final inesperado: {job.estado}")
            require(job.intentos == 1, f"Intentos inesperados: {job.intentos}")

            print("OK: outbox transaccional, concesión exclusiva y recuperación vencida.")
            return 0
        finally:
            db.session.rollback()
            NotificationOutbox.query.filter_by(evento=marker).delete()
            config = SiteConfig.query.filter_by(clave="WHATSAPP_SIMULATE_SEND").first()
            if simulation_existed:
                SiteConfig.set("WHATSAPP_SIMULATE_SEND", original_simulation)
            elif config:
                db.session.delete(config)
            db.session.commit()


if __name__ == "__main__":
    raise SystemExit(main())
