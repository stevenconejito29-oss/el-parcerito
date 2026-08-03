"""Landing del socio_producto: contadores y saldo del mes."""
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from extensions import db, login_manager
from models import (
    Categoria, Product, Proveedor, ProveedorProducto, SiteConfig, User,
)
from routes.auth import auth_bp
from routes.proveedor import proveedor_bp


class PartnerDashboardTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="partner-dashboard",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(auth_bp, url_prefix="/auth")
        self.app.register_blueprint(proveedor_bp, url_prefix="/proveedor")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        @self.app.context_processor
        def _minimal_ctx():
            # admin_base.html accede a `brand.*` y `ui.*`. Poblamos lo mínimo
            # para que Jinja no rompa; el contenido concreto no importa aquí.
            class _Bag:
                def __getattr__(self, name):
                    return ""
                def __contains__(self, k):
                    return False
                def __getitem__(self, k):
                    return ""
                def get(self, k, default=None):
                    return default
            bag = _Bag()
            return {
                "brand": bag, "ui": bag, "asset_version": "test",
                "features": {"delivery": True, "recogida": True, "pedidos_programados": True},
                "csp_nonce": lambda: "",
                "csrf_token": lambda: "",
            }

        self.app.jinja_env.filters.setdefault("upload_url", lambda v: v or "")

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.cat = Categoria(nombre="Cat", activo=True)
        self.socio = Proveedor(
            nombre="Socio dash", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=20,
        )
        db.session.add_all([self.cat, self.socio])
        db.session.flush()
        self.op = User(
            nombre="Op", email="op@t.invalid", rol="socio_producto",
            proveedor_id=self.socio.id,
        )
        self.op.set_password("x")
        db.session.add(self.op)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user):
        g.pop("_login_user", None)
        with self.client.session_transaction() as s:
            s.clear()
            s["_user_id"] = str(user.id)
            s["_fresh"] = True

    def _prod(self, nombre, status, es_combo=False):
        p = Product(
            nombre=nombre, precio=5, categoria_id=self.cat.id,
            activo=(status == "approved"),
            es_combo=es_combo,
            proveedor_despachador_id=self.socio.id,
            partner_submission_status=status,
            stock_mostrar_en_web=True,
        )
        db.session.add(p)
        db.session.flush()
        if not es_combo:
            db.session.add(ProveedorProducto(
                proveedor_id=self.socio.id, producto_id=p.id,
                stock=5, activo=True,
            ))
        db.session.commit()
        return p

    def test_dashboard_cuenta_propuestas_por_estado(self):
        self._prod("Pend 1", "pending")
        self._prod("Pend 2", "pending")
        self._prod("Aprobado", "approved")
        self._prod("Rechazado", "rejected")
        self._prod("Combo pend", "pending", es_combo=True)

        self._login(self.op)
        captured = {}

        def fake_render(template, **ctx):
            captured["template"] = template
            captured.update(ctx)
            return "OK"

        with patch("routes.proveedor.render_template", side_effect=fake_render):
            r = self.client.get("/proveedor/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["template"], "proveedor/dashboard.html")
        conteo = captured["conteo"]
        self.assertEqual(conteo["pending"], 3)  # 2 productos + 1 combo
        self.assertEqual(conteo["approved"], 1)
        self.assertEqual(conteo["rejected"], 1)
        self.assertEqual(conteo["combos_pending"], 1)

    def test_dashboard_no_accesible_para_bar_clasico(self):
        bar = Proveedor(
            nombre="Bar clásico", activo=True,
            modelo_acuerdo="stock_proveedor", comision_pct=0,
        )
        db.session.add(bar)
        db.session.flush()
        user = User(
            nombre="Bar op", email="bar@t.invalid", rol="proveedor",
            proveedor_id=bar.id,
        )
        user.set_password("x")
        db.session.add(user)
        db.session.commit()
        self._login(user)
        r = self.client.get("/proveedor/")
        # socio_capital_required exige modelo_acuerdo="socio_porcentaje"
        self.assertEqual(r.status_code, 403)

    def test_login_redirige_socio_al_dashboard(self):
        # REDIRECT_POR_ROL["socio_producto"] apunta a proveedor.dashboard
        from routes.auth import REDIRECT_POR_ROL
        self.assertEqual(REDIRECT_POR_ROL["socio_producto"], "proveedor.dashboard")


if __name__ == "__main__":
    unittest.main()
