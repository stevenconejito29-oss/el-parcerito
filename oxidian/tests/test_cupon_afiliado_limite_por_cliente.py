"""Tests del límite por cliente en cupones y afiliados + regla
"comisión de afiliado solo si el referido es nuevo".

Antes:
  - Un cliente podía aplicar el mismo cupón/afiliado en cada pedido.
    Códigos permanentes ("BIENVENIDO", "REFERIDO") se volvían vector
    de abuso: mismo cliente veterano cobrando descuento ilimitado.
  - La comisión de afiliado se generaba en CADA uso del código, aunque
    el "referido" ya fuera cliente veterano. Vector de fraude interno:
    un staff aplicaba su propio código a clientes recurrentes.

Ahora:
  - `Coupon.es_valido_para_cliente(cliente_id)` respeta `usos_por_cliente`.
  - `AffiliateCode.es_valido_para_cliente(cliente_id)` idem.
  - `registrar_uso_afiliado()` solo crea `StaffPayment` si el pedido actual
    es el primero no-cancelado del cliente.
"""
import unittest

from flask import Flask

from extensions import db
from models import AffiliateCode, Coupon, Order, StaffPayment, User


class CuponAfiliadoLimitTest(unittest.TestCase):
    _seq = 0

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
        self.cliente = self._mk_user("Cli", "cliente")
        self.affiliate_user = self._mk_user("Staff", "admin")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_user(self, nombre, rol):
        CuponAfiliadoLimitTest._seq += 1
        u = User(
            nombre=nombre,
            email=f"{nombre.lower()}-{self._seq}@t.invalid",
            telefono=f"+3460{self._seq:07d}",
            rol=rol,
            activo=True,
        )
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        return u

    def _mk_pedido(self, cupon=None, estado="entregado"):
        CuponAfiliadoLimitTest._seq += 1
        o = Order(
            numero_pedido=f"T-{self._seq}",
            cliente_id=self.cliente.id,
            total=20, subtotal=20,
            estado=estado,
            tipo_entrega_cliente="delivery",
            cupon_id=cupon.id if cupon else None,
        )
        db.session.add(o)
        db.session.commit()
        return o

    # ── Cupón: límite por cliente ──
    def test_cupon_sin_limite_por_cliente_reutilizable(self):
        c = Coupon(codigo="WELCOME", tipo="porcentaje", valor=10,
                   activo=True, usos_por_cliente=None)
        db.session.add(c); db.session.commit()
        self._mk_pedido(cupon=c)
        ok, _ = c.es_valido_para_cliente(self.cliente.id)
        self.assertTrue(ok)

    def test_cupon_con_limite_1_bloquea_segundo_uso(self):
        c = Coupon(codigo="ONCE", tipo="porcentaje", valor=10,
                   activo=True, usos_por_cliente=1)
        db.session.add(c); db.session.commit()
        # Primer uso permitido
        ok1, _ = c.es_valido_para_cliente(self.cliente.id)
        self.assertTrue(ok1)
        # Simulamos que el cliente ya lo usó
        self._mk_pedido(cupon=c)
        ok2, msg = c.es_valido_para_cliente(self.cliente.id)
        self.assertFalse(ok2)
        self.assertIn("máximo", msg.lower())

    def test_cupon_pedidos_cancelados_no_cuentan(self):
        c = Coupon(codigo="ONCE2", tipo="porcentaje", valor=10,
                   activo=True, usos_por_cliente=1)
        db.session.add(c); db.session.commit()
        self._mk_pedido(cupon=c, estado="cancelado")
        ok, _ = c.es_valido_para_cliente(self.cliente.id)
        self.assertTrue(ok, "pedidos cancelados no deben consumir el cupo")

    def test_cupon_guest_sin_cliente_no_aplica_limite(self):
        c = Coupon(codigo="GST", tipo="porcentaje", valor=10,
                   activo=True, usos_por_cliente=1)
        db.session.add(c); db.session.commit()
        ok, _ = c.es_valido_para_cliente(None)
        self.assertTrue(ok, "sin cliente_id no aplica límite por cliente")

    # ── Afiliado: límite por cliente ──
    def test_afiliado_con_limite_por_cliente(self):
        af = AffiliateCode(codigo="AMIGO", tipo="externo",
                            descuento_tipo="porcentaje", descuento_valor=5,
                            comision_tipo="porcentaje", comision_valor=2,
                            activo=True, usos_por_cliente=1)
        db.session.add(af); db.session.commit()
        ok1, _ = af.es_valido_para_cliente(self.cliente.id)
        self.assertTrue(ok1)

    # ── Comisión: solo si el cliente es nuevo ──
    def test_comision_solo_primer_pedido(self):
        from services import registrar_uso_afiliado
        af = AffiliateCode(codigo="REF1", tipo="externo",
                            user_id=self.affiliate_user.id,
                            descuento_tipo="porcentaje", descuento_valor=5,
                            comision_tipo="porcentaje", comision_valor=10,
                            activo=True)
        db.session.add(af); db.session.commit()

        # Primer pedido del cliente → SÍ genera StaffPayment
        pedido1 = self._mk_pedido()
        uso1 = registrar_uso_afiliado(af, pedido1, self.cliente, descuento_aplicado=1.0)
        db.session.commit()
        pagos = StaffPayment.query.filter_by(user_id=self.affiliate_user.id).count()
        self.assertEqual(pagos, 1)
        self.assertGreater(float(uso1.comision_generada), 0)

        # Segundo pedido del cliente → NO genera StaffPayment
        pedido2 = self._mk_pedido()
        uso2 = registrar_uso_afiliado(af, pedido2, self.cliente, descuento_aplicado=1.0)
        db.session.commit()
        pagos = StaffPayment.query.filter_by(user_id=self.affiliate_user.id).count()
        self.assertEqual(pagos, 1, "segundo pedido no debe crear StaffPayment")
        self.assertEqual(float(uso2.comision_generada), 0.0)

    def test_pedido_cancelado_previo_no_bloquea_comision(self):
        from services import registrar_uso_afiliado
        af = AffiliateCode(codigo="REF2", tipo="externo",
                            user_id=self.affiliate_user.id,
                            descuento_tipo="porcentaje", descuento_valor=5,
                            comision_tipo="porcentaje", comision_valor=10,
                            activo=True)
        db.session.add(af); db.session.commit()

        # Cliente tiene un pedido cancelado previo — no debería descalificarlo.
        self._mk_pedido(estado="cancelado")
        # Ahora hace su primer pedido "real"
        pedido = self._mk_pedido()
        uso = registrar_uso_afiliado(af, pedido, self.cliente, descuento_aplicado=1.0)
        db.session.commit()
        self.assertGreater(float(uso.comision_generada), 0)


if __name__ == "__main__":
    unittest.main()
