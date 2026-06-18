"""Migraciones de esquema idempotentes para despliegues sin Alembic.

No sustituye una adopcion futura de Alembic, pero evita depender solo de
`db.create_all()` para tablas nuevas criticas en Cosmos.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Añadir la raíz del proyecto al path para que funcione tanto desde scripts/ como desde /app/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from app import create_app
from extensions import db
from models import (
    ComboGroup,
    IdempotencyKey,
    NotificationOutbox,
    OrderEvent,
    OrderProviderStatus,
    Proveedor,
    ProveedorProducto,
)


def _migrate_site_config_valor_text():
    inspector = inspect(db.engine)
    if not inspector.has_table("site_config"):
        return
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        db.session.execute(text("ALTER TABLE site_config ALTER COLUMN valor TYPE TEXT"))
    elif dialect == "mysql":
        db.session.execute(text("ALTER TABLE site_config MODIFY valor TEXT"))


def _migrate_financial_uniqueness():
    if db.engine.dialect.name != "postgresql":
        return
    db.session.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_payment_delivery_commission
        ON staff_payments (user_id, tipo, pedido_id)
        WHERE tipo = 'comision' AND pedido_id IS NOT NULL
    """))
    db.session.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_caja_order_income
        ON caja (pedido_id)
        WHERE tipo = 'ingreso' AND pedido_id IS NOT NULL
    """))
    db.session.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_caja_staff_payment_expense
        ON caja (staff_payment_id)
        WHERE tipo = 'egreso' AND staff_payment_id IS NOT NULL
    """))
    db.session.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_points_log_order_earned
        ON points_log (cliente_id, pedido_id, tipo)
        WHERE tipo = 'ganado' AND pedido_id IS NOT NULL
    """))


def _migrate_affiliate_payment_integrity():
    inspector = inspect(db.engine)
    if not inspector.has_table("staff_payments") or not inspector.has_table("affiliate_uses"):
        return

    staff_columns = {col["name"] for col in inspector.get_columns("staff_payments")}
    if "origen" not in staff_columns:
        db.session.execute(text(
            "ALTER TABLE staff_payments ADD COLUMN origen VARCHAR(30) NOT NULL DEFAULT 'manual'"
        ))
    db.session.execute(text("""
        UPDATE staff_payments
        SET origen = CASE
            WHEN concepto LIKE 'Comisión afiliado %' THEN 'affiliate'
            WHEN concepto LIKE 'Reparto cobrado %' THEN 'delivery'
            ELSE COALESCE(NULLIF(origen, ''), 'manual')
        END
        WHERE tipo = 'comision'
    """))

    use_columns = {col["name"] for col in inspector.get_columns("affiliate_uses")}
    if "staff_payment_id" not in use_columns:
        db.session.execute(text(
            "ALTER TABLE affiliate_uses ADD COLUMN staff_payment_id INTEGER "
            "REFERENCES staff_payments(id)"
        ))
    db.session.execute(text("""
        UPDATE affiliate_uses au
        SET staff_payment_id = sp.id
        FROM affiliate_codes ac
        JOIN staff_payments sp
          ON sp.user_id = ac.user_id
         AND sp.pedido_id IS NOT NULL
         AND sp.tipo = 'comision'
         AND sp.origen = 'affiliate'
        WHERE au.codigo_id = ac.id
          AND au.pedido_id = sp.pedido_id
          AND au.staff_payment_id IS NULL
    """))

    if db.engine.dialect.name == "postgresql":
        duplicates = db.session.execute(text("""
            SELECT codigo_id, pedido_id
            FROM affiliate_uses
            GROUP BY codigo_id, pedido_id
            HAVING COUNT(*) > 1
            LIMIT 1
        """)).first()
        if duplicates:
            raise RuntimeError("Hay usos de afiliado duplicados; corrígelos antes de migrar")
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_affiliate_use_order "
            "ON affiliate_uses (codigo_id, pedido_id)"
        ))
        db.session.execute(text(
            "DROP INDEX IF EXISTS uq_staff_payment_delivery_commission"
        ))
        db.session.execute(text("""
            CREATE UNIQUE INDEX uq_staff_payment_delivery_commission
            ON staff_payments (user_id, origen, pedido_id)
            WHERE tipo = 'comision' AND pedido_id IS NOT NULL
        """))
        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_caja_order_refund
            ON caja (pedido_id)
            WHERE tipo = 'egreso' AND categoria = 'devolucion' AND pedido_id IS NOT NULL
        """))


