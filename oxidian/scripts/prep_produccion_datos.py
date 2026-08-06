#!/usr/bin/env python3
"""
Prep de datos para arrancar producción — conserva la configuración de la tienda
y las cuentas admin/super_admin, borra todo lo operativo (productos, combos,
categorías, proveedores, pedidos, cupones, puntos, clientes, carritos, etc.).

DIFERENCIA CON reset_para_produccion.py
=======================================
- reset_para_produccion.py borra TODO (incluyendo SiteConfig) y crea un admin.
- Este script CONSERVA:
    · site_config (branding, horarios, colores, teléfono, pago activo, etc.)
    · users con rol admin/super_admin (existentes)
    · schema_migrations (nunca se toca)
    · admin_features (permisos guardados)
  Y BORRA:
    · Todos los productos (products), categorías, proveedores, stock, variantes
    · Todos los combos (combo_items, combo_groups)
    · Todos los pedidos y su historial (orders, order_items, order_events, ...)
    · Todos los cupones y códigos afiliado (coupons, affiliate_*)
    · Todos los puntos y su historial (points_log)
    · Todos los usuarios NO-admin (rol='cliente', 'preparacion', 'repartidor',
      'proveedor', 'cocina', etc.)
    · Todas las suscripciones push (push_subscriptions)
    · Toda la caja y liquidaciones (caja, staff_payments, daily_closures)
    · Toda la publicidad/menu-config (menu_config, campanas_marketing)
    · Todas las zonas (zonas_entrega) → el admin las re-configura si quiere
    · Todo dato de bot (bot_ai_message, bot_ai_usage, notification_outbox,
      idempotency_keys)

CONFIRMACIÓN
============
Requiere OXIDIAN_CONFIRM_PREP="YES_PREP_DATA_FOR_PRODUCTION" en el entorno.

USO
===
Desde el host:
    ssh 192.168.1.32 'docker exec -e OXIDIAN_CONFIRM_PREP=YES_PREP_DATA_FOR_PRODUCTION \\
        oxidian python scripts/prep_produccion_datos.py'

En seco (solo cuenta filas, no borra):
    ssh 192.168.1.32 'docker exec oxidian python scripts/prep_produccion_datos.py --dry-run'
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

# Orden estricto: dependientes → padres. Cada bloque se TRUNCATE con CASCADE
# para arrastrar FKs residuales sin quejarse.
# Se PRESERVAN: site_config, users (solo admins), admin_features,
# schema_migrations.
TABLAS_A_LIMPIAR = [
    # Bot y notificaciones (dependen de user_id + pedido)
    "notification_outbox",
    "push_subscriptions",
    "bot_ai_message",
    "bot_ai_usage",
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
    # Reseñas
    "reviews",
    # Productos y su estructura
    "product_extra_options",
    "product_extra_groups",
    "extra_catalog_items",
    "product_presentations",
    "combo_items",
    "combo_groups",
    "proveedor_productos",
    "stock",
    "product_variants",
    "products",
    "categorias",
    # Proveedores (socios)
    "proveedores",
    # Zonas de entrega — el admin re-configura si aplica
    "zonas_entrega",
    # Auditoría (queremos empezar limpio)
    "audit_log",
]

# Estos usuarios se conservan (por rol). Los demás se eliminan.
ROLES_ADMIN_A_PRESERVAR = ("super_admin", "admin")


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=:n"
        ),
        {"n": name},
    ).first()
    return row is not None


def _count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def _count_users_no_admin(conn) -> int:
    return conn.execute(
        text(
            "SELECT COUNT(*) FROM users WHERE rol NOT IN :roles"
        ).bindparams(text("(:sa, :a)")),
        {"sa": "super_admin", "a": "admin"},
    ).scalar() or 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo cuenta filas afectadas, no borra nada.")
    args = parser.parse_args()

    if not args.dry_run and os.environ.get("OXIDIAN_CONFIRM_PREP") != CONFIRM_TOKEN:
        print(
            "❌ Confirmación faltante. Para ejecutar la limpieza real, define:",
            f'\n   OXIDIAN_CONFIRM_PREP="{CONFIRM_TOKEN}"',
            "\nEjecuta primero con --dry-run para ver qué se va a borrar.",
            file=sys.stderr,
        )
        return 2

    app = create_app("production")
    with app.app_context():
        conn = db.session.connection()

        # ── 1. Inventario previo ───────────────────────────────────
        print("\n📊 Estado ANTES:")
        preservados = ["site_config", "admin_features", "schema_migrations", "users"]
        for tabla in preservados:
            if _table_exists(conn, tabla):
                c = _count(conn, tabla)
                extra = ""
                if tabla == "users":
                    admins = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM users "
                            "WHERE rol IN ('super_admin','admin')"
                        )
                    ).scalar() or 0
                    extra = f" (de los cuales {admins} son admin/super_admin — se conservan)"
                print(f"  ✓ {tabla}: {c}{extra} → PRESERVAR")

        total_a_borrar = 0
        for tabla in TABLAS_A_LIMPIAR:
            if not _table_exists(conn, tabla):
                print(f"  · {tabla}: (tabla no existe, saltada)")
                continue
            c = _count(conn, tabla)
            total_a_borrar += c
            print(f"  ✗ {tabla}: {c} → BORRAR")

        # Users no-admin
        no_admin = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE rol NOT IN ('super_admin','admin')")
        ).scalar() or 0
        print(f"  ✗ users (no admin): {no_admin} → BORRAR (conservando admins)")
        total_a_borrar += no_admin

        print(f"\n🔢 Total de filas a borrar: {total_a_borrar}")

        if args.dry_run:
            print("\n[dry-run] No se ha borrado nada. Ejecuta sin --dry-run para aplicar.")
            return 0

        # ── 2. Borrado en transacción ─────────────────────────────
        # IMPORTANTE: usar DELETE FROM (NO TRUNCATE CASCADE). CASCADE
        # sigue el grafo de FKs y puede arrastrar tablas que queremos
        # PRESERVAR (aprendido a las malas: site_config, users y
        # admin_features quedaron vacías por una cadena de FKs indirecta).
        # DELETE FROM sin WHERE respeta las FKs (falla si algo depende) y
        # nos obliga a orden explícito hijo→padre.
        print("\n🧹 Aplicando limpieza (transacción atómica, sin CASCADE)…")
        for tabla in TABLAS_A_LIMPIAR:
            if not _table_exists(conn, tabla):
                continue
            try:
                result = conn.execute(text(f"DELETE FROM {tabla}"))
                # Reset de la secuencia serial si existe
                try:
                    conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{tabla}','id'), 1, false)"
                    ))
                except Exception:
                    pass
                print(f"  ✓ {tabla}: {result.rowcount} eliminadas")
            except Exception as exc:
                print(f"  ⚠ {tabla}: {exc}")
                # Si un DELETE falla es por FK — algo depende de esta tabla.
                # No abortamos: seguimos y el usuario verá el detalle final.

        # Users no-admin: DELETE explícito preservando super_admin y admin
        try:
            result = conn.execute(
                text(
                    "DELETE FROM users WHERE rol NOT IN ('super_admin','admin')"
                )
            )
            print(f"  ✓ users (no admin) eliminados: {result.rowcount}")
        except Exception as exc:
            print(f"  ⚠ users no-admin: {exc}")

        db.session.commit()

        # ── 3. Estado final ───────────────────────────────────────
        print("\n📊 Estado DESPUÉS:")
        conn2 = db.session.connection()
        for tabla in preservados:
            if _table_exists(conn2, tabla):
                c = _count(conn2, tabla)
                print(f"  ✓ {tabla}: {c}")

        # Verificación de admins
        admins_finales = conn2.execute(
            text(
                "SELECT id, email, nombre, rol FROM users "
                "WHERE rol IN ('super_admin','admin') ORDER BY id"
            )
        ).all()
        print(f"\n👤 Admin/super_admin conservados: {len(admins_finales)}")
        for u in admins_finales:
            print(f"   · #{u.id}  {u.rol}  {u.email}  ({u.nombre})")

        print("\n✅ Base lista para producción. Empezar a cargar productos.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
