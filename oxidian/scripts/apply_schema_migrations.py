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
    ComboItem,
    ComboGroup,
    IdempotencyKey,
    NotificationOutbox,
    OrderEvent,
    OrderProviderStatus,
    Product,
    ProductVariant,
    ExtraCatalogItem,
    ProductExtraGroup,
    ProductExtraOption,
    Proveedor,
    ProveedorProducto,
    Stock,
    User,
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


def _migrate_product_fulfillment_mode():
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    existing = {col["name"] for col in inspector.get_columns("products")}
    if "modalidad_entrega" not in existing:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN modalidad_entrega VARCHAR(20) NOT NULL DEFAULT 'ambas'"
        ))


def _migrate_product_order_group():
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    existing = {col["name"] for col in inspector.get_columns("products")}
    if "grupo_pedido" not in existing:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN grupo_pedido VARCHAR(80)"
        ))


def _migrate_order_confirmacion_estado():
    """Añade `orders.confirmacion_estado` y `confirmacion_en` (nullable).

    Señal paralela al `estado` para marcar pedidos que necesitan verificación
    del cliente antes de comenzar preparación (antifraude / anti pedido
    fantasma). NULL para pedidos legacy y para pedidos de riesgo bajo — no
    cambia la máquina de estados operativa.
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("orders"):
        return
    existing = {col["name"] for col in inspector.get_columns("orders")}
    if "confirmacion_estado" not in existing:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN confirmacion_estado VARCHAR(30)"
        ))
    if "confirmacion_en" not in existing:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN confirmacion_en TIMESTAMP"
        ))


def _migrate_order_confirmacion_nivel():
    """Añade `orders.confirmacion_nivel` (VARCHAR(10) nullable).

    Persiste el nivel de riesgo evaluado ('MEDIUM' o 'HIGH') para pedidos
    que activaron la verificación pasiva. Necesario para:
      - Desagregar métricas admin (cuántos MEDIUM vs HIGH).
      - Aplicar políticas distintas (auto-cancel más agresivo para HIGH).
      - Auditoría posterior de por qué se marcó un pedido.
    NULL para pedidos que pasaron como LOW o legacy previos al feature.
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("orders"):
        return
    existing = {col["name"] for col in inspector.get_columns("orders")}
    if "confirmacion_nivel" not in existing:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN confirmacion_nivel VARCHAR(10)"
        ))


def _migrate_product_solo_canje():
    """Añade Product.solo_canje (bool NOT NULL default false).
    Marca productos exclusivos de canje con puntos — no comprables con dinero."""
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    existing = {col["name"] for col in inspector.get_columns("products")}
    if "solo_canje" not in existing:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN solo_canje BOOLEAN NOT NULL DEFAULT FALSE"
        ))


def _migrate_product_vertical():
    """Añade Product.vertical (comida|producto|ambos) para separar catálogos por nicho.
    Por defecto 'ambos' → no rompe catálogos existentes.
    El super_admin puede editarlo por producto para restringir en qué modo se muestra."""
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    existing = {col["name"] for col in inspector.get_columns("products")}
    if "vertical" not in existing:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN vertical VARCHAR(20) NOT NULL "
            "DEFAULT 'ambos'"
        ))
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_products_vertical ON products (vertical)"
        ))


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


def _migrate_order_en_punto_encuentro():
    """Añade Order.en_punto_encuentro (subestado del reparto) + timestamp."""
    inspector = inspect(db.engine)
    if not inspector.has_table("orders"):
        return
    existing = {col["name"] for col in inspector.get_columns("orders")}
    stmts = {
        "en_punto_encuentro": "ALTER TABLE orders ADD COLUMN en_punto_encuentro BOOLEAN NOT NULL DEFAULT false",
        "en_punto_encuentro_en": "ALTER TABLE orders ADD COLUMN en_punto_encuentro_en TIMESTAMP",
    }
    for col, ddl in stmts.items():
        if col not in existing:
            db.session.execute(text(ddl))


