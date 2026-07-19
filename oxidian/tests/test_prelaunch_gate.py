import unittest

from prelaunch import es_ruta_exenta_preapertura


class PrelaunchRoutingTest(unittest.TestCase):
    def test_storefront_publico_no_esta_exento(self):
        for path in ("/", "/producto/3", "/carrito", "/checkout", "/club"):
            with self.subTest(path=path):
                self.assertFalse(es_ruta_exenta_preapertura(path))

    def test_operacion_salud_e_integraciones_siguen_disponibles(self):
        for path in (
            "/auth/login",
            "/admin",
            "/superadmin/config",
            "/repartidor/ruta",
            "/preparador/pedidos",
            "/api/bot/health",
            "/api/push/subscribe",
            "/webhook/evolution",
            "/webhook/evolution/messages-upsert",
            "/health",
            "/static/css/app.css",
            "/uploads/productos/a.webp",
            "/sw.js",
            "/manifest.webmanifest",
            "/robots.txt",
        ):
            with self.subTest(path=path):
                self.assertTrue(es_ruta_exenta_preapertura(path))


if __name__ == "__main__":
    unittest.main()