def _migrate_products_combo_pricing():
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    existing = {col["name"] for col in inspector.get_columns("products")}
    column_sql = {
        "combo_precio_modo": "ALTER TABLE products ADD COLUMN combo_precio_modo VARCHAR(30) NOT NULL DEFAULT 'fijo'",
        "combo_descuento_pct": "ALTER TABLE products ADD COLUMN combo_descuento_pct NUMERIC(5,2) NOT NULL DEFAULT 0",
        "combo_precio_base": "ALTER TABLE products ADD COLUMN combo_precio_base NUMERIC(10,2) NOT NULL DEFAULT 0",
    }
    for column_name, ddl in column_sql.items():
        if column_name not in existing:
            db.session.execute(text(ddl))


def _migrate_combo_groups_structure():
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    db.metadata.create_all(bind=db.engine, tables=[ComboGroup.__table__], checkfirst=True)

    if inspector.has_table("combo_items"):
        existing = {col["name"] for col in inspector.get_columns("combo_items")}
        column_sql = {
            "combo_group_id": "ALTER TABLE combo_items ADD COLUMN combo_group_id INTEGER",
            "orden": "ALTER TABLE combo_items ADD COLUMN orden INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, ddl in column_sql.items():
            if column_name not in existing:
                db.session.execute(text(ddl))

    db.session.execute(text("""
        INSERT INTO combo_groups (combo_id, nombre, tipo, min_selecciones, max_selecciones, orden, requerido, descripcion, creado_en)
        SELECT p.id, 'Base incluida', 'fijo', 0, 1, 0, TRUE, NULL, CURRENT_TIMESTAMP
        FROM products p
        WHERE p.es_combo = TRUE
          AND EXISTS (
              SELECT 1 FROM combo_items ci
              WHERE ci.combo_id = p.id AND COALESCE(ci.es_seleccionable, FALSE) = FALSE
          )
          AND NOT EXISTS (
              SELECT 1 FROM combo_groups cg
              WHERE cg.combo_id = p.id AND cg.tipo = 'fijo'
          )
    """))
    db.session.execute(text("""
        INSERT INTO combo_groups (combo_id, nombre, tipo, min_selecciones, max_selecciones, orden, requerido, descripcion, creado_en)
        SELECT ci.combo_id,
               COALESCE(NULLIF(ci.grupo_seleccion, ''), 'Eleccion'),
               'seleccion',
               1,
               GREATEST(1, MAX(COALESCE(ci.max_selecciones, 1))),
               10 + ROW_NUMBER() OVER (PARTITION BY ci.combo_id ORDER BY COALESCE(NULLIF(ci.grupo_seleccion, ''), 'Eleccion')),
               TRUE,
               NULL,
               CURRENT_TIMESTAMP
        FROM combo_items ci
        JOIN products p ON p.id = ci.combo_id
        WHERE p.es_combo = TRUE
          AND COALESCE(ci.es_seleccionable, FALSE) = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM combo_groups cg
              WHERE cg.combo_id = ci.combo_id
                AND cg.tipo = 'seleccion'
                AND LOWER(cg.nombre) = LOWER(COALESCE(NULLIF(ci.grupo_seleccion, ''), 'Eleccion'))
          )
        GROUP BY ci.combo_id, COALESCE(NULLIF(ci.grupo_seleccion, ''), 'Eleccion')
    """))
    db.session.execute(text("""
        UPDATE combo_items ci
        SET combo_group_id = cg.id
        FROM combo_groups cg
        WHERE ci.combo_group_id IS NULL
          AND ci.combo_id = cg.combo_id
          AND COALESCE(ci.es_seleccionable, FALSE) = FALSE
          AND cg.tipo = 'fijo'
    """))
    db.session.execute(text("""
        UPDATE combo_items ci
        SET combo_group_id = cg.id
        FROM combo_groups cg
        WHERE ci.combo_group_id IS NULL
          AND ci.combo_id = cg.combo_id
          AND COALESCE(ci.es_seleccionable, FALSE) = TRUE
          AND cg.tipo = 'seleccion'
          AND LOWER(cg.nombre) = LOWER(COALESCE(NULLIF(ci.grupo_seleccion, ''), 'Eleccion'))
    """))


def _migrate_empty_combo_groups():
    _migrate_combo_groups_structure()
    db.session.execute(text("""
        INSERT INTO combo_groups (combo_id, nombre, tipo, min_selecciones, max_selecciones, orden, requerido, descripcion, creado_en)
        SELECT p.id, 'Base incluida', 'fijo', 0, 1, 0, TRUE, NULL, CURRENT_TIMESTAMP
        FROM products p
        WHERE p.es_combo = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM combo_groups cg WHERE cg.combo_id = p.id
          )
    """))


def _migrate_combo_item_option_fields():
    inspector = inspect(db.engine)
    if not inspector.has_table("combo_items"):
        return
    existing = {col["name"] for col in inspector.get_columns("combo_items")}
    column_sql = {
        "precio_extra": "ALTER TABLE combo_items ADD COLUMN precio_extra NUMERIC(10,2) NOT NULL DEFAULT 0",
        "es_predeterminado": "ALTER TABLE combo_items ADD COLUMN es_predeterminado BOOLEAN DEFAULT FALSE",
        "activo": "ALTER TABLE combo_items ADD COLUMN activo BOOLEAN DEFAULT TRUE",
        "notas_preparacion": "ALTER TABLE combo_items ADD COLUMN notas_preparacion TEXT",
    }
    for column_name, ddl in column_sql.items():
        if column_name not in existing:
            db.session.execute(text(ddl))


def _remove_legacy_promotions():
    """Retira el motor de promociones antiguo sin perder datos silenciosamente."""
    inspector = inspect(db.engine)
    has_promotions = inspector.has_table("promotions")
    has_products = inspector.has_table("products")
    product_columns = (
        {col["name"] for col in inspector.get_columns("products")}
        if has_products else set()
    )

    if has_promotions:
        legacy_rows = db.session.execute(text("SELECT COUNT(*) FROM promotions")).scalar() or 0
        if legacy_rows:
            raise RuntimeError(
                "La tabla legacy promotions contiene datos. "
                "Migra esos registros antes de aplicar la purga."
            )
        db.session.execute(text("DROP TABLE promotions"))

    if not has_products:
        return
    if "en_promocion" in product_columns:
        active_rows = db.session.execute(
            text("SELECT COUNT(*) FROM products WHERE COALESCE(en_promocion, FALSE) = TRUE")
        ).scalar() or 0
        if active_rows:
            raise RuntimeError(
                "Hay productos con en_promocion activo. "
                "Convierte esas promociones antes de retirar la columna."
            )
        db.session.execute(text("ALTER TABLE products DROP COLUMN en_promocion"))
    if "porcentaje_descuento" in product_columns:
        discounted_rows = db.session.execute(
            text("SELECT COUNT(*) FROM products WHERE COALESCE(porcentaje_descuento, 0) <> 0")
        ).scalar() or 0
        if discounted_rows:
            raise RuntimeError(
                "Hay productos con porcentaje_descuento legacy. "
                "Convierte esos descuentos antes de retirar la columna."
            )
        db.session.execute(text("ALTER TABLE products DROP COLUMN porcentaje_descuento"))


def _migrate_unique_customer_phone():
    inspector = inspect(db.engine)
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    if "telefono_normalizado" not in columns:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN telefono_normalizado VARCHAR(20)"
        ))

    from phone_utils import normalizar_telefono_cliente

    rows = db.session.execute(text(
        "SELECT id, telefono FROM users WHERE telefono IS NOT NULL AND telefono <> ''"
    )).fetchall()
    owners: dict[str, int] = {}
    for user_id, telefono in rows:
        canonical = normalizar_telefono_cliente(telefono)
        if not canonical:
            continue
        previous = owners.get(canonical)
        if previous and previous != user_id:
            raise RuntimeError(
                f"Los usuarios {previous} y {user_id} comparten el teléfono {canonical}. "
                "Fusiona esos clientes antes de aplicar la unicidad."
            )
        owners[canonical] = user_id
        db.session.execute(
            text("UPDATE users SET telefono = :phone, telefono_normalizado = :phone WHERE id = :id"),
            {"phone": canonical, "id": user_id},
        )

    inspector = inspect(db.engine)
    unique_columns = {
        tuple(constraint.get("column_names") or [])
        for constraint in inspector.get_unique_constraints("users")
    }
    unique_indexes = {
        tuple(index.get("column_names") or [])
        for index in inspector.get_indexes("users")
        if index.get("unique")
    }
    phone_already_unique = ("telefono_normalizado",) in unique_columns | unique_indexes

    if db.engine.dialect.name == "postgresql" and not phone_already_unique:
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_telefono_normalizado "
            "ON users (telefono_normalizado) WHERE telefono_normalizado IS NOT NULL"
        ))
    elif not phone_already_unique:
        index_names = {idx["name"] for idx in inspect(db.engine).get_indexes("users")}
        if "uq_users_telefono_normalizado" not in index_names:
            db.session.execute(text(
                "CREATE UNIQUE INDEX uq_users_telefono_normalizado "
                "ON users (telefono_normalizado)"
            ))


