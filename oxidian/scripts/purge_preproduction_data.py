#!/usr/bin/env python3
"""Limpieza transaccional previa a producción, sin tocar el catálogo.

Conserva el catálogo completo (productos y sus dependencias), configuración,
zonas y cuentas ``super_admin``. Elimina pedidos, clientes, empleados,
finanzas, marketing, notificaciones y demás actividad de prueba.

Por seguridad es dry-run salvo que se use simultáneamente::

    python scripts/purge_preproduction_data.py \
      --execute --confirm PURGAR-DATOS-PREPRODUCCION

La operación comprueba dentro de la misma transacción que ninguna tabla
protegida haya perdido filas. Ante cualquier diferencia hace rollback.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app
from extensions import db
from models import utcnow


CONFIRMATION = "PURGAR-DATOS-PREPRODUCCION"
PROTECTED_TABLES = {
    "schema_migrations",
    "site_config",
    "menu_config",
    "knowledge_entries",
    "categorias",
    "products",
    "product_batches",
    "stock",
    "product_variants",
    "product_presentations",
    "product_presentation_flavors",
    "extra_catalog_items",
    "product_extra_groups",
    "product_extra_options",
    "combo_groups",
    "combo_items",
    "combo_item_allowed_flavors",
    "combo_item_allowed_presentations",
    "proveedores",
    "proveedor_productos",
    "zonas_entrega",
    "users",  # se depura por rol, nunca se trunca
}


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_counts(table_names: list[str]) -> dict[str, int]:
    return {
        name: int(db.session.execute(text(f"SELECT count(*) FROM {qident(name)}")).scalar_one())
        for name in table_names
    }


def nullable_user_references(table_names: set[str]) -> list[tuple[str, str]]:
    rows = db.session.execute(text("""
        SELECT tc.table_name, kcu.column_name, cols.is_nullable
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
           AND tc.table_schema = kcu.table_schema
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
           AND ccu.table_schema = tc.table_schema
          JOIN information_schema.columns cols
            ON cols.table_schema = tc.table_schema
           AND cols.table_name = tc.table_name
           AND cols.column_name = kcu.column_name
         WHERE tc.constraint_type = 'FOREIGN KEY'
           AND tc.table_schema = 'public'
           AND ccu.table_name = 'users'
    """)).all()
    refs = []
    for table_name, column_name, is_nullable in rows:
        if table_name not in table_names or table_name == "users":
            continue
        if is_nullable != "YES":
            raise RuntimeError(
                f"La tabla protegida {table_name}.{column_name} exige un usuario; "
                "se rehúsa la purga para no dañar catálogo/configuración."
            )
        refs.append((table_name, column_name))
    return refs


def build_report(retention_days: int = 3) -> dict:
    inspector = inspect(db.engine)
    existing = sorted(inspector.get_table_names())
    protected = sorted(set(existing) & PROTECTED_TABLES)
    transient = sorted(set(existing) - set(protected))
    roles = dict(db.session.execute(text(
        "SELECT rol, count(*) FROM users GROUP BY rol ORDER BY rol"
    )).all())
    cutoff = utcnow() - timedelta(days=retention_days)
    recent_products = int(db.session.execute(text(
        "SELECT count(*) FROM products WHERE creado_en >= :cutoff"
    ), {"cutoff": cutoff}).scalar_one())
    return {
        "database": db.engine.url.database,
        "host": db.engine.url.host,
        "protected_counts": table_counts(protected),
        "transient_counts": table_counts(transient),
        "user_roles": roles,
        "super_admins": int(roles.get("super_admin", 0)),
        "non_super_users": int(sum(count for role, count in roles.items() if role != "super_admin")),
        "protected_tables": protected,
        "transient_tables": transient,
        "product_retention_days": retention_days,
        "product_cutoff_utc": cutoff.isoformat() + "Z",
        "recent_products_to_keep": recent_products,
        "old_products_to_delete": int(table_counts(["products"])["products"] - recent_products),
    }


def _purge_old_catalog(cutoff) -> dict:
    # Si un combo reciente contiene un producto antiguo, conservarlo dejaría
    # una propuesta comercial incompleta. La CTE propaga esa dependencia.
    db.session.execute(text("""
        CREATE TEMP TABLE purge_product_ids ON COMMIT DROP AS
        WITH RECURSIVE doomed(id) AS (
            SELECT id FROM products WHERE creado_en IS NULL OR creado_en < :cutoff
            UNION
            SELECT ci.combo_id
              FROM combo_items ci
              JOIN doomed d ON d.id = ci.producto_id
        )
        SELECT DISTINCT id FROM doomed
    """), {"cutoff": cutoff})
    doomed = int(db.session.execute(text("SELECT count(*) FROM purge_product_ids")).scalar_one())
    statements = [
        "DELETE FROM combo_item_allowed_flavors WHERE combo_item_id IN (SELECT id FROM combo_items WHERE combo_id IN (SELECT id FROM purge_product_ids) OR producto_id IN (SELECT id FROM purge_product_ids))",
        "DELETE FROM combo_item_allowed_presentations WHERE combo_item_id IN (SELECT id FROM combo_items WHERE combo_id IN (SELECT id FROM purge_product_ids) OR producto_id IN (SELECT id FROM purge_product_ids)) OR presentation_id IN (SELECT id FROM product_presentations WHERE producto_id IN (SELECT id FROM purge_product_ids))",
        "DELETE FROM product_presentation_flavors WHERE presentation_id IN (SELECT id FROM product_presentations WHERE producto_id IN (SELECT id FROM purge_product_ids))",
        "DELETE FROM product_extra_options WHERE grupo_id IN (SELECT id FROM product_extra_groups WHERE producto_id IN (SELECT id FROM purge_product_ids))",
        "DELETE FROM combo_items WHERE combo_id IN (SELECT id FROM purge_product_ids) OR producto_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM combo_groups WHERE combo_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM product_extra_groups WHERE producto_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM product_presentations WHERE producto_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM product_variants WHERE product_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM product_batches WHERE producto_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM proveedor_productos WHERE producto_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM stock WHERE producto_id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM products WHERE id IN (SELECT id FROM purge_product_ids)",
        "DELETE FROM categorias WHERE NOT EXISTS (SELECT 1 FROM products WHERE products.categoria_id = categorias.id)",
    ]
    for statement in statements:
        db.session.execute(text(statement))
    return {"products_deleted": doomed}


def purge(report: dict, keep_superadmin_email: str | None = None) -> dict:
    if report["super_admins"] < 1:
        raise RuntimeError("REHUSO: no existe ninguna cuenta super_admin que preservar.")
    if report["super_admins"] > 1 and not keep_superadmin_email:
        raise RuntimeError("REHUSO: hay varios super_admin; indica --keep-superadmin-email.")
    if keep_superadmin_email:
        matches = int(db.session.execute(text(
            "SELECT count(*) FROM users WHERE rol='super_admin' AND lower(email)=lower(:email)"
        ), {"email": keep_superadmin_email}).scalar_one())
        if matches != 1:
            raise RuntimeError("REHUSO: el super_admin indicado no existe o no es único.")
    protected = report["protected_tables"]
    transient = report["transient_tables"]
    before = dict(report["protected_counts"])

    try:
        if transient:
            names = ", ".join(qident(name) for name in transient)
            db.session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

        cutoff = report["product_cutoff_utc"].removesuffix("Z")
        catalog_result = _purge_old_catalog(cutoff)

        # Catálogo y configuración pueden conservar referencias de auditoría a
        # empleados demo. Se limpian antes de borrar esas cuentas.
        for table_name, column_name in nullable_user_references(set(protected)):
            user_filter = (
                "rol <> 'super_admin' OR lower(email) <> lower(:keep_email)"
                if keep_superadmin_email else "rol <> 'super_admin'"
            )
            db.session.execute(text(
                f"UPDATE {qident(table_name)} SET {qident(column_name)} = NULL "
                f"WHERE {qident(column_name)} IN "
                f"(SELECT id FROM users WHERE {user_filter})"
            ), {"keep_email": keep_superadmin_email})
        if keep_superadmin_email:
            deleted_users = db.session.execute(text(
                "DELETE FROM users WHERE rol <> 'super_admin' OR lower(email) <> lower(:email)"
            ), {"email": keep_superadmin_email}).rowcount
        else:
            deleted_users = db.session.execute(text(
                "DELETE FROM users WHERE rol <> 'super_admin'"
            )).rowcount
        # Los socios eliminados podían referenciar su entidad proveedora. Solo
        # después de borrar usuarios es seguro retirar proveedores huérfanos.
        db.session.execute(text("""
            DELETE FROM proveedores
             WHERE NOT EXISTS (
                       SELECT 1 FROM products
                        WHERE products.proveedor_despachador_id = proveedores.id
                   )
               AND NOT EXISTS (
                       SELECT 1 FROM proveedor_productos
                        WHERE proveedor_productos.proveedor_id = proveedores.id
                   )
        """))

        after = table_counts(protected)
        # `users` es la única protegida cuyo conteo debe cambiar.
        immutable = {"schema_migrations", "site_config", "menu_config", "knowledge_entries", "zonas_entrega"}
        unexpected = {
            name: {"before": before[name], "after": after[name]}
            for name in protected
            if name in immutable and before[name] != after[name]
        }
        if unexpected:
            raise RuntimeError(f"La purga alcanzó tablas protegidas: {unexpected}")
        expected_users = 1 if keep_superadmin_email else report["super_admins"]
        if after.get("users", 0) != expected_users:
            raise RuntimeError("El número final de usuarios no coincide con el super_admin preservado.")
        recent_after = int(db.session.execute(text(
            "SELECT count(*) FROM products WHERE creado_en >= :cutoff"
        ), {"cutoff": cutoff}).scalar_one())
        if recent_after > report["recent_products_to_keep"]:
            raise RuntimeError("El control final del catálogo produjo un conteo imposible.")
        db.session.commit()
        return {
            "ok": True,
            "deleted_users": deleted_users,
            "recent_products_kept": recent_after,
            **catalog_result,
            "final_counts": after,
        }
    except Exception:
        db.session.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--keep-superadmin-email", default="")
    parser.add_argument("--product-retention-days", type=int, default=3)
    args = parser.parse_args()

    app = create_app(os.environ.get("FLASK_ENV", "production"))
    with app.app_context():
        if args.product_retention_days != 3:
            raise SystemExit("REHUSO: la política preproducción exige exactamente 3 días.")
        report = build_report(retention_days=args.product_retention_days)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if not args.execute:
            print("DRY-RUN: no se modificó ningún registro.")
            return
        if args.confirm != CONFIRMATION:
            raise SystemExit(f"REHUSO: usa --confirm {CONFIRMATION}")
        result = purge(report, args.keep_superadmin_email.strip() or None)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
