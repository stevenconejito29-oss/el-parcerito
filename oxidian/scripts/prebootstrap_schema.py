"""Compatibilidad mínima necesaria antes de cargar modelos durante el bootstrap."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text


def main():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return
    engine = create_engine(database_url)
    inspector = inspect(engine)
    changed = []
    with engine.begin() as connection:
        if inspector.has_table("users"):
            columns = {column["name"] for column in inspector.get_columns("users")}
            if "telefono_normalizado" not in columns:
                connection.execute(text(
                    "ALTER TABLE users ADD COLUMN telefono_normalizado VARCHAR(20)"
                ))
                changed.append("users.telefono_normalizado")
        if inspector.has_table("products"):
            columns = {column["name"] for column in inspector.get_columns("products")}
            if "canal_preparacion" not in columns:
                connection.execute(text(
                    "ALTER TABLE products ADD COLUMN canal_preparacion VARCHAR(20) "
                    "NOT NULL DEFAULT 'cocina'"
                ))
                changed.append("products.canal_preparacion")
            if "proveedor_id" not in columns:
                connection.execute(text(
                    "ALTER TABLE products ADD COLUMN proveedor_id INTEGER REFERENCES users(id)"
                ))
                changed.append("products.proveedor_id")
        if inspector.has_table("orders"):
            columns = {column["name"] for column in inspector.get_columns("orders")}
            if "proveedor_preparado" not in columns:
                connection.execute(text(
                    "ALTER TABLE orders ADD COLUMN proveedor_preparado BOOLEAN "
                    "NOT NULL DEFAULT FALSE"
                ))
                changed.append("orders.proveedor_preparado")
            if "proveedor_preparado_en" not in columns:
                connection.execute(text(
                    "ALTER TABLE orders ADD COLUMN proveedor_preparado_en TIMESTAMP"
                ))
                changed.append("orders.proveedor_preparado_en")
    if changed:
        print({"prebootstrap": changed})


if __name__ == "__main__":
    main()