def _migrate_provider_kitchen_flow():
    inspector = inspect(db.engine)
    if not inspector.has_table("products") or not inspector.has_table("orders"):
        return

    product_columns = {col["name"] for col in inspector.get_columns("products")}
    order_columns = {col["name"] for col in inspector.get_columns("orders")}
    if "canal_preparacion" not in product_columns:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN canal_preparacion VARCHAR(20) NOT NULL DEFAULT 'cocina'"
        ))
    if "proveedor_id" not in product_columns:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN proveedor_id INTEGER REFERENCES users(id)"
        ))
    if "proveedor_preparado" not in order_columns:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN proveedor_preparado BOOLEAN NOT NULL DEFAULT FALSE"
        ))
    if "proveedor_preparado_en" not in order_columns:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN proveedor_preparado_en TIMESTAMP"
        ))

    db.metadata.create_all(
        bind=db.engine,
        tables=[OrderProviderStatus.__table__],
        checkfirst=True,
    )
    db.session.execute(text("""
        INSERT INTO order_provider_status (pedido_id, proveedor_id, preparado, preparado_en)
        SELECT DISTINCT oi.pedido_id, p.proveedor_id,
               COALESCE(o.proveedor_preparado, FALSE),
               CASE WHEN COALESCE(o.proveedor_preparado, FALSE)
                    THEN o.proveedor_preparado_en ELSE NULL END
        FROM order_items oi
        JOIN products p ON p.id = oi.producto_id
        JOIN orders o ON o.id = oi.pedido_id
        WHERE p.proveedor_id IS NOT NULL
          AND COALESCE(p.canal_preparacion, 'cocina') = 'cocina'
        ON CONFLICT (pedido_id, proveedor_id) DO NOTHING
    """))


