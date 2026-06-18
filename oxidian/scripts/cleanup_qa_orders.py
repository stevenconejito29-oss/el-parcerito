#!/usr/bin/env python3
"""Elimina fixtures de pedidos con prefijos QA explícitos."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from extensions import db


ALLOWED_PREFIXES = ("OX-TEST-", "QA-", "QA-VIS-")
DEPENDENT_TABLES = (
    "notification_outbox",
    "reviews",
    "affiliate_uses",
    "points_log",
    "caja",
    "staff_payments",
    "order_events",
    "order_provider_status",
    "order_items",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True, choices=ALLOWED_PREFIXES)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    app = create_app("production")
    with app.app_context():
        rows = db.session.execute(
            text("""
                SELECT id, numero_pedido, estado
                FROM orders
                WHERE numero_pedido LIKE :pattern
                ORDER BY id
            """),
            {"pattern": f"{args.prefix}%"},
        ).mappings().all()
        print({"prefix": args.prefix, "count": len(rows), "orders": [dict(row) for row in rows]})
        if not args.apply or not rows:
            return 0

        order_ids = [row["id"] for row in rows]
        for table in DEPENDENT_TABLES:
            db.session.execute(
                text(f"DELETE FROM {table} WHERE pedido_id = ANY(:ids)"),
                {"ids": order_ids},
            )
        db.session.execute(
            text("""
                DELETE FROM audit_log
                WHERE recurso = 'order' AND recurso_id = ANY(:ids)
            """),
            {"ids": order_ids},
        )
        db.session.execute(
            text("DELETE FROM orders WHERE id = ANY(:ids)"),
            {"ids": order_ids},
        )
        db.session.commit()
        print({"deleted": len(order_ids)})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
