import unittest

from flask import Flask
from sqlalchemy import event

from catalog_projection import build_catalog_projection
from extensions import db
from models import (
    ComboGroup,
    ComboItem,
    Product,
    ProductExtraGroup,
    ProductExtraOption,
    ProductPresentation,
    Stock,
)


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
        sauce_group = ProductExtraGroup(
            producto_id=product.id,
            nombre="Salsas",
            min_selecciones=0,
            max_selecciones=1,
            activo=True,
        )
        flavor_group = ProductExtraGroup(
            producto_id=product.id,
            nombre="Sabores",
            tipo="sabor",
            min_selecciones=1,
            max_selecciones=3,
            activo=True,
        )
        db.session.add_all([sauce_group, flavor_group])
        db.session.flush()
        db.session.add_all([
            Stock(producto_id=product.id, cantidad=4),
            ProductExtraOption(grupo_id=sauce_group.id, nombre="Ají", activo=True),
            ProductExtraOption(grupo_id=flavor_group.id, nombre="Mango", activo=True),
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
        self.assertTrue(projection[product.id].has_flavors)
        self.assertTrue(projection[product.id].flavors_required)
        self.assertEqual(projection[product.id].flavor_catalog_max_selecciones, 3)
        self.assertEqual([p.tamaño for p in projection[product.id].presentations], ["grande"])
        self.assertTrue(projection[unlimited.id].available)
        self.assertEqual(float(projection[product.id].display_price), 6)

    def test_combo_summary_preserves_included_items_and_choice_limits(self):
        component = Product(nombre='Incluido', precio=3, activo=True, stock_mostrar_en_web=False)
        combo = Product(nombre='Combo', precio=99, combo_precio_base=12, es_combo=True, activo=True)
        db.session.add_all([component, combo]); db.session.flush()
        group = ComboGroup(combo_id=combo.id,nombre='Bebidas',tipo='seleccion',min_selecciones=2,max_selecciones=3)
        db.session.add(group); db.session.flush()
        fixed = ComboItem(combo_id=combo.id,producto_id=component.id,cantidad=4,activo=True,es_seleccionable=False)
        choice = ComboItem(combo_id=combo.id,producto_id=component.id,cantidad=1,activo=True,es_seleccionable=True,combo_group_id=group.id,max_selecciones=3)
        removed = ComboItem(combo_id=combo.id,producto_id=component.id,cantidad=99,activo=False,es_seleccionable=False)
        db.session.add_all([fixed,choice,removed]); db.session.commit()
        card = build_catalog_projection([combo])[combo.id]
        self.assertEqual([row.id for row in card.combo_items], [fixed.id,choice.id])
        self.assertEqual(card.combo_items[0].cantidad,4)
        self.assertEqual(card.combo_choices,[{'name':'Bebidas','minimum':2,'maximum':3}])
        self.assertEqual(float(card.display_price),12)
        self.assertEqual(combo.precio_combo_para_seleccion([choice.id]*2),card.display_price)
        from routes.public import _combo_selection_payload
        _, _, metadata = _combo_selection_payload(combo, {})
        self.assertEqual([item['combo_item_id'] for item in metadata['combo']['componentes']], [fixed.id])
        combo.combo_precio_modo='descuento_porcentaje'
        combo.combo_descuento_pct=10
        # 4 fijos + 2 elecciones a 3 €; el componente retirado no se cobra.
        self.assertEqual(float(combo.precio_combo_para_seleccion([choice.id]*2)),16.2)

    def test_presentation_starting_price_includes_cheapest_active_size(self):
        product=Product(nombre='Tamaños',precio=5,activo=True)
        db.session.add(product);db.session.flush()
        db.session.add_all([
            ProductPresentation(producto_id=product.id,tamaño='pequeño',precio_extra=-1,activo=True),
            ProductPresentation(producto_id=product.id,tamaño='grande',precio_extra=2,activo=True),
            ProductPresentation(producto_id=product.id,tamaño='mediano',precio_extra=-4,activo=False),
        ])
        db.session.commit()
        self.assertEqual(float(build_catalog_projection([product])[product.id].display_price),4)

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
