import unittest

from zone_geometry import (
    CoverageGeometryError,
    contiene_punto,
    limites_cobertura,
    normalizar_cobertura_geojson,
)


class ZoneGeometryTest(unittest.TestCase):
    def setUp(self):
        self.polygon = {
            "type": "Polygon",
            "coordinates": [[
                [-5.66, 37.46], [-5.62, 37.46], [-5.62, 37.49],
                [-5.66, 37.49], [-5.66, 37.46],
            ]],
        }

    def test_normaliza_y_cierra_contorno(self):
        value = {"type": "Polygon", "coordinates": [[
            [-5.66, 37.46], [-5.62, 37.46], [-5.62, 37.49],
        ]]}
        normalized = normalizar_cobertura_geojson(value)
        self.assertEqual(normalized["coordinates"][0][0], normalized["coordinates"][0][-1])

    def test_punto_interior_borde_y_exterior(self):
        self.assertTrue(contiene_punto(self.polygon, 37.47, -5.64))
        self.assertTrue(contiene_punto(self.polygon, 37.46, -5.64))
        self.assertFalse(contiene_punto(self.polygon, 37.50, -5.64))

    def test_hueco_excluye_area_interior(self):
        polygon = dict(self.polygon)
        polygon["coordinates"] = polygon["coordinates"] + [[
            [-5.645, 37.468], [-5.635, 37.468], [-5.635, 37.478],
            [-5.645, 37.478], [-5.645, 37.468],
        ]]
        self.assertFalse(contiene_punto(polygon, 37.47, -5.64))
        self.assertTrue(contiene_punto(polygon, 37.485, -5.64))

    def test_multipoligono_admite_areas_separadas(self):
        multi = {
            "type": "MultiPolygon",
            "coordinates": [self.polygon["coordinates"], [[
                [-5.70, 37.50], [-5.69, 37.50], [-5.69, 37.51],
                [-5.70, 37.51], [-5.70, 37.50],
            ]]],
        }
        self.assertTrue(contiene_punto(multi, 37.505, -5.695))

    def test_rechaza_tipo_y_coordenadas_invalidas(self):
        with self.assertRaises(CoverageGeometryError):
            normalizar_cobertura_geojson({"type": "Point", "coordinates": [0, 0]})
        with self.assertRaises(CoverageGeometryError):
            normalizar_cobertura_geojson({"type": "Polygon", "coordinates": [[[999, 0], [0, 1], [1, 0]]]})

    def test_rechaza_contorno_autocruzado(self):
        with self.assertRaises(CoverageGeometryError):
            normalizar_cobertura_geojson({
                "type": "Polygon",
                "coordinates": [[
                    [-5.66, 37.46], [-5.62, 37.49], [-5.66, 37.49],
                    [-5.61, 37.45], [-5.66, 37.46],
                ]],
            })

    def test_limites(self):
        self.assertEqual(limites_cobertura(self.polygon), (37.46, -5.66, 37.49, -5.62))


if __name__ == "__main__":
    unittest.main()
