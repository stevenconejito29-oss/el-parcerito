import unittest
from types import SimpleNamespace

from routes.public import _variantes_catalogo_unificadas


def variant(product_id, catalog_key, origin):
    return SimpleNamespace(
        id=product_id,
        clave_catalogo=f"catalog:{catalog_key}",
        origen_operativo_key=origin,
    )


class CatalogOriginTest(unittest.TestCase):
    def test_catalog_shows_one_card_per_explicit_key(self):
        products = [
            variant(1, "cola-330", "propio"),
            variant(2, "cola-330", "proveedor:10"),
            variant(3, "burger", "proveedor:10"),
        ]
        selected = _variantes_catalogo_unificadas(products)
        self.assertEqual([product.id for product in selected], [1, 3])

    def test_catalog_prefers_cart_origin_without_mixing_ids(self):
        products = [
            variant(1, "cola-330", "propio"),
            variant(2, "cola-330", "proveedor:10"),
            variant(3, "cola-330", "proveedor:20"),
        ]
        selected = _variantes_catalogo_unificadas(
            products,
            origen_preferido="proveedor:10",
        )
        self.assertEqual([product.id for product in selected], [2])


if __name__ == "__main__":
    unittest.main()
