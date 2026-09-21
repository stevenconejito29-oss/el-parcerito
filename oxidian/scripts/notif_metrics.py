"""Metricas del gate de notificaciones (canal_service).

Ejecucion:
    docker exec oxidian python3 scripts/notif_metrics.py --dias 30

Objetivo: que dentro de 6 meses el usuario pueda verificar que el gate
esta funcionando y no perdiendo notificaciones importantes. Salida en
JSON para consumo por dashboards o inspeccion humana rapida.

Metricas por dia:
  * total_encolados
  * por_canal (wa, push, web, push+web, none)
  * por_evento
  * top_razones_none (los skips agrupados por razon extraida del payload)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func

from app import create_app
from extensions import db
from models import NotificationOutbox


def _parse_razon(payload_json: str) -> str:
    try:
        data = json.loads(payload_json or "{}")
        return str(data.get("razon") or data.get("canal_decision") or "sin_razon")
    except (json.JSONDecodeError, TypeError):
        return "payload_invalido"


def gather(dias: int = 30) -> dict:
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(days=max(1, int(dias)))
    q = (
        db.session.query(NotificationOutbox)
        .filter(NotificationOutbox.creado_en >= desde)
        .yield_per(500)
    )

    por_dia: dict[str, dict] = defaultdict(lambda: {
        "total_encolados": 0,
        "por_canal": defaultdict(int),
        "por_evento": defaultdict(int),
        "top_razones_none": defaultdict(int),
    })

    for row in q:
        dia = row.creado_en.date().isoformat() if row.creado_en else "unknown"
        bucket = por_dia[dia]
        bucket["total_encolados"] += 1
        bucket["por_canal"][row.canal or "?"] += 1
        bucket["por_evento"][row.evento or "?"] += 1
        if (row.canal == "none") or (row.estado == "skipped"):
            razon = _parse_razon(row.payload_json)
            bucket["top_razones_none"][razon] += 1

    # convertir defaultdicts a dicts serializables
    salida: dict[str, dict] = {}
    for dia in sorted(por_dia.keys()):
        b = por_dia[dia]
        salida[dia] = {
            "total_encolados": b["total_encolados"],
            "por_canal": dict(sorted(b["por_canal"].items(), key=lambda x: -x[1])),
            "por_evento": dict(sorted(b["por_evento"].items(), key=lambda x: -x[1])),
            "top_razones_none": dict(sorted(
                b["top_razones_none"].items(), key=lambda x: -x[1]
            )[:10]),
        }
    return {
        "generado_en": ahora.isoformat(),
        "ventana_dias": dias,
        "dias": salida,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=30)
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        result = gather(dias=args.dias)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