def _migrate_combo_item_activo_not_null():
    """Combo items con activo=NULL se evaluaban como agotados en templates.
    Set default=true en filas existentes y añade NOT NULL + server_default."""
    inspector = inspect(db.engine)
    if not inspector.has_table("combo_items"):
        return
    # 1) datos: NULL → true
    db.session.execute(text("UPDATE combo_items SET activo=true WHERE activo IS NULL"))
    # 2) schema: NOT NULL + DEFAULT (postgres tolera si ya lo tienen)
    for stmt in (
        "ALTER TABLE combo_items ALTER COLUMN activo SET DEFAULT true",
        "ALTER TABLE combo_items ALTER COLUMN activo SET NOT NULL",
    ):
        try:
            db.session.execute(text(stmt))
        except Exception:
            db.session.rollback()


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


def _migrate_provider_operator_phones():
    """Conserva acceso legacy solo cuando el enlace proveedor-operador es inequívoco."""
    if not inspect(db.engine).has_table("proveedores"):
        return
    for proveedor in Proveedor.query.filter(Proveedor.telefono.isnot(None)).all():
        operadores = (
            User.query
            .filter_by(rol="proveedor", proveedor_id=proveedor.id, activo=True)
            .filter(User.telefono_normalizado.is_(None))
            .all()
        )
        if len(operadores) != 1:
            continue
        from phone_utils import normalizar_telefono_cliente
        canonical = normalizar_telefono_cliente(proveedor.telefono)
        if not canonical or User.query.filter_by(telefono_normalizado=canonical).first():
            continue
        operadores[0].telefono = canonical


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


def _migrate_zonas_cobertura_geojson():
    """Añade la geometría detallada; JSONB en PostgreSQL, JSON en SQLite."""
    inspector = inspect(db.engine)
    if not inspector.has_table("zonas_entrega"):
        return
    cols = {col["name"] for col in inspector.get_columns("zonas_entrega")}
    if "cobertura_geojson" in cols:
        return
    column_type = "JSONB" if db.engine.dialect.name == "postgresql" else "JSON"
    db.session.execute(text(
        f"ALTER TABLE zonas_entrega ADD COLUMN cobertura_geojson {column_type}"
    ))


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


def _catalog_key(product):
    if product.es_combo:
        return None
    key = str(product.get_atributos().get("catalog_key") or "").strip().lower()
    return key or None


def _ensure_simple_provider_mapping(product):
    proveedor_id = product.proveedor_despachador_id
    if product.es_combo or not proveedor_id:
        return
    row = ProveedorProducto.query.filter_by(
        proveedor_id=proveedor_id,
        producto_id=product.id,
    ).first()
    if row:
        row.activo = True
        return
    db.session.add(ProveedorProducto(
        proveedor_id=proveedor_id,
        producto_id=product.id,
        stock=0,
        precio_costo=product.precio_costo,
        activo=True,
    ))


def _merge_provider_mappings(canonical, duplicate):
    rows = ProveedorProducto.query.filter_by(producto_id=duplicate.id).all()
    for source in rows:
        target = ProveedorProducto.query.filter_by(
            proveedor_id=source.proveedor_id,
            producto_id=canonical.id,
        ).first()
        if target:
            target.stock = int(target.stock or 0) + int(source.stock or 0)
            target.activo = bool(target.activo or source.activo)
            if target.precio_costo is None:
                target.precio_costo = source.precio_costo
            db.session.delete(source)
        else:
            source.producto_id = canonical.id


def _consolidate_simple_catalog_variants():
    """Convierte variantes por origen en un producto maestro por catalog_key."""
    products = Product.query.filter(Product.es_combo.is_(False)).order_by(Product.id).all()
    for product in products:
        _ensure_simple_provider_mapping(product)
    db.session.flush()

    groups = {}
    for product in products:
        key = _catalog_key(product)
        if key:
            groups.setdefault(key, []).append(product)

    for variants in groups.values():
        active_variants = [product for product in variants if product.activo]
        already_consolidated = (
            len(active_variants) == 1
            and all(product.proveedor_despachador_id is None for product in variants)
        )
        canonical = (
            active_variants[0]
            if already_consolidated
            else min(
                variants,
                key=lambda product: (
                    product.proveedor_despachador_id is not None,
                    not bool(product.activo),
                    product.id,
                ),
            )
        )
        canonical.activo = any(product.activo for product in variants)
        for duplicate in variants:
            if duplicate.id == canonical.id:
                continue
            _merge_provider_mappings(canonical, duplicate)
            for stock_row in Stock.query.filter_by(producto_id=duplicate.id).all():
                stock_row.producto_id = canonical.id
            for combo_item in ComboItem.query.filter_by(producto_id=duplicate.id).all():
                combo_item.producto_id = canonical.id
            duplicate.activo = False
            duplicate.proveedor_despachador_id = None
        canonical.proveedor_despachador_id = None

    # Los simples sin catalog_key también pasan a ser catálogo maestro. Su
    # pertenencia al bar queda representada exclusivamente por el mapping.
    for product in products:
        product.proveedor_despachador_id = None


