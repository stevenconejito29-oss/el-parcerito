"""Verifica que un socio no puede editar productos ni combos de otro socio."""
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db, login_manager
from models import (
    Categoria, ComboGroup, ComboItem, Product, Proveedor, ProveedorProducto,
    SiteConfig, User,
)
from routes.admin import admin_bp
from routes.proveedor import proveedor_bp


class PartnerIsolationTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="partner-isolation",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(admin_bp, url_prefix="/admin")
        self.app.register_blueprint(proveedor_bp, url_prefix="/proveedor")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        SiteConfig.set("TIPO_TIENDA", "producto")

        self.category = Categoria(nombre="Cat", activo=True)
        self.socio_a = Proveedor(
            nombre="Socio A", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=15,
        )
        self.socio_b = Proveedor(
            nombre="Socio B", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=15,
        )
        db.session.add_all([self.category, self.socio_a, self.socio_b])
        db.session.flush()

        self.op_a = self._user(
            "Op A", "opa@test.invalid", "socio_producto",
            proveedor_id=self.socio_a.id,
        )
        self.op_b = self._user(
            "Op B", "opb@test.invalid", "socio_producto",
            proveedor_id=self.socio_b.id,
        )

        self.prod_a = self._product("Producto A", self.socio_a)
        self.prod_b = self._product("Producto B", self.socio_b)
        self.combo_a = self._combo("Combo A", self.socio_a, [self.prod_a])
        self.combo_b = self._combo("Combo B", self.socio_b, [self.prod_b])

        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, nombre, email, rol, **kwargs):
        user = User(nombre=nombre, email=email, rol=rol, **kwargs)
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        return user

    def _product(self, name, socio):
        p = Product(
            nombre=name, precio=5, categoria_id=self.category.id,
            activo=True, es_combo=False, vertical="producto",
            tipo_entrega="inmediato", modalidad_entrega="ambas",
            canal_preparacion="almacen",
            proveedor_despachador_id=socio.id,
            partner_submission_status="pending",
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(ProveedorProducto(
            proveedor_id=socio.id, producto_id=p.id, stock=5, activo=True,
        ))
        return p

    def _combo(self, name, socio, componentes):
        c = Product(
            nombre=name, precio=10, categoria_id=self.category.id,
            activo=False, es_combo=True, tipo_producto="combo",
            vertical="producto", tipo_entrega="inmediato",
            modalidad_entrega="ambas", canal_preparacion="almacen",
            proveedor_despachador_id=socio.id,
            partner_submission_status="pending",
            combo_precio_modo="fijo", combo_precio_base=10,
        )
        db.session.add(c)
        db.session.flush()
        grp = ComboGroup(
            combo_id=c.id, nombre="Base", tipo="fijo",
            min_selecciones=0, max_selecciones=1, orden=0, requerido=True,
        )
        db.session.add(grp)
        db.session.flush()
        for i, comp in enumerate(componentes):
            db.session.add(ComboItem(
                combo_id=c.id, combo_group_id=grp.id,
                producto_id=comp.id, cantidad=1, orden=i, activo=True,
            ))
        return c

    def _login(self, user):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True

    # ── Producto ajeno ──

    def test_get_producto_ajeno_devuelve_404(self):
        self._login(self.op_a)
        response = self.client.get(
            f"/proveedor/productos/{self.prod_b.id}/editar"
        )
        self.assertEqual(response.status_code, 404)

    def test_post_producto_ajeno_devuelve_404(self):
        self._login(self.op_a)
        response = self.client.post(
            f"/proveedor/productos/{self.prod_b.id}/editar",
            data={
                "nombre": "Intento hostil",
                "precio": "3.50",
                "stock": "1",
                "categoria_id": str(self.category.id),
                "modalidad_entrega": "ambas",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_socio_puede_editar_su_producto(self):
        self._login(self.op_a)
        response = self.client.post(
            f"/proveedor/productos/{self.prod_a.id}/editar",
            data={
                "nombre": "Renombrado por dueño",
                "precio": "6.00",
                "stock": "3",
                "categoria_id": str(self.category.id),
                "modalidad_entrega": "ambas",
            },
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.prod_a)
        self.assertEqual(self.prod_a.nombre, "Renombrado por dueño")

    # ── Combo ajeno ──

    def test_get_combo_ajeno_devuelve_404(self):
        self._login(self.op_a)
        response = self.client.get(
            f"/proveedor/combos/{self.combo_b.id}/editar"
        )
        self.assertEqual(response.status_code, 404)

    def test_socio_puede_abrir_su_combo(self):
        self._login(self.op_a)
        with patch("routes.proveedor.render_template", return_value="ok"):
            response = self.client.get(
                f"/proveedor/combos/{self.combo_a.id}/editar"
            )
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