def _migrate_proveedor_entity():
    """Convierte el modelo 'proveedor = User con rol' en una entidad restaurante.

    Importante: TODO el descubrimiento de esquema (inspect) se hace ANTES del
    primer ALTER para evitar que un segundo inspector abra una conexión nueva
    que pelee por el lock con la transacción activa (deadlock distribuido).
    """
    # 1. Tablas nuevas (commit por separado para liberar locks de DDL)
    db.metadata.create_all(
        bind=db.engine,
        tables=[Proveedor.__table__, ProveedorProducto.__table__],
        checkfirst=True,
    )

    # 2. Inspección única — leemos columnas/FKs ANTES de tocar nada
    inspector = inspect(db.engine)
    user_columns = {col["name"] for col in inspector.get_columns("users")}
    product_columns = {col["name"] for col in inspector.get_columns("products")}
    ops_columns = {col["name"] for col in inspector.get_columns("order_provider_status")}
    ops_fk_names = {
        fk["name"]
        for fk in inspector.get_foreign_keys("order_provider_status")
        if fk.get("name") and "proveedor" in fk["name"]
    }

    # 3. Seed de proveedores desde users con rol='proveedor'
    db.session.execute(text("""
        INSERT INTO proveedores
            (nombre, telefono, direccion, email, activo, creado_en, comision_pct)
        SELECT u.nombre,
               u.telefono,
               u.direccion,
               u.email,
               TRUE,
               COALESCE(u.creado_en, NOW()),
               0
        FROM users u
        WHERE u.rol = 'proveedor'
          AND NOT EXISTS (
              SELECT 1 FROM proveedores p WHERE p.email = u.email
          )
    """))

    # 4. users.proveedor_id
    if "proveedor_id" not in user_columns:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN proveedor_id INTEGER REFERENCES proveedores(id)"
        ))
    db.session.execute(text("""
        UPDATE users u
        SET proveedor_id = p.id
        FROM proveedores p
        WHERE u.rol = 'proveedor'
          AND u.email = p.email
          AND u.proveedor_id IS NULL
    """))

    # 5. products.proveedor_despachador_id
    if "proveedor_despachador_id" not in product_columns:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN proveedor_despachador_id INTEGER "
            "REFERENCES proveedores(id)"
        ))
    db.session.execute(text("""
        UPDATE products pr
        SET proveedor_despachador_id = u.proveedor_id
        FROM users u
        WHERE pr.proveedor_id = u.id
          AND pr.es_combo = TRUE
          AND u.proveedor_id IS NOT NULL
          AND pr.proveedor_despachador_id IS NULL
    """))

    # 6. proveedor_productos: SKUs que cada proveedor maneja
    db.session.execute(text("""
        INSERT INTO proveedor_productos
            (proveedor_id, producto_id, stock, precio_costo, activo, actualizado_en)
        SELECT DISTINCT u.proveedor_id, pr.id, 0, pr.precio_costo, TRUE, NOW()
        FROM products pr
        JOIN users u ON pr.proveedor_id = u.id
        WHERE u.proveedor_id IS NOT NULL
        ON CONFLICT (proveedor_id, producto_id) DO NOTHING
    """))

    # 7. order_provider_status: cambio de FK users → proveedores
    # 7a. Añadir columna nueva si no existe
    if "proveedor_entity_id" not in ops_columns:
        db.session.execute(text(
            "ALTER TABLE order_provider_status ADD COLUMN proveedor_entity_id "
            "INTEGER REFERENCES proveedores(id)"
        ))
    # 7b. Mapear valores antiguos
    db.session.execute(text("""
        UPDATE order_provider_status ops
        SET proveedor_entity_id = u.proveedor_id
        FROM users u
        WHERE ops.proveedor_id = u.id
          AND u.proveedor_id IS NOT NULL
          AND ops.proveedor_entity_id IS NULL
    """))
    # 7c. Eliminar huérfanos (filas sin mapeo posible)
    db.session.execute(text(
        "DELETE FROM order_provider_status WHERE proveedor_entity_id IS NULL"
    ))
    # 7d. Solo si la migración no se completó antes: dropear FK antigua + columna
    # y renombrar la nueva. Idempotente: si proveedor_id ya está apuntando a
    # proveedores (migración previa terminó), salta este bloque.
    if "proveedor_id" in ops_columns:
        for cname in ops_fk_names:
            db.session.execute(text(
                f"ALTER TABLE order_provider_status DROP CONSTRAINT IF EXISTS {cname}"
            ))
        db.session.execute(text(
            "ALTER TABLE order_provider_status DROP COLUMN proveedor_id"
        ))
        db.session.execute(text(
            "ALTER TABLE order_provider_status RENAME COLUMN proveedor_entity_id TO proveedor_id"
        ))
        db.session.execute(text(
            "ALTER TABLE order_provider_status "
            "ADD CONSTRAINT order_provider_status_proveedor_id_fkey "
            "FOREIGN KEY (proveedor_id) REFERENCES proveedores(id)"
        ))
        db.session.execute(text(
            "ALTER TABLE order_provider_status ALTER COLUMN proveedor_id SET NOT NULL"
        ))