def _add_postgresql_check_if_safe(table, name, expression, invalid_where):
    if db.engine.dialect.name != "postgresql":
        return
    inspector = inspect(db.engine)
    if not inspector.has_table(table):
        return
    exists = db.session.execute(text("""
        SELECT 1
        FROM pg_constraint
        WHERE conname = :name
          AND conrelid = to_regclass(:table)
    """), {"name": name, "table": table}).first()
    if exists:
        return
    invalid = db.session.execute(text(
        f"SELECT 1 FROM {table} WHERE {invalid_where} LIMIT 1"
    )).first()
    if invalid:
        return
    db.session.execute(text(
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression})"
    ))


def _migrate_master_catalog_inventory():
    _consolidate_simple_catalog_variants()
    _add_postgresql_check_if_safe(
        "stock",
        "ck_stock_cantidad_nonnegative",
        "cantidad >= 0",
        "cantidad < 0",
    )


def _migrate_order_fulfillment_mode_and_staff_role():
    inspector = inspect(db.engine)
    if inspector.has_table("orders"):
        existing = {col["name"] for col in inspector.get_columns("orders")}
        if "tipo_entrega_cliente" not in existing:
            db.session.execute(text(
                "ALTER TABLE orders ADD COLUMN tipo_entrega_cliente VARCHAR(20) NOT NULL DEFAULT 'delivery'"
            ))
    if inspector.has_table("users"):
        db.session.execute(text(
            "UPDATE users SET rol = 'preparacion' WHERE rol = 'staff'"
        ))
    _add_postgresql_check_if_safe(
        "proveedor_productos",
        "ck_proveedor_productos_stock_nonnegative",
        "stock >= 0",
        "stock < 0",
    )
    _add_postgresql_check_if_safe(
        "proveedores",
        "ck_proveedores_comision_pct_range",
        "comision_pct >= 0 AND comision_pct <= 100",
        "comision_pct < 0 OR comision_pct > 100",
    )
    _add_postgresql_check_if_safe(
        "combo_items",
        "ck_combo_items_cantidad_positive",
        "cantidad > 0",
        "cantidad <= 0",
    )


def _migrate_service_commission_snapshots():
    inspector = inspect(db.engine)
    if not inspector.has_table("orders"):
        return
    existing = {col["name"] for col in inspector.get_columns("orders")}
    columns = {
        "service_commission_pct": "NUMERIC(5, 2) NOT NULL DEFAULT 0",
        "service_commission_amount": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
        "merchant_net_amount": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
    }
    for name, definition in columns.items():
        if name not in existing:
            db.session.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {definition}"))
    db.session.execute(text(
        "UPDATE orders SET merchant_net_amount = COALESCE(total, 0) "
        "WHERE merchant_net_amount = 0 AND COALESCE(total, 0) <> 0 "
        "AND service_commission_amount = 0"
    ))


def _migrate_reusable_extra_catalog():
    ExtraCatalogItem.__table__.create(bind=db.engine, checkfirst=True)
    inspector = inspect(db.engine)
    if not inspector.has_table("product_extra_options"):
        return
    existing = {col["name"] for col in inspector.get_columns("product_extra_options")}
    if "catalog_item_id" not in existing:
        db.session.execute(text(
            "ALTER TABLE product_extra_options ADD COLUMN catalog_item_id INTEGER "
            "REFERENCES extra_catalog_items(id) ON DELETE SET NULL"
        ))


