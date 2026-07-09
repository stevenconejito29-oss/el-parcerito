#!/usr/bin/env python3
"""
Wipe COMPLETO y GENÉRICO — deja la BD lista para vender el sistema como
servicio white-label. Cada bar/negocio configurará su nombre, catálogo,
zonas y textos desde el panel admin.

Preserva:
- super_admins (para no perder acceso)
- schema_migrations

Restaura una config MÍNIMA con placeholders neutros. NO siembra productos,
combos, categorías ni proveedores. NO menciona "El Parcerito" ni ninguna
ciudad concreta.

Uso: docker exec oxidian python3 scripts/wipe_generico.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import User, SiteConfig, AdminFeature


TABLAS_WIPE = [
    "notification_outbox", "order_events", "order_provider_status",
    "order_items", "orders", "idempotency_keys", "points_log",
    "affiliate_uses", "affiliate_codes", "coupons", "caja",
    "bot_ai_usage", "push_subscriptions", "campanas_marketing",
    "product_extra_options", "product_extra_groups", "extra_catalog_items",
    "product_presentations",
    "combo_items", "combo_groups", "proveedor_productos", "stock",
    "products", "categorias", "proveedores", "zonas_entrega",
    "menu_config", "audit_log", "site_config",
    "admin_features", "users",
]


CONFIG_MINIMA = {
    # Marca — placeholders neutros. El admin los cambia en /superadmin/config.
    "NOMBRE_NEGOCIO":      "Mi Negocio",
    "TELEFONO_NEGOCIO":    "",
    "DIRECCION_NEGOCIO":   "",
    # Modo operativo por defecto
    "MODO_TIENDA":         "propia",
    "TIPO_TIENDA":         "comida",
    # Features todas ON por default (el admin apaga las que no use)
    "FEATURE_DELIVERY":    "1",
    "FEATURE_RECOGIDA":    "1",
    "FEATURE_PEDIDOS_PROGRAMADOS": "1",
    "FEATURE_PUNTOS":      "1",
    # Horario placeholder — el admin ajusta
    "HORARIO_APERTURA":    "09:00",
    "HORARIO_CIERRE":      "22:00",
    "PEDIDO_MINIMO":       "0.00",
    # Puntos: 1 punto por euro, ratio 20 pts = 1€ (configurable)
    "PUNTOS_POR_EURO":     "1",
    "PUNTOS_RATIO":        "20",
    # Comisión servicio (solo se aplica en modo bar_servicio)
    "SERVICE_COMMISSION_PCT": "0.00",
    # Pagos por default ambos ON
    "PAGO_EFECTIVO_HABILITADO": "1",
    "PAGO_BIZUM_HABILITADO":    "1",
    # Auto-destacados en home
    "AUTO_DESTACADOS_ENABLED": "1",
    # Guard: IA analítica limitada por defecto
    "IA_ADMIN_LIMITE_HORA":    "30",
}


def wipe():
    print("• Wipe TOTAL (preserva super_admins + schema_migrations)")
    supers = []
    for u in User.query.filter_by(rol="super_admin").all():
        supers.append({
            "nombre": u.nombre, "email": u.email,
            "telefono": u.telefono, "telefono_normalizado": u.telefono_normalizado,
            "rol": u.rol, "password_hash": u.password_hash,
            "activo": u.activo, "puntos": u.puntos or 0,
            "mfa_secret": getattr(u, "mfa_secret", None),
            "mfa_enabled": getattr(u, "mfa_enabled", False),
        })
    if not supers:
        raise SystemExit("REHUSO: no hay super_admin. Wipe cancelaría el acceso.")

    from sqlalchemy import text
    db.session.execute(text(
        "TRUNCATE " + ", ".join(TABLAS_WIPE) + " RESTART IDENTITY CASCADE"
    ))
    db.session.commit()

    for snap in supers:
        u = User(
            nombre=snap["nombre"], email=snap["email"],
            telefono=snap["telefono"], telefono_normalizado=snap["telefono_normalizado"],
            rol=snap["rol"], activo=snap["activo"], puntos=snap["puntos"],
        )
        u.password_hash = snap["password_hash"]
        if snap.get("mfa_secret"):
            u.mfa_secret = snap["mfa_secret"]
            u.mfa_enabled = snap.get("mfa_enabled", False)
        db.session.add(u)
    db.session.commit()
    print(f"  - super_admins re-insertados: {len(supers)}")


def restaurar_config():
    print("• SiteConfig mínima (sin nombres, sin ciudad)")
    for clave, valor in CONFIG_MINIMA.items():
        db.session.add(SiteConfig(clave=clave, valor=valor))
    db.session.commit()


def main():
    app = create_app()
    with app.app_context():
        wipe()
        restaurar_config()
        db.session.commit()
        print()
        print("=" * 60)
        print("✓ BD lista para white-label:")
        print(f"  Users: {User.query.count()} (solo super_admins)")
        print(f"  SiteConfig: {SiteConfig.query.count()} claves")
        print(f"  Productos / combos / categorías / zonas: 0")
        print("  → El admin de cada bar configura desde /superadmin/config")
        print("=" * 60)


if __name__ == "__main__":
    main()