def _migrate_proveedor_modelo_acuerdo():
    """Añade proveedores.modelo_acuerdo con default 'stock_proveedor'."""
    inspector = inspect(db.engine)
    if not inspector.has_table("proveedores"):
        return
    cols = {col["name"] for col in inspector.get_columns("proveedores")}
    if "modelo_acuerdo" not in cols:
        db.session.execute(text(
            "ALTER TABLE proveedores ADD COLUMN modelo_acuerdo VARCHAR(30) "
            "NOT NULL DEFAULT 'stock_proveedor'"
        ))


def _migrate_proveedor_horario():
    """Añade Proveedor.hora_apertura y hora_cierre."""
    inspector = inspect(db.engine)
    if not inspector.has_table("proveedores"):
        return
    cols = {col["name"] for col in inspector.get_columns("proveedores")}
    if "hora_apertura" not in cols:
        db.session.execute(text("ALTER TABLE proveedores ADD COLUMN hora_apertura TIME"))
    if "hora_cierre" not in cols:
        db.session.execute(text("ALTER TABLE proveedores ADD COLUMN hora_cierre TIME"))


def _migrate_zonas_geo():
    """Añade centro_lat/centro_lng/radio_km a zonas_entrega."""
    inspector = inspect(db.engine)
    if not inspector.has_table("zonas_entrega"):
        return
    cols = {col["name"] for col in inspector.get_columns("zonas_entrega")}
    if "centro_lat" not in cols:
        db.session.execute(text("ALTER TABLE zonas_entrega ADD COLUMN centro_lat DOUBLE PRECISION"))
    if "centro_lng" not in cols:
        db.session.execute(text("ALTER TABLE zonas_entrega ADD COLUMN centro_lng DOUBLE PRECISION"))
    if "radio_km" not in cols:
        db.session.execute(text("ALTER TABLE zonas_entrega ADD COLUMN radio_km DOUBLE PRECISION"))