def _migrate_product_extra_groups_current_schema():
    """Alinea bases antiguas de extras con el modelo actual.

    Algunas instalaciones locales conservaron las columnas legacy
    min_select/max_select aunque la migración quedó registrada como aplicada.
    Esta migración mira el esquema real y corrige nombres/defaults sin perder
    los grupos ya creados.
    """
    db.metadata.create_all(
        bind=db.engine,
        tables=[ExtraCatalogItem.__table__, ProductExtraGroup.__table__, ProductExtraOption.__table__],
        checkfirst=True,
    )
    inspector = inspect(db.engine)
    if not inspector.has_table("product_extra_groups"):
        return

    existing = {col["name"] for col in inspector.get_columns("product_extra_groups")}
    if "descripcion" not in existing:
        db.session.execute(text(
            "ALTER TABLE product_extra_groups ADD COLUMN descripcion VARCHAR(240)"
        ))

    if "min_selecciones" not in existing:
        if "min_select" in existing:
            db.session.execute(text(
                "ALTER TABLE product_extra_groups RENAME COLUMN min_select TO min_selecciones"
            ))
            existing.remove("min_select")
            existing.add("min_selecciones")
        else:
            db.session.execute(text(
                "ALTER TABLE product_extra_groups ADD COLUMN min_selecciones INTEGER NOT NULL DEFAULT 0"
            ))
            existing.add("min_selecciones")
    elif "min_select" in existing:
        db.session.execute(text("""
            UPDATE product_extra_groups
            SET min_selecciones = COALESCE(min_selecciones, min_select, 0)
        """))

    if "max_selecciones" not in existing:
        if "max_select" in existing:
            db.session.execute(text(
                "ALTER TABLE product_extra_groups RENAME COLUMN max_select TO max_selecciones"
            ))
            existing.remove("max_select")
            existing.add("max_selecciones")
        else:
            db.session.execute(text(
                "ALTER TABLE product_extra_groups ADD COLUMN max_selecciones INTEGER NOT NULL DEFAULT 1"
            ))
            existing.add("max_selecciones")
    elif "max_select" in existing:
        db.session.execute(text("""
            UPDATE product_extra_groups
            SET max_selecciones = COALESCE(max_selecciones, max_select, 1)
        """))

    db.session.execute(text("""
        UPDATE product_extra_groups
        SET min_selecciones = GREATEST(0, COALESCE(min_selecciones, 0)),
            max_selecciones = GREATEST(GREATEST(0, COALESCE(min_selecciones, 0)), COALESCE(max_selecciones, 1))
    """))
    db.session.execute(text("ALTER TABLE product_extra_groups ALTER COLUMN min_selecciones SET DEFAULT 0"))
    db.session.execute(text("ALTER TABLE product_extra_groups ALTER COLUMN min_selecciones SET NOT NULL"))
    db.session.execute(text("ALTER TABLE product_extra_groups ALTER COLUMN max_selecciones SET DEFAULT 1"))
    db.session.execute(text("ALTER TABLE product_extra_groups ALTER COLUMN max_selecciones SET NOT NULL"))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_product_extra_groups_product "
        "ON product_extra_groups (producto_id, orden)"
    ))
    _add_postgresql_check_if_safe(
        "product_extra_groups",
        "ck_extra_group_min",
        "min_selecciones >= 0",
        "min_selecciones < 0",
    )
    _add_postgresql_check_if_safe(
        "product_extra_groups",
        "ck_extra_group_range",
        "max_selecciones >= min_selecciones",
        "max_selecciones < min_selecciones",
    )


def _migrate_user_zona_repartidor():
    """Añade users.zona_repartidor_id (FK zonas_entrega) para asignar zona al repartidor."""
    inspector = inspect(db.engine)
    if not inspector.has_table("users") or not inspector.has_table("zonas_entrega"):
        return
    cols = {col["name"] for col in inspector.get_columns("users")}
    if "zona_repartidor_id" not in cols:
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN zona_repartidor_id INTEGER NULL "
            "REFERENCES zonas_entrega(id) ON DELETE SET NULL"
        ))


