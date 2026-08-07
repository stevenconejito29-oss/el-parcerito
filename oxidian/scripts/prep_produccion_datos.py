#!/usr/bin/env python3
"""
Prep de datos para arrancar producción — conserva la configuración de la tienda
y las cuentas admin/super_admin, borra todo lo operativo.

APPROACH PROFESIONAL
====================
Usa `SET session_replication_role = 'replica'` (requiere SUPERUSER) para
desactivar los triggers de foreign key durante el wipe. Es el mismo mecanismo
que usa pg_restore para restaurar dumps con dependencias circulares. Con FKs
desactivados el orden de DELETE no importa y no hay riesgo de CASCADE
accidental (a diferencia de TRUNCATE ... CASCADE, que sigue el grafo de FKs
y arrastra tablas que queríamos preservar).

CONSERVA:
    · site_config (branding, horarios, colores, teléfono, pago, features)
    · users con rol admin y super_admin (y sus admin_features)
    · schema_migrations

BORRA:
    · Todos los productos, categorías, proveedores, stock, variantes, combos
    · Todos los pedidos y su historial
    · Todos los cupones y códigos afiliado
    · Todos los puntos, caja, staff_payments, daily_closures
    · Toda la publicidad (menu_config, campanas_marketing) y reviews
    · Todo dato de bot (notification_outbox, push_*, bot_ai_*, idempotency_keys)
    · Todas las zonas y auditoría
    · Todos los usuarios NO admin (clientes, staff, proveedores, cocina, etc.)

CONFIRMACIÓN
============
Requiere OXIDIAN_CONFIRM_PREP="YES_PREP_DATA_FOR_PRODUCTION" en el entorno.

USO
===
Dry-run (solo cuenta, no borra):
    docker exec oxidian python scripts/prep_produccion_datos.py --dry-run

Real:
    docker exec -e OXIDIAN_CONFIRM_PREP=YES_PREP_DATA_FOR_PRODUCTION \\
      oxidian python scripts/prep_produccion_datos.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app import create_app
from extensions import db


CONFIRM_TOKEN = "YES_PREP_DATA_FOR_PRODUCTION"

# Tablas operativas a vaciar. Con session_replication_role='replica' el
# ORDEN NO IMPORTA — solo debe ser completa. Cualquier tabla omitida quedará
# con datos huérfanos que pueden violar FKs al reactivar los triggers.
# Actualizada con TODAS las tablas hijas descubiertas via information_schema.
TABLAS_A_LIMPIAR = [
    # Bot y notificaciones
    "notification_outbox",
    "push_subscriptions",
    "push_broadcasts",
    "bot_ai_usage",
    "knowledge_entries",
    "idempotency_keys",
    # Pedidos y todo su historial
    "order_events",
    "order_provider_status",
    "order_items",
    "orders",
    # Fidelidad
    "points_log",
    # Afiliados y cupones
    "affiliate_uses",
    "affiliate_codes",
    "coupons",
    # Caja y finanzas
    "caja",
    "staff_payments",
    "daily_closures",
    # Publicidad / menú de home
    "menu_config",
    "campanas_marketing",
    # Reseñas y precio histórico
    "reviews",
    "price_history",
    # Productos y su estructura
    "product_extra_options",
    "product_extras",
    "product_extra_groups",
    "extra_catalog_items",
    "product_presentations",
    "product_batches",
    "combo_items",
    "combo_groups",
    "proveedor_productos",
    "stock",
    "product_variants",
    "products",
    "categorias",
    # Proveedores
    "proveedores",
    # Zonas
    "zonas_entrega",
    # Auditoría
    "audit_log",
]

PRESERVAR = ["site_config", "admin_features", "schema_migrations", "users"]
ROLES_ADMIN = ("super_admin", "admin")


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=:n"
        ),
        {"n": name},
    ).first() is not None


def _count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and os.environ.get("OXIDIAN_CONFIRM_PREP") != CONFIRM_TOKEN:
        print(f'❌ Falta confirmación. Define OXIDIAN_CONFIRM_PREP="{CONFIRM_TOKEN}"',
              file=sys.stderr)
        return 2

    app = create_app("production")
    with app.app_context():
        conn = db.session.connection()

        # ── 1. Inventario previo ───────────────────────────────────
        print("\n📊 Estado ANTES:")
        for tabla in PRESERVAR:
            if _table_exists(conn, tabla):
                c = _count(conn, tabla)
                extra = ""
                if tabla == "users":
                    admins = conn.execute(
                        text("SELECT COUNT(*) FROM users WHERE rol IN ('super_admin','admin')")
                    ).scalar() or 0
                    extra = f" (admins: {admins})"
                print(f"  ✓ {tabla}: {c}{extra} → PRESERVAR")

        total = 0
        for tabla in TABLAS_A_LIMPIAR:
            if not _table_exists(conn, tabla):
                print(f"  · {tabla}: (no existe, saltar)")
                continue
            c = _count(conn, tabla)
            total += c
            print(f"  ✗ {tabla}: {c}")
        no_admin = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE rol NOT IN ('super_admin','admin')")
        ).scalar() or 0
        print(f"  ✗ users (no admin): {no_admin}")
        total += no_admin
        print(f"\n🔢 Total a borrar: {total}")

        if args.dry_run:
            print("\n[dry-run] No se ha borrado nada.")
            return 0

        # ── 2. Wipe con FK triggers desactivados ──────────────────
        print("\n🧹 Aplicando limpieza (FK triggers off, misma transacción)…")
        try:
            conn.execute(text("SET session_replication_role = 'replica'"))
        except Exception as exc:
            print(f"❌ No se pudo desactivar FK triggers: {exc}", file=sys.stderr)
            db.session.rollback()
            return 3

        # SAVEPOINT por DELETE: si uno falla no aborta la transacción entera.
        # Sin esto, un error "current transaction is aborted" impide ejecutar
        # el resto del wipe (incluido el SET session_replication_role='origin'
        # del finally, que también falla).
        fallidas = []
        try:
            for tabla in TABLAS_A_LIMPIAR:
                if not _table_exists(conn, tabla):
                    continue
                sp = conn.begin_nested()  # SAVEPOINT
                try:
                    result = conn.execute(text(f"DELETE FROM {tabla}"))
                    try:
                        conn.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{tabla}','id'), 1, false) "
                            f"WHERE pg_get_serial_sequence('{tabla}','id') IS NOT NULL"
                        ))
                    except Exception:
                        pass
                    sp.commit()
                    print(f"  ✓ {tabla}: {result.rowcount} eliminadas")
                except Exception as exc:
                    sp.rollback()
                    msg = str(exc).splitlines()[0][:200]
                    print(f"  ⚠ {tabla}: {msg}")
                    fallidas.append((tabla, msg))

            # NULL a FKs colgantes en admins ANTES de reactivar triggers
            for col in ("proveedor_id", "zona_id", "preparador_default_id",
                        "repartidor_default_id"):
                sp = conn.begin_nested()
                try:
                    conn.execute(text(
                        f"UPDATE users SET {col} = NULL "
                        f"WHERE {col} IS NOT NULL "
                        f"  AND rol IN ('super_admin','admin')"
                    ))
                    sp.commit()
                except Exception:
                    sp.rollback()

            # Users no-admin
            sp = conn.begin_nested()
            try:
                res = conn.execute(
                    text("DELETE FROM users WHERE rol NOT IN ('super_admin','admin')")
                )
                sp.commit()
                print(f"  ✓ users (no admin): {res.rowcount} eliminados")
            except Exception as exc:
                sp.rollback()
                msg = str(exc).splitlines()[0][:200]
                print(f"  ⚠ users (no admin): {msg}")
                fallidas.append(("users (no admin)", msg))

            # Reintento de tablas fallidas (una tabla pudo fallar por FK a
            # otra que se limpia después — al reintentar ya está vacía).
            if fallidas:
                print(f"\n🔁 Reintentando {len(fallidas)} tabla(s) fallida(s)…")
                fallidas_retry = []
                for tabla, _ in fallidas:
                    if tabla == "users (no admin)":
                        query = "DELETE FROM users WHERE rol NOT IN ('super_admin','admin')"
                    else:
                        query = f"DELETE FROM {tabla}"
                    sp = conn.begin_nested()
                    try:
                        result = conn.execute(text(query))
                        sp.commit()
                        print(f"  ✓ {tabla}: {result.rowcount} eliminadas (retry)")
                    except Exception as exc:
                        sp.rollback()
                        msg = str(exc).splitlines()[0][:200]
                        print(f"  ✗ {tabla}: sigue fallando — {msg}")
                        fallidas_retry.append(tabla)
                if fallidas_retry:
                    print(f"\n❌ Fallan tras retry: {fallidas_retry}")
                    print("   Restaura backup y ajusta script/orden.")

        finally:
            try:
                conn.execute(text("SET session_replication_role = 'origin'"))
            except Exception:
                pass

        db.session.commit()

        # ── 3. Verificación final ─────────────────────────────────
        print("\n📊 Estado DESPUÉS:")
        conn2 = db.session.connection()
        for tabla in PRESERVAR:
            if _table_exists(conn2, tabla):
                print(f"  ✓ {tabla}: {_count(conn2, tabla)}")

        admins = conn2.execute(
            text("SELECT id, email, nombre, rol FROM users "
                 "WHERE rol IN ('super_admin','admin') ORDER BY id")
        ).all()
        print(f"\n👤 Admins conservados: {len(admins)}")
        for u in admins:
            print(f"   · #{u.id}  {u.rol:12s}  {u.email:35s}  ({u.nombre})")

        # Verificación de integridad: cuenta filas huérfanas por FK.
        # Introspección real desde information_schema (no asumimos nombres de
        # columna: los descubrimos). Si tras el wipe las tablas operativas
        # están vacías, no habrá huérfanos posibles.
        try:
            fks = conn2.execute(text(
                "SELECT tc.table_name AS t, kcu.column_name AS c, "
                "       ccu.table_name AS ref "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu USING (constraint_name, table_schema) "
                "JOIN information_schema.constraint_column_usage ccu USING (constraint_name, table_schema) "
                "WHERE tc.constraint_type = 'FOREIGN KEY' "
                "  AND tc.table_schema = 'public' "
                "  AND ccu.column_name = 'id'"
            )).all()
            orphans = 0
            for fk in fks:
                if not _table_exists(conn2, fk.t) or not _table_exists(conn2, fk.ref):
                    continue
                try:
                    q = conn2.execute(text(
                        f"SELECT COUNT(*) FROM {fk.t} a "
                        f"WHERE a.{fk.c} IS NOT NULL "
                        f"  AND NOT EXISTS (SELECT 1 FROM {fk.ref} b WHERE b.id = a.{fk.c})"
                    )).scalar() or 0
                    if q:
                        orphans += q
                        print(f"  ⚠ {fk.t}.{fk.c} → {fk.ref}: {q} huérfanas")
                except Exception:
                    pass  # Columna con tipo raro o sin id — omitir
            if orphans == 0:
                print("\n✅ Integridad OK — base lista para cargar productos.")
            else:
                print(f"\n⚠ {orphans} referencias huérfanas — revisar.")
        except Exception as exc:
            print(f"\n(verificación de integridad omitida: {exc})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
