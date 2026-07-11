#!/usr/bin/env python3
"""
Reset total para arrancar producción — deja SOLO 1 super_admin creado de cero.

CASOS DE USO
============
- Terminaste las pruebas de QA y quieres arrancar producción limpia.
- Vas a instalar Oxidian en un cliente white-label desde cero.
- Vas a cambiar de super_admin (el anterior queda eliminado).

QUÉ HACE
========
1. Vacía TODAS las tablas de datos (productos, pedidos, usuarios, config).
2. Preserva `schema_migrations` (para no re-ejecutar migraciones).
3. Crea 1 super_admin con las credenciales de las env vars.
4. Siembra la SiteConfig mínima (branding placeholder + retención de tablas).

RIESGO
======
DESTRUCTIVO E IRREVERSIBLE. Este script BORRA toda la BD.
Antes de correrlo:
  1. Haz un backup manual (`docker exec ... pg_dump ...`).
  2. Confirma que no hay pedidos en curso.
  3. Ten los datos del nuevo super_admin listos.

USO
===
Requiere 3 confirmaciones simultáneas (fail-safe contra accidentes):

  # 1. Variable de confirmación explícita
  export OXIDIAN_CONFIRM_WIPE="YES_WIPE_DATABASE_FOR_PRODUCTION"

  # 2. Credenciales del nuevo super_admin
  export SUPERADMIN_EMAIL="admin@midominio.com"
  export SUPERADMIN_PASSWORD="una-password-muy-larga-al-menos-12-chars"
  export SUPERADMIN_NAME="Nombre Real"
  export SUPERADMIN_PHONE="+34600000000"

  # 3. Correr desde el contenedor
  docker exec -it oxidian-oxidian-1 \\
      -e OXIDIAN_CONFIRM_WIPE \\
      -e SUPERADMIN_EMAIL -e SUPERADMIN_PASSWORD \\
      -e SUPERADMIN_NAME -e SUPERADMIN_PHONE \\
      python scripts/reset_para_produccion.py

O más simple: exportar y correr:

  docker exec -it oxidian-oxidian-1 python scripts/reset_para_produccion.py

(las env vars pasan si están en el entorno del container o pasadas con -e)
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIRM_TOKEN = "YES_WIPE_DATABASE_FOR_PRODUCTION"


# Tablas a truncar (mismo orden que wipe_generico.py, incluye users).
# schema_migrations NO se toca — preservamos historial de migrations.
TABLAS_WIPE = [
    "notification_outbox", "order_events", "order_provider_status",
    "order_items", "orders", "idempotency_keys", "points_log",
    "affiliate_uses", "affiliate_codes", "coupons", "caja",
    "bot_ai_message", "bot_ai_usage", "push_subscriptions",
    "campanas_marketing",
    "product_extra_options", "product_extra_groups", "extra_catalog_items",
    "product_presentations",
    "combo_items", "combo_groups", "proveedor_productos", "stock",
    "product_variants",
    "products", "categorias", "proveedores", "zonas_entrega",
    "menu_config", "audit_log", "site_config",
    "admin_features", "users",
]


# SiteConfig mínima con placeholders neutros — el super_admin la editará
# desde /superadmin/config al arrancar.
CONFIG_MINIMA = {
    # ── Marca (placeholders)
    "NOMBRE_NEGOCIO":      "Mi Negocio",
    "TELEFONO_NEGOCIO":    "",
    "DIRECCION_NEGOCIO":   "",
    # ── Modo operativo por defecto
    "MODO_TIENDA":         "propia",
    "TIPO_TIENDA":         "comida",
    # ── Features (admin apaga las que no use)
    "FEATURE_DELIVERY":    "1",
    "FEATURE_RECOGIDA":    "1",
    "FEATURE_PEDIDOS_PROGRAMADOS": "1",
    "FEATURE_PUNTOS":      "1",
    # ── Horario placeholder
    "HORARIO_APERTURA":    "09:00",
    "HORARIO_CIERRE":      "22:00",
    "PEDIDO_MINIMO":       "0.00",
    # ── Puntos (1 punto/euro; 20 pts = 1€ de descuento)
    "PUNTOS_POR_EURO":     "1",
    "PUNTOS_RATIO":        "20",
    # ── Comisión servicio (solo activa en modo bar_servicio)
    "SERVICE_COMMISSION_PCT": "0.00",
    # ── Pagos
    "PAGO_EFECTIVO_HABILITADO": "1",
    "PAGO_BIZUM_HABILITADO":    "1",
    # ── UI
    "AUTO_DESTACADOS_ENABLED": "1",
    "IA_ADMIN_LIMITE_HORA":    "30",
}


def _read_credentials():
    """Lee y valida las 4 env vars del nuevo super_admin. Falla ruidosamente."""
    email = (os.environ.get("SUPERADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("SUPERADMIN_PASSWORD") or ""
    nombre = (os.environ.get("SUPERADMIN_NAME") or "").strip()
    telefono = (os.environ.get("SUPERADMIN_PHONE") or "").strip()

    faltantes = []
    if not email:
        faltantes.append("SUPERADMIN_EMAIL")
    if not password:
        faltantes.append("SUPERADMIN_PASSWORD")
    if not nombre:
        faltantes.append("SUPERADMIN_NAME")
    if not telefono:
        faltantes.append("SUPERADMIN_PHONE")
    if faltantes:
        raise SystemExit(
            f"REHUSO: faltan credenciales del nuevo super_admin: {', '.join(faltantes)}"
        )

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise SystemExit(f"REHUSO: SUPERADMIN_EMAIL inválido: {email}")
    if len(password) < 12:
        raise SystemExit(
            "REHUSO: SUPERADMIN_PASSWORD debe tener al menos 12 caracteres."
        )
    if len(nombre) < 2 or len(nombre) > 80:
        raise SystemExit("REHUSO: SUPERADMIN_NAME debe tener 2-80 caracteres.")

    # Normalizar teléfono con el helper del propio Oxidian para coherencia.
    from phone_utils import normalizar_telefono_cliente, telefono_valido
    tn = normalizar_telefono_cliente(telefono)
    if not telefono_valido(tn):
        raise SystemExit(
            f"REHUSO: SUPERADMIN_PHONE inválido: {telefono} (usar formato E.164 +34...)"
        )

    return {"email": email, "password": password, "nombre": nombre, "telefono": tn}


def _confirmar():
    """Triple guard: env token + prompt interactivo + summary del daño."""
    if os.environ.get("OXIDIAN_CONFIRM_WIPE") != CONFIRM_TOKEN:
        raise SystemExit(
            f"REHUSO: OXIDIAN_CONFIRM_WIPE debe valer exactamente '{CONFIRM_TOKEN}'.\n"
            "Este script es DESTRUCTIVO. Setea la variable si estás seguro."
        )

    if os.environ.get("FLASK_ENV", "").lower() in ("test", "testing"):
        raise SystemExit("REHUSO: no ejecutar en entorno de tests.")


def wipe_y_crear_superadmin(creds):
    from app import create_app
    from extensions import db
    from models import User, SiteConfig
    from sqlalchemy import text

    app = create_app()
    with app.app_context():
        # ── Snapshot pre-wipe para el log
        prev_users = User.query.count()
        prev_productos = _safe_count("products", db)
        prev_pedidos = _safe_count("orders", db)

        print()
        print("=" * 68)
        print("RESET PARA PRODUCCIÓN — resumen del daño:")
        print(f"  Users existentes:      {prev_users} (se borran TODOS)")
        print(f"  Products existentes:   {prev_productos} (se borran)")
        print(f"  Orders existentes:     {prev_pedidos} (se borran)")
        print(f"  → nuevo super_admin:   {creds['email']} ({creds['telefono']})")
        print("=" * 68)
        print()

        # ── Truncate TODAS las tablas de datos, RESTART IDENTITY, CASCADE FKs.
        # schema_migrations NO está en la lista → migraciones intactas.
        print("[1/4] TRUNCATE de tablas de datos…")
        db.session.execute(text(
            "TRUNCATE " + ", ".join(TABLAS_WIPE) + " RESTART IDENTITY CASCADE"
        ))
        db.session.commit()

        # ── Crear super_admin fresco
        print(f"[2/4] Creando super_admin fresco ({creds['email']})…")
        u = User(
            nombre=creds["nombre"],
            email=creds["email"],
            telefono=creds["telefono"],
            telefono_normalizado=creds["telefono"],
            rol="super_admin",
            activo=True,
            puntos=0,
        )
        u.set_password(creds["password"])
        db.session.add(u)
        db.session.commit()

        # ── SiteConfig mínima
        print("[3/4] Sembrando SiteConfig mínima…")
        for clave, valor in CONFIG_MINIMA.items():
            db.session.add(SiteConfig(clave=clave, valor=valor))
        db.session.commit()

        # ── Sembrar defaults nuevos (retención, cli page size, etc.)
        print("[4/4] Sembrando defaults de retención (PR #17) y otros…")
        try:
            from config_defaults import sembrar_defaults
            nuevas = sembrar_defaults()
            db.session.commit()
            print(f"       → {nuevas} claves adicionales sembradas.")
        except Exception as exc:
            db.session.rollback()
            print(f"       ⚠ config_defaults.sembrar_defaults falló: {exc}")

        # ── Resumen final
        print()
        print("=" * 68)
        print("✓ BD lista para producción:")
        print(f"  Users totales:         {User.query.count()} (solo el super_admin)")
        print(f"  SiteConfig entries:    {SiteConfig.query.count()}")
        print(f"  Products / Orders:     0 / 0")
        print()
        print(f"  Login como super_admin:")
        print(f"    URL:      /auth/login")
        print(f"    Email:    {creds['email']}")
        print(f"    Teléfono: {creds['telefono']}")
        print(f"    Password: (la que pasaste en SUPERADMIN_PASSWORD)")
        print()
        print("  Próximos pasos desde el panel:")
        print("    1. /superadmin/config — datos del negocio, teléfono, ciudad")
        print("    2. /superadmin/zonas — configurar zonas de entrega")
        print("    3. /admin/categorias — categorías del catálogo")
        print("    4. /admin/productos — dar de alta productos")
        print("    5. /admin/telefonos — cross-check env↔BD (env solo whitelist)")
        print("=" * 68)


def _safe_count(tabla, db):
    from sqlalchemy import text
    try:
        return db.session.execute(text(f"SELECT COUNT(*) FROM {tabla}")).scalar()
    except Exception:
        return "?"


def main():
    _confirmar()
    creds = _read_credentials()
    wipe_y_crear_superadmin(creds)


if __name__ == "__main__":
    main()