def _migrate_user_mfa():
    """Añade columnas MFA a users."""
    inspector = inspect(db.engine)
    if not inspector.has_table("users"):
        return
    cols = {col["name"] for col in inspector.get_columns("users")}
    if "mfa_secret" not in cols:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN mfa_secret VARCHAR(64)"
        ))
    if "mfa_enabled" not in cols:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE"
        ))
    if "mfa_session_version" not in cols:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN mfa_session_version INTEGER NOT NULL DEFAULT 0"
        ))


MIGRATIONS = [
    {
        "id": "20260526_01_order_events_notification_outbox",
        "description": "Crear order_events y notification_outbox",
        "tables": [OrderEvent.__table__, NotificationOutbox.__table__],
    },
    {
        "id": "20260526_02_site_config_valor_text",
        "description": "Ampliar site_config.valor a TEXT para claves e integraciones largas",
        "fn": _migrate_site_config_valor_text,
    },
    {
        "id": "20260602_01_products_combo_pricing",
        "description": "Agregar modo de precio y descuento porcentual para combos",
        "fn": _migrate_products_combo_pricing,
    },
    {
        "id": "20260605_01_combo_groups_structure",
        "description": "Crear secciones formales de combo y enlazarlas a componentes",
        "fn": _migrate_combo_groups_structure,
    },
    {
        "id": "20260605_02_combo_groups_backfill",
        "description": "Poblar secciones formales para combos existentes sin grupos",
        "fn": _migrate_combo_groups_structure,
    },
    {
        "id": "20260605_03_empty_combo_base_group",
        "description": "Crear seccion inicial para combos sin componentes",
        "fn": _migrate_empty_combo_groups,
    },
    {
        "id": "20260605_04_combo_item_option_fields",
        "description": "Agregar suplementos, default, activo y notas operativas a opciones de combo",
        "fn": _migrate_combo_item_option_fields,
    },
    {
        "id": "20260608_01_remove_legacy_promotions",
        "description": "Retirar tabla y columnas del motor de promociones obsoleto",
        "fn": _remove_legacy_promotions,
    },
    {
        "id": "20260609_01_unique_customer_phone",
        "description": "Normalizar y hacer único el teléfono usado como identidad del cliente",
        "fn": _migrate_unique_customer_phone,
    },
    {
        "id": "20260612_01_provider_kitchen_flow",
        "description": "Agregar canal, proveedor y preparación independiente por proveedor",
        "fn": _migrate_provider_kitchen_flow,
    },
    {
        "id": "20260613_02_financial_uniqueness",
        "description": "Impedir comisiones, ingresos, pagos staff y puntos duplicados",
        "fn": _migrate_financial_uniqueness,
    },
    {
        "id": "20260615_01_affiliate_payment_integrity",
        "description": "Separar comisiones, enlazar usos de afiliado e impedir devoluciones duplicadas",
        "fn": _migrate_affiliate_payment_integrity,
    },
    {
        "id": "20260616_01_proveedor_entity",
        "description": (
            "Convertir 'proveedor' en entidad restaurante: tablas proveedores y "
            "proveedor_productos, FKs en users/products/order_provider_status"
        ),
        "fn": _migrate_proveedor_entity,
    },
    {
        "id": "20260616_02_proveedor_modelo_acuerdo",
        "description": (
            "Añadir proveedores.modelo_acuerdo (stock_proveedor | stock_propio_bar) "
            "para distinguir cómo se liquida a cada bar"
        ),
        "fn": lambda: _migrate_proveedor_modelo_acuerdo(),
    },
    {
        "id": "20260617_01_user_mfa",
        "description": "Añadir columnas MFA (mfa_secret, mfa_enabled, mfa_session_version) a users",
        "fn": _migrate_user_mfa,
    },
    {
        "id": "20260617_02_idempotency_keys",
        "description": "Crear tabla idempotency_keys para deduplicar creación de pedidos (checkout, POS, bot)",
        "tables": [IdempotencyKey.__table__],
    },
    {
        "id": "20260617_03_zonas_geo",
        "description": "Añadir centro_lat/centro_lng/radio_km a zonas_entrega para matching geográfico",
        "fn": _migrate_zonas_geo,
    },
    {
        "id": "20260618_01_proveedor_horario",
        "description": "Añadir hora_apertura/hora_cierre a proveedores para filtrar catálogo fuera de horario",
        "fn": _migrate_proveedor_horario,
    },
]


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_registry():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id VARCHAR(120) PRIMARY KEY,
            description TEXT,
            applied_at TIMESTAMP NOT NULL
        )
    """))
    db.session.commit()


def _applied_ids() -> set[str]:
    rows = db.session.execute(text("SELECT id FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def _record_migration(migration_id: str, description: str):
    db.session.execute(
        text("""
            INSERT INTO schema_migrations (id, description, applied_at)
            VALUES (:id, :description, :applied_at)
        """),
        {"id": migration_id, "description": description, "applied_at": _utcnow()},
    )


# ID constante para `pg_advisory_lock`. Cualquier bigint distinto a los que
# usen otros sistemas en la misma BD; uso un hash determinista del nombre del
# servicio para reducir colisiones accidentales.
#   python -c "import hashlib; print(int.from_bytes(hashlib.sha256(b'oxidian.migrations').digest()[:8], 'big', signed=True))"
MIGRATION_LOCK_ID = -5273401983142671019


def _acquire_advisory_lock():
    """Bloquea hasta obtener el advisory lock global de migraciones.

    Si otro contenedor está corriendo migraciones, este se queda esperando en
    vez de meterse en una race condition. PostgreSQL libera el lock al cerrar
    la conexión, así que tras un crash no queda colgado."""
    db.session.execute(text("SELECT pg_advisory_lock(:lid)"), {"lid": MIGRATION_LOCK_ID})


def _release_advisory_lock():
    try:
        db.session.execute(text("SELECT pg_advisory_unlock(:lid)"), {"lid": MIGRATION_LOCK_ID})
    except Exception:
        pass


def apply_migrations() -> list[str]:
    # Solo intentamos el advisory lock en Postgres (otros dialectos no lo soportan).
    use_lock = db.engine.dialect.name == "postgresql"
    if use_lock:
        _acquire_advisory_lock()
    try:
        # En una instalación limpia aún no existen las tablas base referenciadas
        # por migraciones incrementales (por ejemplo order_events -> orders).
        # create_all es idempotente: crea solo lo que falta y no altera tablas
        # existentes, que siguen siendo responsabilidad de las migraciones.
        db.create_all()
        db.session.commit()

        _ensure_registry()
        applied = _applied_ids()
        ran: list[str] = []
        inspector = inspect(db.engine)

        for migration in MIGRATIONS:
            migration_id = migration["id"]
            tables = migration.get("tables") or []
            fn = migration.get("fn")
            missing_tables = [table for table in tables if not inspector.has_table(table.name)]
            if migration_id in applied and not missing_tables:
                continue

            if tables:
                db.metadata.create_all(bind=db.engine, tables=tables, checkfirst=True)
            if fn and migration_id not in applied:
                fn()
            if migration_id not in applied:
                _record_migration(migration_id, migration["description"])
            db.session.commit()
            ran.append(migration_id)

        return ran
    finally:
        if use_lock:
            _release_advisory_lock()
            db.session.commit()


def main():
    env = os.environ.get("FLASK_ENV", "production")
    os.environ["OXIDIAN_SKIP_STARTUP_DB"] = "1"
    app = create_app(env)
    with app.app_context():
        ran = apply_migrations()
        if ran:
            print({"applied": ran})
        else:
            print({"applied": []})


if __name__ == "__main__":
    main()
