"""HTTP tests para la gestión admin de `ProductBatch`.

Cubre `/admin/encargos/lotes` y sus dos acciones POST:
    * Actualizar tope máximo de tandas (con validación de que no puede
      quedar por debajo de las ya vendidas — evitaría overbooking retro).
    * Cerrar lote manualmente (marca `agotado` sin cancelar pedidos).

Estas rutas son la interfaz operativa para que admin controle capacidad
y disponibilidad de encargos sin bajar a SQL.
"""
import unittest
from datetime import date, timedelta
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import Product, ProductBatch, SiteConfig, User
from routes.admin import admin_bp


class AdminLotesTest(unittest.TestCase):
    _seq = 0

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(admin_bp, url_prefix="/admin")

        @login_manager.user_loader
        def _load(uid):
            return db.session.get(User, int(uid))

        @self.app.route("/")
        def _root():
            return "ok"
        self.app.add_url_rule("/", endpoint="public.index", view_func=_root)

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        AdminLotesTest._seq += 1
        self.admin_user = User(
            nombre=f"Admin{self._seq}",
            email=f"a{self._seq}@t.invalid",
            rol="admin",
            telefono=f"+3460100{self._seq:04d}",
            activo=True,
        )
        self.admin_user.set_password("t")
        db.session.add(self.admin_user); db.session.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["_user_id"] = str(self.admin_user.id)
            s["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk_batch(self, maximo=10, vendidas=0, estado="abierto"):
        AdminLotesTest._seq += 1
        p = Product(
            nombre=f"Prod {self._seq}", precio=5.0, activo=True,
            tipo_entrega="programado", cantidad_por_lote=4,
        )
        db.session.add(p); db.session.commit()
        b = ProductBatch(
            producto_id=p.id,
            fecha_entrega=date.today() + timedelta(days=7),
            cantidad_por_tanda=4,
            cantidad_maxima_tandas=maximo,
            cantidad_vendida_tandas=vendidas,
            estado=estado,
        )
        db.session.add(b); db.session.commit()
        return b

    # Nota: el render completo depende de context processors (`brand`,
    # etc.) inicializados en `app.py`. Aquí probamos solo las
    # transiciones de estado (POST endpoints), que son la lógica crítica.

    def test_actualizar_tope_aumenta_y_reabre_agotado(self):
        b = self._mk_batch(maximo=3, vendidas=3, estado="agotado")
        r = self.client.post(
            f"/admin/encargos/lotes/{b.id}/tope",
            data={"cantidad_maxima_tandas": "10"},
        )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(b)
        self.assertEqual(b.cantidad_maxima_tandas, 10)
        self.assertEqual(b.estado, "abierto", "subir tope reabre agotado")

    def test_actualizar_tope_vacio_es_ilimitado(self):
        b = self._mk_batch(maximo=5)
        r = self.client.post(
            f"/admin/encargos/lotes/{b.id}/tope",
            data={"cantidad_maxima_tandas": ""},
        )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(b)
        self.assertIsNone(b.cantidad_maxima_tandas)

    def test_actualizar_tope_bajo_las_vendidas_rechaza(self):
        b = self._mk_batch(maximo=10, vendidas=5)
        r = self.client.post(
            f"/admin/encargos/lotes/{b.id}/tope",
            data={"cantidad_maxima_tandas": "3"},
        )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(b)
        self.assertEqual(b.cantidad_maxima_tandas, 10, "no debe cambiar")

    def test_cerrar_lote_lo_marca_agotado(self):
        b = self._mk_batch(maximo=10, vendidas=2)
        r = self.client.post(f"/admin/encargos/lotes/{b.id}/cerrar")
        self.assertEqual(r.status_code, 302)
        db.session.refresh(b)
        self.assertEqual(b.estado, "agotado")

    def test_cerrar_lote_listo_es_rechazado(self):
        b = self._mk_batch(maximo=5, vendidas=5, estado="listo")
        r = self.client.post(f"/admin/encargos/lotes/{b.id}/cerrar")
        self.assertEqual(r.status_code, 302)
        db.session.refresh(b)
        self.assertEqual(b.estado, "listo", "no baja de listo a agotado")


if __name__ == "__main__":
    unittest.main()
