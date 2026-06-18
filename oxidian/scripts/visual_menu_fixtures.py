"""Crea o elimina contenido temporal para auditar MenuConfig visualmente."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from extensions import db
from models import Categoria, MenuConfig, Product, User, utcnow


MARKER = "[AUDIT VISUAL]"
QA_EMAIL = "qa-visual-rewards@oxidian.local"


def cleanup() -> int:
    deleted = MenuConfig.query.filter(MenuConfig.titulo.startswith(MARKER)).delete(
        synchronize_session=False
    )
    User.query.filter_by(email=QA_EMAIL).delete(synchronize_session=False)
    db.session.commit()
    return deleted


def create() -> int:
    cleanup()
    product = Product.query.filter_by(activo=True).filter(Product.imagen_url.isnot(None)).first()
    category = Categoria.query.filter_by(activo=True).first()
    owner = User.query.filter_by(rol="super_admin", activo=True).first()
    if not product or not category or not owner:
        raise RuntimeError("Faltan producto, categoría o super admin para la auditoría")

    rows = [
        MenuConfig(
            tipo="banner",
            pagina="home",
            titulo=f"{MARKER} Combo del día",
            contenido="Una promoción clara, táctil y legible desde móvil.",
            imagen_url=product.imagen_url,
            enlace_url=f"/producto/{product.id}",
            orden=1,
            activo=True,
            creado_por=owner.id,
        ),
        MenuConfig(
            tipo="producto_destacado",
            pagina="home",
            titulo=f"{MARKER} Recomendado",
            producto_id=product.id,
            orden=2,
            activo=True,
            creado_por=owner.id,
        ),
        MenuConfig(
            tipo="seccion",
            pagina="home",
            titulo=f"{MARKER} Explora {category.nombre}",
            contenido="Acceso directo a la categoría.",
            categoria_id=category.id,
            orden=3,
            activo=True,
            creado_por=owner.id,
        ),
        MenuConfig(
            tipo="texto_promo",
            pagina="menu",
            titulo=f"{MARKER} Puntos en cada pedido",
            contenido="Identifícate con tu WhatsApp y canjea durante el checkout.",
            enlace_url="/club",
            orden=1,
            activo=True,
            creado_por=owner.id,
        ),
        MenuConfig(
            tipo="banner",
            pagina="checkout",
            titulo=f"{MARKER} Revisa tu pedido",
            contenido="Confirma dirección, recompensas y forma de pago.",
            imagen_url=product.imagen_url,
            enlace_url="#form-checkout",
            orden=1,
            activo=True,
            creado_por=owner.id,
        ),
        MenuConfig(
            tipo="producto_destacado",
            pagina="checkout",
            titulo=f"{MARKER} Añade algo más",
            producto_id=product.id,
            orden=2,
            activo=True,
            creado_por=owner.id,
        ),
    ]
    db.session.add_all(rows)
    customer = User(
        nombre="Cliente visual",
        email=QA_EMAIL,
        rol="cliente",
        telefono="+34699111222",
        puntos=300,
        activo=True,
        cod_puntos="123456",
        cod_puntos_expira=utcnow() + timedelta(minutes=20),
        cod_puntos_intentos=0,
    )
    customer.set_password("visual-fixture-not-for-login")
    db.session.add(customer)
    db.session.commit()
    return len(rows)


def main() -> None:
    action = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    app = create_app("production")
    with app.app_context():
        if action == "create":
            print({"created": create()})
        elif action == "cleanup":
            print({"deleted": cleanup()})
        else:
            raise SystemExit("Uso: visual_menu_fixtures.py create|cleanup")


if __name__ == "__main__":
    main()
