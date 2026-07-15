"""Tests del flujo "empleado/admin hace su propio pedido con su número".

Verifica que `buscar_cliente_por_telefono` (fuente única usada por bot y
checkout web) devuelve el usuario correcto aunque su rol no sea
'cliente'. Antes: el bot filtraba estrictamente por rol='cliente' y
disparaba IntegrityError al intentar crear un cliente shadow con el
mismo teléfono.
"""
import unittest

from flask import Flask

from extensions import db
from models import Order, SiteConfig, User


class EmpleadoHacePedidoTest(unittest.TestCase):
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_user(self, nombre, rol, telefono):
        EmpleadoHacePedidoTest._seq += 1
        u = User(
            nombre=nombre,
            email=f"{nombre.lower()}-{EmpleadoHacePedidoTest._seq}@t.invalid",
            telefono=telefono,
            telefono_normalizado=telefono,
            rol=rol,
            activo=True,
        )
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        return u

    def test_empleado_preparacion_encuentra_su_propio_usuario(self):
        # Empleado con rol=preparacion hace un pedido — debe encontrar SU
        # user, no crear uno nuevo (que colisionaría con el UNIQUE).
        from services import buscar_cliente_por_telefono
        empleado = self._mk_user("Preparador", "preparacion", "+34600111111")
        found, tel = buscar_cliente_por_telefono("+34600111111")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, empleado.id)
        self.assertEqual(found.rol, "preparacion")

    def test_admin_encuentra_su_propio_usuario(self):
        from services import buscar_cliente_por_telefono
        admin = self._mk_user("Admin", "admin", "+34600222222")
        found, _ = buscar_cliente_por_telefono("+34600222222")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, admin.id)
        self.assertEqual(found.rol, "admin")

    def test_super_admin_encuentra_su_propio_usuario(self):
        from services import buscar_cliente_por_telefono
        sa = self._mk_user("Super", "super_admin", "+34600333333")
        found, _ = buscar_cliente_por_telefono("+34600333333")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, sa.id)

    def test_cliente_puro_tiene_prioridad_sobre_operador(self):
        # Caso raro pero posible: el UNIQUE es global — si hubiera un cliente
        # y un operador con el mismo teléfono, el cliente gana. En la práctica
        # el UNIQUE lo impide, pero validamos que la prioridad exista.
        # Aquí no podemos crear los dos (UNIQUE), pero sí verificar que si
        # solo hay cliente, se devuelve el cliente.
        from services import buscar_cliente_por_telefono
        self._mk_user("Cliente", "cliente", "+34600444444")
        found, _ = buscar_cliente_por_telefono("+34600444444")
        self.assertEqual(found.rol, "cliente")

    def test_repartidor_encuentra_su_usuario(self):
        from services import buscar_cliente_por_telefono
        rep = self._mk_user("Repa", "repartidor", "+34600555555")
        found, _ = buscar_cliente_por_telefono("+34600555555")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, rep.id)

    def test_none_si_telefono_no_existe(self):
        from services import buscar_cliente_por_telefono
        found, tel = buscar_cliente_por_telefono("+34699999999")
        self.assertIsNone(found)

    def test_telefono_invalido_devuelve_none(self):
        from services import buscar_cliente_por_telefono
        found, tel = buscar_cliente_por_telefono("123")  # muy corto
        self.assertIsNone(found)

    def test_bot_api_bot_delegates_al_helper_de_services(self):
        # El helper local en api_bot.py debe delegar exactamente en el de
        # services. Antes eran dos implementaciones divergentes.
        from routes.api_bot import _cliente_por_telefono
        empleado = self._mk_user("Empleado", "preparacion", "+34600666666")
        found, tel = _cliente_por_telefono("+34600666666")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, empleado.id)

    def test_recupera_identidad_legacy_sin_prefijo_configurado_en_su_momento(self):
        from services import buscar_cliente_por_telefono
        SiteConfig.set("WHATSAPP_COUNTRY_CODE", "34", descripcion="test")
        legacy = self._mk_user("Legacy", "cliente", "+632907709")
        found, canonical = buscar_cliente_por_telefono("+34632907709")
        self.assertEqual(canonical, "+34632907709")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, legacy.id)

    def test_admin_canonico_recupera_historial_cliente_legacy_inequivoco(self):
        from services import buscar_cliente_por_telefono
        SiteConfig.set("WHATSAPP_COUNTRY_CODE", "34", descripcion="test")
        admin = self._mk_user("Admin canonico", "super_admin", "+34622663874")
        legacy = self._mk_user("Cliente legacy", "cliente", "+622663874")
        db.session.add(Order(
            numero_pedido="LEGACY-1",
            cliente_id=legacy.id,
            subtotal=10,
            total=10,
            estado="pendiente",
        ))
        db.session.commit()

        found, canonical = buscar_cliente_por_telefono(admin.telefono)

        self.assertEqual(canonical, "+34622663874")
        self.assertEqual(found.id, legacy.id)


if __name__ == "__main__":
    unittest.main()
