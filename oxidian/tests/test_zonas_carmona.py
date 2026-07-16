"""Tests del sistema de zonas endurecido — reglas de Carmona.

Cubre:
- `validar_radio_entrega`: fail-closed sin config, con config buena y con
  direcciones fuera del radio.
- `asignar_zona_por_direccion`: prioriza zonas con geodata, cae al radio
  global si no hay geo por zona, respeta el flag legacy solo cuando está
  activo.
- `asignar_zona_por_coordenadas`: idéntica lógica de fallback.
- `_leer_geo_negocio`: cap defensivo del radio + valida rangos lat/lon.

Los tests NO tocan Internet — mockean `geocodificar_direccion` para
evitar depender de Nominatim en CI.
"""
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import SiteConfig, ZonaEntrega


class ZonasCarmonaTest(unittest.TestCase):
    # Coordenadas reales de Carmona centro (Plaza San Fernando).
    CARMONA_LAT = 37.4736
    CARMONA_LON = -5.6438
    # Coordenadas de Sevilla centro — ~30 km al oeste, deben quedar fuera
    # de cualquier radio razonable de Carmona.
    SEVILLA_LAT = 37.3891
    SEVILLA_LON = -5.9845
    # Muy cerca de Carmona centro — dentro incluso de un radio pequeño.
    CERCA_CARMONA_LAT = 37.4750
    CERCA_CARMONA_LON = -5.6420

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # Config default para "negocio en Carmona centro, radio 3km".
        SiteConfig.set("VALIDAR_RADIO_ENTREGA", "1", descripcion="test")
        SiteConfig.set("BLOQUEAR_DIRECCION_NO_VERIFICADA", "1", descripcion="test")
        SiteConfig.set("CENTRO_LAT", str(self.CARMONA_LAT), descripcion="test")
        SiteConfig.set("CENTRO_LON", str(self.CARMONA_LON), descripcion="test")
        SiteConfig.set("RADIO_ENTREGA_KM", "3", descripcion="test")
        SiteConfig.set("CIUDAD_NEGOCIO", "Carmona", descripcion="test")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_zona(self, nombre="Zona test", activo=True, orden=0, tiene_geo=False):
        z = ZonaEntrega(
            nombre=nombre,
            activo=activo,
            orden=orden,
            precio_envio=0,
            tiempo_estimado_min=30,
        )
        if tiene_geo:
            z.centro_lat = self.CARMONA_LAT
            z.centro_lng = self.CARMONA_LON
            z.radio_km = 3
        db.session.add(z)
        db.session.commit()
        return z

    # ── _leer_geo_negocio ─────────────────────────────────────────

    def test_leer_geo_devuelve_config_valida(self):
        from services import _leer_geo_negocio
        lat, lon, radio = _leer_geo_negocio()
        self.assertAlmostEqual(lat, self.CARMONA_LAT, places=4)
        self.assertAlmostEqual(lon, self.CARMONA_LON, places=4)
        self.assertEqual(radio, 3.0)

    def test_leer_geo_none_si_falta_alguna(self):
        from services import _leer_geo_negocio
        SiteConfig.set("CENTRO_LAT", "", descripcion="test")
        db.session.commit()
        self.assertEqual(_leer_geo_negocio(), (None, None, None))

    def test_leer_geo_cap_defensivo_radio_maximo(self):
        from services import _leer_geo_negocio
        SiteConfig.set("RADIO_ENTREGA_KM", "9999", descripcion="test")
        db.session.commit()
        _, _, radio = _leer_geo_negocio()
        self.assertEqual(radio, 25.0)

    def test_leer_geo_cap_defensivo_radio_minimo(self):
        from services import _leer_geo_negocio
        SiteConfig.set("RADIO_ENTREGA_KM", "0.01", descripcion="test")
        db.session.commit()
        _, _, radio = _leer_geo_negocio()
        self.assertEqual(radio, 0.5)

    def test_leer_geo_none_si_lat_fuera_de_rango(self):
        from services import _leer_geo_negocio
        SiteConfig.set("CENTRO_LAT", "200", descripcion="test")
        db.session.commit()
        self.assertEqual(_leer_geo_negocio(), (None, None, None))

    # ── validar_radio_entrega ────────────────────────────────────

    def test_validar_rechaza_direccion_muy_corta(self):
        from services import validar_radio_entrega
        r = validar_radio_entrega("ab")
        self.assertFalse(r["ok"])
        self.assertIn("dirección completa", r["mensaje"])

    def test_validar_rechaza_sin_config(self):
        # Fail-closed: sin CENTRO_LAT no vendemos.
        from services import validar_radio_entrega
        SiteConfig.set("CENTRO_LAT", "", descripcion="test")
        db.session.commit()
        r = validar_radio_entrega("Calle Mayor 5, Carmona")
        self.assertFalse(r["ok"])
        self.assertIn("no está configurada", r["mensaje"])

    def test_validar_acepta_direccion_dentro_del_radio(self):
        from services import validar_radio_entrega
        with patch("services.geocodificar_direccion",
                   return_value=(self.CERCA_CARMONA_LAT, self.CERCA_CARMONA_LON)):
            r = validar_radio_entrega("Calle Mayor 5, Carmona")
        self.assertTrue(r["ok"])
        self.assertIsNotNone(r["distancia_km"])
        self.assertLess(r["distancia_km"], 3)

    def test_validar_rechaza_direccion_fuera_del_radio(self):
        from services import validar_radio_entrega
        with patch("services.geocodificar_direccion",
                   return_value=(self.SEVILLA_LAT, self.SEVILLA_LON)):
            r = validar_radio_entrega("Calle Sierpes 1, Sevilla")
        self.assertFalse(r["ok"])
        self.assertGreater(r["distancia_km"], 25)
        self.assertIn("fuera de nuestra zona", r["mensaje"])

    def test_validar_rechaza_no_geocodificable_con_bloqueo(self):
        # BLOQUEAR_DIRECCION_NO_VERIFICADA=1 (default) → sin geocode, rechaza.
        from services import validar_radio_entrega
        with patch("services.geocodificar_direccion", return_value=None):
            r = validar_radio_entrega("XXXNoExisteXXX 999")
        self.assertFalse(r["ok"])
        self.assertIn("No encontramos", r["mensaje"])

    def test_validar_permite_no_geocodificable_si_flag_off(self):
        from services import validar_radio_entrega
        SiteConfig.set("BLOQUEAR_DIRECCION_NO_VERIFICADA", "0", descripcion="test")
        db.session.commit()
        with patch("services.geocodificar_direccion", return_value=None):
            r = validar_radio_entrega("XXXNoExisteXXX 999")
        self.assertTrue(r["ok"])

    def test_validar_desactivado_globalmente(self):
        from services import validar_radio_entrega
        SiteConfig.set("VALIDAR_RADIO_ENTREGA", "0", descripcion="test")
        db.session.commit()
        r = validar_radio_entrega("cualquier cosa")
        self.assertTrue(r["ok"])

    # ── asignar_zona_por_direccion ────────────────────────────────

    def test_asignar_none_cuando_no_hay_zonas(self):
        from services import asignar_zona_por_direccion
        self.assertIsNone(asignar_zona_por_direccion("Calle X", []))

    def test_asignar_zona_geo_cuando_dentro_del_radio(self):
        from services import asignar_zona_por_direccion
        z = self._mk_zona("Carmona centro", tiene_geo=True)
        with patch("services.geocodificar_direccion",
                   return_value=(self.CERCA_CARMONA_LAT, self.CERCA_CARMONA_LON)):
            r = asignar_zona_por_direccion("Calle Mayor 5", [z])
        self.assertEqual(r.id, z.id)

    def test_asignar_none_cuando_zona_geo_pero_direccion_fuera(self):
        from services import asignar_zona_por_direccion
        z = self._mk_zona("Carmona centro", tiene_geo=True)
        with patch("services.geocodificar_direccion",
                   return_value=(self.SEVILLA_LAT, self.SEVILLA_LON)):
            r = asignar_zona_por_direccion("Sevilla centro", [z])
        self.assertIsNone(r)

    def test_asignar_usa_radio_global_si_zona_sin_geo(self):
        # Zona sin geodata individual, pero el negocio tiene radio configurado.
        # Debe cae al radio global — dentro → asigna la zona.
        from services import asignar_zona_por_direccion
        z = self._mk_zona("Zona única", tiene_geo=False)
        with patch("services.geocodificar_direccion",
                   return_value=(self.CERCA_CARMONA_LAT, self.CERCA_CARMONA_LON)):
            r = asignar_zona_por_direccion("Calle Mayor 5", [z])
        self.assertEqual(r.id, z.id)

    def test_asignar_none_si_zona_sin_geo_y_fuera_del_radio_global(self):
        from services import asignar_zona_por_direccion
        z = self._mk_zona("Zona única", tiene_geo=False)
        with patch("services.geocodificar_direccion",
                   return_value=(self.SEVILLA_LAT, self.SEVILLA_LON)):
            r = asignar_zona_por_direccion("Sevilla centro", [z])
        self.assertIsNone(r)

    def test_asignar_fallback_legacy_solo_si_flag_activo(self):
        # Sin geo en zona ni en negocio, con flag ON → devuelve zona (peligroso
        # pero explícitamente pedido por el admin).
        from services import asignar_zona_por_direccion
        SiteConfig.set("CENTRO_LAT", "", descripcion="test")
        SiteConfig.set("ALLOW_LEGACY_ZONE_FALLBACK", "1", descripcion="test")
        db.session.commit()
        z = self._mk_zona("Legacy", tiene_geo=False)
        r = asignar_zona_por_direccion("cualquier cosa", [z])
        self.assertEqual(r.id, z.id)

    def test_asignar_none_sin_geo_negocio_ni_flag_legacy(self):
        # Fail-closed: cualquier ambigüedad → None.
        from services import asignar_zona_por_direccion
        SiteConfig.set("CENTRO_LAT", "", descripcion="test")
        db.session.commit()
        z = self._mk_zona("Sin geo", tiene_geo=False)
        r = asignar_zona_por_direccion("cualquier cosa", [z])
        self.assertIsNone(r)

    # ── asignar_zona_por_coordenadas ──────────────────────────────

    def test_coordenadas_asignan_zona_geo_dentro(self):
        from services import asignar_zona_por_coordenadas
        z = self._mk_zona("Carmona centro", tiene_geo=True)
        r, dist = asignar_zona_por_coordenadas(
            self.CERCA_CARMONA_LAT, self.CERCA_CARMONA_LON, [z],
        )
        self.assertEqual(r.id, z.id)
        self.assertIsNotNone(dist)

    def test_coordenadas_rechazan_fuera_de_zona(self):
        from services import asignar_zona_por_coordenadas
        z = self._mk_zona("Carmona centro", tiene_geo=True)
        r, dist = asignar_zona_por_coordenadas(
            self.SEVILLA_LAT, self.SEVILLA_LON, [z],
        )
        self.assertIsNone(r)

    def test_coordenadas_invalidas_none(self):
        from services import asignar_zona_por_coordenadas
        z = self._mk_zona("Carmona centro", tiene_geo=True)
        self.assertEqual(asignar_zona_por_coordenadas(200, 200, [z]), (None, None))
        self.assertEqual(asignar_zona_por_coordenadas("x", "y", [z]), (None, None))
        self.assertEqual(asignar_zona_por_coordenadas(None, None, [z]), (None, None))

    def test_poligono_irregular_prevalece_sobre_radio_legacy(self):
        from services import asignar_zona_por_coordenadas
        legacy = self._mk_zona("Radio amplio", tiene_geo=True, orden=0)
        polygon = self._mk_zona("Casco alcanzable", orden=9)
        polygon.cobertura_geojson = {
            "type": "Polygon",
            "coordinates": [[
                [-5.650, 37.468], [-5.637, 37.468], [-5.637, 37.480],
                [-5.650, 37.480], [-5.650, 37.468],
            ]],
        }
        db.session.commit()
        zone, _ = asignar_zona_por_coordenadas(37.474, -5.643, [legacy, polygon])
        self.assertEqual(zone.id, polygon.id)

    def test_poligono_rechaza_punto_dentro_del_antiguo_radio(self):
        from services import asignar_zona_por_coordenadas
        polygon = self._mk_zona("Casco alcanzable")
        polygon.cobertura_geojson = {
            "type": "Polygon",
            "coordinates": [[
                [-5.650, 37.468], [-5.637, 37.468], [-5.637, 37.480],
                [-5.650, 37.480], [-5.650, 37.468],
            ]],
        }
        db.session.commit()
        # Cerca del centro global, pero al norte del contorno autorizado.
        zone, _ = asignar_zona_por_coordenadas(37.488, -5.643, [polygon])
        self.assertIsNone(zone)

    def test_validacion_devuelve_zona_autoritativa(self):
        from services import validar_radio_entrega
        polygon = self._mk_zona("Casco alcanzable")
        polygon.cobertura_geojson = {
            "type": "Polygon",
            "coordinates": [[
                [-5.650, 37.468], [-5.637, 37.468], [-5.637, 37.480],
                [-5.650, 37.480], [-5.650, 37.468],
            ]],
        }
        db.session.commit()
        with patch("services.geocodificar_direccion", return_value=(37.474, -5.643)):
            result = validar_radio_entrega("Calle de prueba 1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["zona_id"], polygon.id)
        self.assertEqual(result["metodo_cobertura"], "poligono")


if __name__ == "__main__":
    unittest.main()
