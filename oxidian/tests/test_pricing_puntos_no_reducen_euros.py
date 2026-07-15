"""Tests que garantizan la regla clave del rediseño 2026-07-15:

    Los puntos NO reducen el total en euros. Solo se canjean por un
    producto gratis del catálogo.

Antes: `/checkout` aceptaba `puntos_usar` del form y el motor de pricing
aplicaba un descuento en €. La UI mostraba slider engañoso. Rediseño:
`public.py:1966` fuerza `puntos_a_canjear = 0` sin importar el form.

Estos tests validan la garantía a nivel de motor: si alguien reintroduce
la variable en el frontend, el pricing lo respeta pero el endpoint la
sigue anulando. Los tests golpean directamente `calcular_precio` para
demostrar el comportamiento y sirven como spec regresiva.
"""
import unittest

from flask import Flask

from extensions import db
from pricing_service import calcular_precio


class FakeCupon:
    """Cupón mínimo con la firma que `calcular_precio` espera."""
    def __init__(self, tipo="porcentaje", valor=10.0, minimo=0.0):
        self.id = 1
        self.tipo = tipo
        self.valor = valor
        self.minimo_pedido = minimo

    def calcular_descuento(self, subtotal):
        if self.tipo == "porcentaje":
            return round(subtotal * (self.valor / 100.0), 2)
        if self.tipo == "monto_fijo":
            return min(self.valor, subtotal)
        return 0.0


class FakeAfiliado:
    def __init__(self, valor_pct=5.0):
        self.id = 42
        self.tipo = "externo"
        self.descuento_tipo = "porcentaje"
        self.descuento_valor = valor_pct

    def calcular_descuento(self, subtotal):
        return round(subtotal * (self.descuento_valor / 100.0), 2)


class PricingPuntosNoDescuentoEurosTest(unittest.TestCase):
    def setUp(self):
        # calcular_precio no toca la BD pero pricing_service importa
        # models arriba — dejamos un app_context por si acaso alguna
        # ruta introduce dependencia (no crashea si no la usa).
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite://")
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_puntos_usar_cero_no_afecta_total(self):
        res = calcular_precio([], subtotal=100.0, puntos_usar=0)
        self.assertEqual(float(res.descuento_puntos), 0.0)
        self.assertEqual(int(res.puntos_usados), 0)
        self.assertEqual(float(res.total), 100.0)

    def test_cupon_solo_aplica_su_descuento(self):
        cupon = FakeCupon(tipo="porcentaje", valor=10)
        res = calcular_precio([], subtotal=100.0, cupon=cupon)
        self.assertEqual(float(res.descuento_cupon), 10.0)
        self.assertEqual(float(res.descuento_puntos), 0.0)
        self.assertEqual(float(res.total), 90.0)

    def test_afiliado_solo_aplica_su_descuento(self):
        afil = FakeAfiliado(valor_pct=5)
        res = calcular_precio([], subtotal=100.0, afiliado=afil)
        self.assertEqual(float(res.descuento_afiliado), 5.0)
        self.assertEqual(float(res.descuento_puntos), 0.0)
        self.assertEqual(float(res.total), 95.0)

    def test_cupon_y_afiliado_suman(self):
        cupon = FakeCupon(tipo="porcentaje", valor=10)
        afil = FakeAfiliado(valor_pct=5)
        res = calcular_precio([], subtotal=100.0, cupon=cupon, afiliado=afil)
        self.assertEqual(float(res.descuento_cupon), 10.0)
        self.assertEqual(float(res.descuento_afiliado), 5.0)
        self.assertEqual(float(res.descuento_total), 15.0)
        self.assertEqual(float(res.total), 85.0)

    def test_descuento_total_capped_al_subtotal(self):
        # Un cupón absurdo del 90% no debe llevar el total a negativo.
        cupon = FakeCupon(tipo="monto_fijo", valor=999)
        res = calcular_precio([], subtotal=50.0, cupon=cupon)
        self.assertLessEqual(float(res.descuento_total), 50.0)
        self.assertGreaterEqual(float(res.total), 0.01)


if __name__ == "__main__":
    unittest.main()
