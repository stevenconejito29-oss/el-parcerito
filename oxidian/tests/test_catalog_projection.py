import unittest

from flask import Flask
from sqlalchemy import event

from catalog_projection import build_catalog_projection
from extensions import db
from models import Product, ProductExtraGroup, ProductPresentation, Stock


class CatalogProjectionTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test",
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_bulk_projection_resolves_card_data_and_stock(self):
        product = Product(
            nombre="Arepa",
            precio=5,
            activo=True,
            vertical="comida",
            tipo_entrega="inmediato",
            stock_mostrar_en_web=True,
        )
        unlimited = Product(
            nombre="Café preparado",
            precio=2,
            activo=True,
            vertical="comida",
            tipo_entrega="inmediato",
            stock_mostrar_en_web=False,
        )
        db.session.add_all([product, unlimited])
        db.session.flush()
        db.session.add_all([
            Stock(producto_id=product.id, cantidad=4),
            ProductExtraGroup(
                producto_id=product.id,
                nombre="Salsas",
                min_selecciones=0,
                max_selecciones=1,
                activo=True,
            ),
            ProductPresentation(
                producto_id=product.id,
                tamaño="grande",
                precio_extra=1,
                activo=True,
            ),
        ])
        db.session.commit()

        projection = build_catalog_projection([product, unlimited], "propio")

        self.assertEqual(projection[product.id].stock, 4)
        self.assertTrue(projection[product.id].available)
        self.assertTrue(projection[product.id].has_extras)
        self.assertEqual([p.tamaño for p in projection[product.id].presentations], ["grande"])
        self.assertTrue(projection[unlimited.id].available)

    def test_query_count_is_bounded_instead_of_growing_per_product(self):
        products = [
            Product(
                nombre=f"Producto {index}",
                precio=1,
                activo=True,
                vertical="comida",
                tipo_entrega="inmediato",
                stock_mostrar_en_web=False,
            )
            for index in range(40)
        ]
        db.session.add_all(products)
        db.session.commit()
        # Igual que la ruta pública: la colección entra ya cargada por una
        # única consulta. No contamos aquí los refresh de instancias expiradas
        # por el commit del fixture.
        products = Product.query.order_by(Product.id).all()
        query_count = 0

        def count_query(*_args):
            nonlocal query_count
            query_count += 1

        event.listen(db.engine, "before_cursor_execute", count_query)
        try:
            projection = build_catalog_projection(products, "propio")
        finally:
            event.remove(db.engine, "before_cursor_execute", count_query)

        self.assertEqual(len(projection), 40)
        self.assertLessEqual(query_count, 8)


if __name__ == "__main__":
    unittest.main()
