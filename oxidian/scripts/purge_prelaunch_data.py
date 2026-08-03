#!/usr/bin/env python3
"""Purga datos de pruebas conservando catálogo, configuración y cuentas laborales.

El modo por defecto es diagnóstico. Para ejecutar la transacción destructiva:

    OXIDIAN_CONFIRM_PURGE=PRESERVE_CATALOG_AND_STAFF \
      python scripts/purge_prelaunch_data.py --execute

Se preservan productos y toda su configuración (combos, presentaciones,
sabores, stock, proveedores/socios), zonas, horarios, permisos, conocimiento
del chatbot y usuarios autenticables. Se eliminan clientes y actividad previa.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONFIRM_TOKEN = "PRESERVE_CATALOG_AND_STAFF"

# Un TRUNCATE conjunto evita depender del orden de las FK y reinicia únicamente
# secuencias de actividad. Ninguna tabla de catálogo/configuración figura aquí.
ACTIVITY_TABLES = (
    "notification_outbox",
    "push_broadcasts",
    "push_subscriptions",
    "order_events",
    "order_provider_status",
    "affiliate_uses",
    "points_log",
    "caja",
    "staff_payments",
    "daily_closures",
    "hub_commissions",
    "reviews",
    "idempotency_keys",
    "order_items",
    "orders",
    "product_batches",
    "affiliate_codes",
    "coupons",
    "campanas_marketing",
    "bot_ai_messages",
    "bot_ai_usage",
    "ai_conversations",
    "ai_usage_log",
    "ai_assistant_config",
    "audit_log",
    "price_history",
)

PRESERVED_CATALOG_TABLES = (
    "categorias",
    "products",
    "stock",
    "product_variants",
    "product_presentations",
    "product_presentation_flavors",
    "product_extra_groups",
    "product_extra_options",
    "extra_catalog_items",
    "product_extras",
    "combo_groups",
    "combo_items",
    "combo_item_allowed_flavors",
    "combo_item_allowed_presentations",
    "proveedores",
    "proveedor_productos",
)


def table_counts(db, tables: tuple[str, ...]) -> dict[str, int]:
    existing = set(inspect(db.engine).get_table_names())
    return {
        table: int(db.session.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0)
        for table in tables
        if table in existing
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="aplica la purga confirmada")
    args = parser.parse_args()

    from app import create_app
    from extensions import db
    from models import ROLES_AUTENTICABLES, User

    app = create_app("production")
    with app.app_context():
        existing = set(inspect(db.engine).get_table_names())
        activity = tuple(table for table in ACTIVITY_TABLES if table in existing)
        before_activity = table_counts(db, activity)
        before_catalog = table_counts(db, PRESERVED_CATALOG_TABLES)
        staff_before = {
            row.id: (row.email, row.rol)
            for row in User.query.filter(User.rol.in_(ROLES_AUTENTICABLES)).all()
        }
        customer_count = User.query.filter_by(rol="cliente").count()

        print("Purga previa a producción")
        print(f"  productos preservados: {before_catalog.get('products', 0)}")
        print(f"  cuentas laborales preservadas: {len(staff_before)}")
        print(f"  clientes a eliminar: {customer_count}")
        print(f"  registros de actividad a eliminar: {sum(before_activity.values())}")
        for table, count in before_activity.items():
            if count:
                print(f"    {table}: {count}")

        if not args.execute:
            print("DRY-RUN: no se modificó ningún dato.")
            return 0
        if os.environ.get("OXIDIAN_CONFIRM_PURGE") != CONFIRM_TOKEN:
            raise SystemExit(
                f"REHUSO: OXIDIAN_CONFIRM_PURGE debe valer exactamente {CONFIRM_TOKEN!r}"
            )
        if db.engine.dialect.name != "postgresql":
            raise SystemExit("REHUSO: la purga operativa solo se ejecuta sobre PostgreSQL")
        if not staff_before:
            raise SystemExit("REHUSO: no hay cuentas laborales que preservar")

        try:
            # Impide que dos operadores o un despliegue ejecuten la purga a la vez.
            db.session.execute(text("SELECT pg_advisory_xact_lock(684219731)"))
            if activity:
                quoted = ", ".join(f'"{table}"' for table in activity)
                db.session.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))

            # Referencias editoriales no deben conservar la identidad de un
            # cliente de QA. Las relaciones de propiedad de producto no se
            # tocan: si estuvieran mal modeladas, el DELETE fallará y revierte.
            for table, column in (
                ("site_config", "actualizado_por"),
                ("menu_config", "creado_por"),
                ("knowledge_entries", "actualizado_por"),
            ):
                if table in existing:
                    db.session.execute(text(
                        f'UPDATE "{table}" SET "{column}" = NULL '
                        f'WHERE "{column}" IN (SELECT id FROM users WHERE rol = :role)'
                    ), {"role": "cliente"})

            User.query.filter_by(rol="cliente").delete(synchronize_session=False)
            db.session.execute(text(
                "UPDATE users SET puntos = 0, cod_puntos = NULL, "
                "cod_puntos_expira = NULL, cod_puntos_intentos = 0, "
                "en_linea = FALSE, last_seen = NULL WHERE rol <> 'cliente'"
            ))

            # Validar dentro de la misma transacción: cualquier cascada no
            # prevista provoca rollback completo, nunca una purga parcial.
            staff_after = {
                row.id: (row.email, row.rol)
                for row in User.query.filter(User.rol.in_(ROLES_AUTENTICABLES)).all()
            }
            after_catalog = table_counts(db, PRESERVED_CATALOG_TABLES)
            after_activity = table_counts(db, activity)
            if staff_after != staff_before:
                raise RuntimeError("La identidad de las cuentas laborales cambió durante la purga")
            if after_catalog != before_catalog:
                raise RuntimeError("El catálogo cambió durante la purga")
            if User.query.filter_by(rol="cliente").count() != 0:
                raise RuntimeError("Quedaron clientes después de la purga")
            if any(after_activity.values()):
                raise RuntimeError(f"Quedó actividad después de la purga: {after_activity}")
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        print("OK: actividad eliminada; catálogo, configuración y roles preservados.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