def _migrate_order_codigo_confirmacion_expira():
    """Añade orders.codigo_confirmacion_expira_en (DateTime nullable) para
    aplicar TTL a los códigos de entrega. Idempotente en todos los dialectos."""
    inspector = inspect(db.engine)
    if not inspector.has_table("orders"):
        return
    cols = {c["name"] for c in inspector.get_columns("orders")}
    if "codigo_confirmacion_expira_en" in cols:
        return
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN codigo_confirmacion_expira_en TIMESTAMP NULL"
        ))
    elif dialect == "mysql":
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN codigo_confirmacion_expira_en DATETIME NULL"
        ))
    else:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN codigo_confirmacion_expira_en DATETIME"
        ))


def _migrate_product_iva_pct():
    """Añade products.iva_pct (NUMERIC(4,2) nullable).

    Nullable → si es NULL, en `_resolver_iva_pct_producto` se aplica el
    default por vertical desde SiteConfig (IVA_DEFAULT_COMIDA/IVA_DEFAULT_RETAIL).
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    cols = {c["name"] for c in inspector.get_columns("products")}
    if "iva_pct" in cols:
        return
    dialect = db.engine.dialect.name
    if dialect in ("postgresql", "mysql"):
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN iva_pct NUMERIC(4,2) NULL"
        ))
    else:
        db.session.execute(text(
            "ALTER TABLE products ADD COLUMN iva_pct NUMERIC(4,2)"
        ))


def _migrate_order_iva_total():
    """Añade orders.iva_total (NUMERIC(10,2) NOT NULL default 0).

    Se calcula al confirmar el pedido a partir del snapshot iva_pct de cada
    OrderItem. Los pedidos preexistentes se quedan en 0 (correcto: no hay
    reprocesado retro-activo)."""
    inspector = inspect(db.engine)
    if not inspector.has_table("orders"):
        return
    cols = {c["name"] for c in inspector.get_columns("orders")}
    if "iva_total" in cols:
        return
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN iva_total NUMERIC(10,2) NOT NULL DEFAULT 0"
        ))
    elif dialect == "mysql":
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN iva_total DECIMAL(10,2) NOT NULL DEFAULT 0"
        ))
    else:
        db.session.execute(text(
            "ALTER TABLE orders ADD COLUMN iva_total NUMERIC(10,2) DEFAULT 0"
        ))


def _migrate_user_nif():
    """Añade users.nif (VARCHAR(15) nullable) para facturación fiscal opcional."""
    inspector = inspect(db.engine)
    if not inspector.has_table("users"):
        return
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "nif" in cols:
        return
    db.session.execute(text(
        "ALTER TABLE users ADD COLUMN nif VARCHAR(15) NULL"
    ))


def _migrate_product_variants():
    """Crea la tabla `product_variants` (variantes retail: talla/color/precio/stock).

    Idempotente:
    - Si la tabla ya existe, no hace nada (db.create_all previo la habrá creado
      desde el modelo cuando aplique).
    - Añade además el índice único parcial en Postgres para evitar duplicados
      (product_id, talla, color) cuando ambos no son NULL.
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    if not inspector.has_table("product_variants"):
        # db.create_all() en apply_migrations() ya la habrá creado desde el
        # metadata del modelo. Si aún así falta (dialecto atípico), la creamos
        # en bruto.
        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS product_variants (
                    id SERIAL PRIMARY KEY,
                    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    sku VARCHAR(60) UNIQUE,
                    talla VARCHAR(20),
                    color VARCHAR(40),
                    color_hex VARCHAR(7),
                    precio_override NUMERIC(10,2),
                    stock INTEGER NOT NULL DEFAULT 0,
                    activo BOOLEAN NOT NULL DEFAULT TRUE,
                    orden INTEGER NOT NULL DEFAULT 0,
                    imagen_url VARCHAR(300)
                )
            """))
    # Índice de orden (idempotente).
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_product_variants_producto_orden
            ON product_variants (product_id, orden)
        """))
        # Índice único parcial para (product_id, talla, color) cuando ambos no son NULL.
        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_product_variants_talla_color
            ON product_variants (product_id, talla, color)
            WHERE talla IS NOT NULL AND color IS NOT NULL
        """))


def _migrate_product_retail_fields():
    """Añade columnas retail-specific a `products`.
    Nullable en todos — comida no las usa. Idempotente."""
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    cols = {c["name"] for c in inspector.get_columns("products")}
    dialect = db.engine.dialect.name
    if "marca" not in cols:
        db.session.execute(text("ALTER TABLE products ADD COLUMN marca VARCHAR(100)"))
    if "material" not in cols:
        db.session.execute(text("ALTER TABLE products ADD COLUMN material VARCHAR(100)"))
    if "dimensiones" not in cols:
        db.session.execute(text("ALTER TABLE products ADD COLUMN dimensiones VARCHAR(80)"))
    if "peso_gramos" not in cols:
        db.session.execute(text("ALTER TABLE products ADD COLUMN peso_gramos INTEGER"))
    if "garantia_meses" not in cols:
        db.session.execute(text("ALTER TABLE products ADD COLUMN garantia_meses INTEGER"))


def _migrate_product_vertical_backfill_ambos():
    """Convierte productos legacy con vertical='ambos' al nicho activo actual
    (SiteConfig.TIPO_TIENDA). Elimina la contaminación cruzada donde un producto
    aparecía en las dos tiendas simultáneamente.

    Idempotente: si no hay filas con 'ambos', no hace nada. Segundo run: 0 filas.
    """
    inspector = inspect(db.engine)
    if not inspector.has_table("products"):
        return
    cols = {c["name"] for c in inspector.get_columns("products")}
    if "vertical" not in cols:
        return
    # Lee nicho activo desde SiteConfig — si no existe, comida por default.
    tt_row = db.session.execute(text(
        "SELECT valor FROM site_config WHERE clave = 'TIPO_TIENDA' LIMIT 1"
    )).first()
    tt = (tt_row[0].strip().lower() if tt_row and tt_row[0] else "comida")
    if tt not in ("comida", "producto"):
        tt = "comida"
    db.session.execute(text(
        "UPDATE products SET vertical = :tt WHERE LOWER(COALESCE(vertical, '')) = 'ambos'"
    ), {"tt": tt})


def _migrate_combo_item_variant():
    """Añade combo_items.variant_id (FK product_variants ondelete SET NULL) para
    permitir bundles retail que congelen una talla/color por componente."""
    inspector = inspect(db.engine)
    if not inspector.has_table("combo_items"):
        return
    cols = {c["name"] for c in inspector.get_columns("combo_items")}
    dialect = db.engine.dialect.name
    if "variant_id" not in cols:
        if dialect == "postgresql":
            db.session.execute(text(
                "ALTER TABLE combo_items ADD COLUMN variant_id INTEGER "
                "REFERENCES product_variants(id) ON DELETE SET NULL"
            ))
        elif dialect == "mysql":
            db.session.execute(text(
                "ALTER TABLE combo_items ADD COLUMN variant_id INTEGER NULL"
            ))
        else:
            db.session.execute(text(
                "ALTER TABLE combo_items ADD COLUMN variant_id INTEGER"
            ))
    try:
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_combo_items_variant_id "
            "ON combo_items (variant_id)"
        ))
    except Exception:
        pass


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
        "id": "20260716_01_zonas_cobertura_geojson",
        "description": "Añadir Polygon/MultiPolygon GeoJSON para cobertura precisa de reparto",
        "fn": _migrate_zonas_cobertura_geojson,
    },
    {
        "id": "20260618_01_proveedor_horario",
        "description": "Añadir hora_apertura/hora_cierre a proveedores para filtrar catálogo fuera de horario",
        "fn": _migrate_proveedor_horario,
    },
    {
        "id": "20260619_01_provider_operator_phones",
        "description": "Migrar el teléfono legacy del bar a su único operador inequívoco",
        "fn": _migrate_provider_operator_phones,
    },
    {
        "id": "20260620_01_master_catalog_location_inventory",
        "description": (
            "Consolidar productos simples por catalog_key, mover stock por ubicación "
            "y proteger cantidades y comisiones con CHECK constraints"
        ),
        "fn": _migrate_master_catalog_inventory,
    },
    {
        "id": "20260626_01_fulfillment_mode_and_staff_cleanup",
        "description": "Añadir tipo_entrega_cliente a pedidos y migrar rol staff legacy a preparacion",
        "fn": _migrate_order_fulfillment_mode_and_staff_role,
    },
    {
        "id": "20260629_01_service_commission_snapshots",
        "description": "Congelar comisión white-label y neto del restaurante por pedido",
        "fn": _migrate_service_commission_snapshots,
    },
    {
        "id": "20260630_01_product_extras",
        "description": "Crear grupos y opciones de extras configurables por producto",
        "tables": [ProductExtraGroup.__table__, ProductExtraOption.__table__],
    },
    {
        "id": "20260630_02_product_fulfillment_mode",
        "description": "Añadir modalidad delivery, recogida o ambas por producto",
        "fn": _migrate_product_fulfillment_mode,
    },
    {
        "id": "20260701_01_product_order_group",
        "description": "Añadir grupo configurable de compatibilidad por producto",
        "fn": _migrate_product_order_group,
    },
    {
        "id": "20260701_02_reusable_extra_catalog",
        "description": "Crear biblioteca reutilizable de extras y vincularla a productos",
        "fn": _migrate_reusable_extra_catalog,
    },
    {
        "id": "20260702_01_product_solo_canje",
        "description": "Añadir Product.solo_canje (productos exclusivos de canje con puntos)",
        "fn": _migrate_product_solo_canje,
    },
    {
        "id": "20260702_02_combo_item_activo_not_null",
        "description": "combo_items.activo → NOT NULL con default TRUE (NULLs se evaluaban como agotado)",
        "fn": _migrate_combo_item_activo_not_null,
    },
    {
        "id": "20260702_03_order_en_punto_encuentro",
        "description": "Order.en_punto_encuentro + timestamp (subestado del reparto para el bot)",
        "fn": _migrate_order_en_punto_encuentro,
    },
    {
        "id": "20260706_01_product_vertical",
        "description": "Product.vertical: separa catálogo por nicho (comida|producto|ambos)",
        "fn": _migrate_product_vertical,
    },
    {
        "id": "20260707_01_product_extra_groups_current_schema",
        "description": "Alinear product_extra_groups legacy con columnas actuales de selección",
        "fn": _migrate_product_extra_groups_current_schema,
    },
    {
        "id": "20260710_01_order_codigo_confirmacion_expira",
        "description": "orders.codigo_confirmacion_expira_en (TTL configurable del código de entrega)",
        "fn": _migrate_order_codigo_confirmacion_expira,
    },
    {
        "id": "20260710_02_user_zona_repartidor",
        "description": "users.zona_repartidor_id (FK zonas_entrega) para restringir pedidos por zona asignada",
        "fn": _migrate_user_zona_repartidor,
    },
    {
        "id": "20260710_03_product_iva_pct",
        "description": "Product.iva_pct (tasa IVA por producto — España, fallback por vertical)",
        "fn": _migrate_product_iva_pct,
    },
    {
        "id": "20260710_04_order_iva_total",
        "description": "Order.iva_total (IVA congelado por pedido para exportación fiscal)",
        "fn": _migrate_order_iva_total,
    },
    {
        "id": "20260710_05_user_nif",
        "description": "User.nif (NIF/DNI/NIE/CIF opcional para facturas a empresa)",
        "fn": _migrate_user_nif,
    },
    {
        "id": "20260710_10_product_variants",
        "description": (
            "product_variants: variantes retail (talla/color/precio_override/stock) "
            "por producto con vertical producto|ambos"
        ),
        "tables": [ProductVariant.__table__],
        "fn": _migrate_product_variants,
    },
    {
        "id": "20260710_21_combo_item_variant",
        "description": (
            "combo_items.variant_id (FK product_variants ON DELETE SET NULL) "
            "para bundles retail que congelan talla/color por componente"
        ),
        "fn": _migrate_combo_item_variant,
    },
    {
        "id": "20260710_30_product_retail_fields",
        "description": (
            "Product: marca, material, dimensiones, peso_gramos, garantia_meses "
            "(nullable). Solo aplican si vertical='producto'."
        ),
        "fn": _migrate_product_retail_fields,
    },
    {
        "id": "20260710_31_backfill_vertical_ambos",
        "description": (
            "Convierte Product.vertical='ambos' (legacy) al nicho activo actual "
            "para eliminar la contaminación cruzada entre comida y retail."
        ),
        "fn": _migrate_product_vertical_backfill_ambos,
    },
    {
        "id": "20260712_40_order_confirmacion_estado",
        "description": (
            "Añade orders.confirmacion_estado y confirmacion_en para verificación "
            "pasiva anti-pedido-fantasma vía WhatsApp. NULL por defecto — no "
            "toca pedidos legacy ni cambia la máquina de estados operativa."
        ),
        "fn": _migrate_order_confirmacion_estado,
    },
    {
        "id": "20260713_41_order_confirmacion_nivel",
        "description": (
            "Añade orders.confirmacion_nivel (VARCHAR(10) nullable) para "
            "persistir MEDIUM/HIGH del scoring de riesgo antifraude. Permite "
            "desagregar métricas y aplicar políticas distintas por nivel."
        ),
        "fn": _migrate_order_confirmacion_nivel,
    },
    {
        "id": "20260715_01_cupon_afiliado_usos_por_cliente",
        "description": (
            "Añade coupons.usos_por_cliente y affiliate_codes.usos_por_cliente "
            "(INTEGER nullable, None = ilimitado). Permite topear reutilización "
            "por cliente para prevenir abuso de códigos permanentes."
        ),
        "fn": lambda: _migrate_add_nullable_int_columns([
            ("coupons", "usos_por_cliente"),
            ("affiliate_codes", "usos_por_cliente"),
        ]),
    },
    {
        "id": "20260715_02_product_cantidad_por_lote",
        "description": (
            "Añade products.cantidad_por_lote (INT NULL). Cuando >0, el "
            "producto se vende por tandas — el checkout multiplica cantidad "
            "de item por este valor para stock/coste. NULL = venta unitaria "
            "clásica (retro-compat)."
        ),
        "fn": lambda: _migrate_add_nullable_int_columns([
            ("products", "cantidad_por_lote"),
        ]),
    },
    {
        "id": "20260715_03_product_batches",
        "description": (
            "Crea tabla product_batches (encargos con fecha por lote): "
            "disponibilidad de tandas por producto/fecha con reserva atómica. "
            "UNIQUE (producto_id, fecha_entrega) — un lote por combinación."
        ),
        "fn": lambda: _migrate_create_product_batches(),
    },
]


def _migrate_create_product_batches():
    """Crea la tabla product_batches si aún no existe (Postgres + SQLite).
    Idempotente: chequea inspector antes de emitir DDL."""
    inspector = inspect(db.engine)
    if inspector.has_table("product_batches"):
        return
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        db.session.execute(text("""
            CREATE TABLE product_batches (
                id                       SERIAL PRIMARY KEY,
                producto_id              INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                fecha_entrega            DATE NOT NULL,
                cantidad_por_tanda       INTEGER NOT NULL,
                cantidad_maxima_tandas   INTEGER NULL,
                cantidad_vendida_tandas  INTEGER NOT NULL DEFAULT 0,
                estado                   VARCHAR(16) NOT NULL DEFAULT 'abierto',
                listo_en                 TIMESTAMP NULL,
                creado_en                TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_product_batches_producto_fecha UNIQUE (producto_id, fecha_entrega)
            )
        """))
    else:
        # SQLite (tests) — mismo esquema sin las particularidades PG.
        db.session.execute(text("""
            CREATE TABLE product_batches (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id              INTEGER NOT NULL,
                fecha_entrega            DATE NOT NULL,
                cantidad_por_tanda       INTEGER NOT NULL,
                cantidad_maxima_tandas   INTEGER,
                cantidad_vendida_tandas  INTEGER NOT NULL DEFAULT 0,
                estado                   VARCHAR(16) NOT NULL DEFAULT 'abierto',
                listo_en                 DATETIME,
                creado_en                DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (producto_id, fecha_entrega)
            )
        """))


def _migrate_add_nullable_int_columns(pairs):
    """Añade columnas INTEGER NULL a las tablas indicadas si no existen.
    Idempotente: chequea `information_schema` (o inspector) antes."""
    inspector = inspect(db.engine)
    for tabla, col in pairs:
        if not inspector.has_table(tabla):
            continue
        cols = {c["name"] for c in inspector.get_columns(tabla)}
        if col in cols:
            continue
        db.session.execute(text(
            f"ALTER TABLE {tabla} ADD COLUMN {col} INTEGER NULL"
        ))


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
