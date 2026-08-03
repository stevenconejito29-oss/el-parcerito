"""Cobertura del flujo del socio con combos, presentaciones y sabores.

Ejercita el parser/builder unificado (`combo_form_parser` + `combo_builder`)
desde el endpoint del socio, incluyendo aprobación con variantes inactivas
y edición idempotente.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db, login_manager
from models import (
    Categoria, ComboGroup, ComboItem, Product, ProductExtraGroup,
    ProductExtraOption, ProductPresentation, Proveedor, ProveedorProducto,
    SiteConfig, User,
)
from routes.admin import admin_bp
from routes.proveedor import proveedor_bp


class PartnerComboVariantsTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="partner-combo-variants",
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
        SiteConfig.set("TIPO_TIENDA", "comida")
        SiteConfig.set("COMBO_MIN_COMPONENTS", "1")

        self.category = Categoria(nombre="Menú", activo=True)
        self.socio = Proveedor(
            nombre="Socio combos", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=15,
        )
        self.otro = Proveedor(
            nombre="Otro socio", activo=True,
            modelo_acuerdo="socio_porcentaje", comision_pct=15,
        )
        db.session.add_all([self.category, self.socio, self.otro])
        db.session.flush()

        self.operator = self._user(
            "Operador", "op@test.invalid", "socio_producto",
            proveedor_id=self.socio.id,
        )
        self.superadmin = self._user(
            "Super", "super@test.invalid", "super_admin",
            telefono="+34600000001",
        )

        # Coca: 2 presentaciones
        self.coca = self._product("Coca", self.socio)
        self.pres_33 = self._presentation(self.coca, "pequeño", "0", orden=0)
        self.pres_50 = self._presentation(self.coca, "mediano", "1.50", orden=1)

        # Hamburguesa: 1 grupo sabor con 3 opciones
        self.hamburguesa = self._product("Hamburguesa", self.socio)
        self.grp_sabor = ProductExtraGroup(
            producto_id=self.hamburguesa.id, nombre="Sabor",
            tipo="sabor", min_selecciones=1, max_selecciones=1, activo=True,
        )
        db.session.add(self.grp_sabor)
        db.session.flush()
        self.sabor_clasica = self._flavor(self.grp_sabor, "Clásica", 0)
        self.sabor_bacon = self._flavor(self.grp_sabor, "Bacon", 1)
        self.sabor_bbq = self._flavor(self.grp_sabor, "BBQ", 2)

        # Papas: sin variantes
        self.papas = self._product("Papas", self.socio)
        # Agua: sin variantes, para tests que necesitan dos productos sin sabor
        self.agua = self._product("Agua", self.socio)

        # Producto ajeno
        self.ajeno = self._product("Ajeno", self.otro)

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
            activo=True, es_combo=False, vertical="comida",
            tipo_entrega="inmediato", modalidad_entrega="ambas",
            canal_preparacion="almacen",
            proveedor_despachador_id=socio.id,
            partner_submission_status="approved",
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(ProveedorProducto(
            proveedor_id=socio.id, producto_id=p.id, stock=10, activo=True,
        ))
        return p

    def _presentation(self, product, tamaño, extra, orden=0, activo=True):
        pres = ProductPresentation(
            producto_id=product.id, tamaño=tamaño,
            precio_extra=extra, activo=activo, orden=orden,
        )
        db.session.add(pres)
        db.session.flush()
        return pres

    def _flavor(self, grp, nombre, orden):
        opt = ProductExtraOption(
            grupo_id=grp.id, nombre=nombre, precio=0,
            max_cantidad=1, orden=orden, activo=True,
        )
        db.session.add(opt)
        db.session.flush()
        return opt

    def _login(self, user):
        with self.client.session_transaction() as session:
            session.clear()
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
            session["_id"] = "test-session"

    def _login_client(self, user):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(user.id)
            s["_fresh"] = True
        return c

    # ── Tests ──

    def test_combo_fijo_con_presentacion_y_sabor_fijos(self):
        self._login(self.operator)
        data = {
            "nombre": "Combo A",
            "precio": "12.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [
                str(self.coca.id), str(self.hamburguesa.id), str(self.papas.id),
            ],
            "comp_cantidad": ["1", "1", "1"],
            "comp_tipo": ["fijo", "fijo", "fijo"],
            "comp_grupo": ["", "", ""],
            "comp_max_sel": ["1", "1", "1"],
            "comp_presentation_mode": ["fijo", "fijo", "fijo"],
            "comp_presentation_id": [str(self.pres_33.id), "", ""],
            "comp_flavor_mode": ["sin_sabor", "fijo", "sin_sabor"],
            "comp_fixed_flavor_id": ["", str(self.sabor_bacon.id), ""],
            "comp_allowed_flavor_ids": ["", "", ""],
            "comp_allowed_presentation_ids": ["", "", ""],
        }
        response = self.client.post("/proveedor/combos/nuevo", data=data)
        self.assertEqual(response.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo A").one()
        self.assertEqual(combo.partner_submission_status, "pending")
        self.assertFalse(combo.activo)
        items = ComboItem.query.filter_by(combo_id=combo.id).order_by(
            ComboItem.orden
        ).all()
        self.assertEqual(len(items), 3)
        by_prod = {it.producto_id: it for it in items}
        self.assertEqual(
            by_prod[self.coca.id].presentation_id, self.pres_33.id
        )
        self.assertEqual(
            by_prod[self.hamburguesa.id].fixed_flavor_option_id,
            self.sabor_bacon.id,
        )
        self.assertIsNone(by_prod[self.papas.id].presentation_id)

    def test_combo_con_presentacion_cliente_elige(self):
        self._login(self.operator)
        allowed = json.dumps([self.pres_33.id, self.pres_50.id])
        data = {
            "nombre": "Combo pres client",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [str(self.coca.id)],
            "comp_cantidad": ["1"],
            "comp_tipo": ["fijo"],
            "comp_grupo": [""],
            "comp_max_sel": ["1"],
            "comp_presentation_mode": ["cliente_elige"],
            "comp_presentation_id": [""],
            "comp_flavor_mode": ["sin_sabor"],
            "comp_fixed_flavor_id": [""],
            "comp_allowed_flavor_ids": [""],
            "comp_allowed_presentation_ids": [allowed],
        }
        response = self.client.post("/proveedor/combos/nuevo", data=data)
        self.assertEqual(response.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo pres client").one()
        item = ComboItem.query.filter_by(combo_id=combo.id).one()
        self.assertEqual(
            {p.id for p in item.allowed_presentations},
            {self.pres_33.id, self.pres_50.id},
        )

    def test_combo_con_sabor_restringido(self):
        self._login(self.operator)
        allowed = json.dumps([self.sabor_clasica.id, self.sabor_bacon.id])
        data = {
            "nombre": "Combo sabor",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [str(self.hamburguesa.id)],
            "comp_cantidad": ["1"],
            "comp_tipo": ["fijo"],
            "comp_grupo": [""],
            "comp_max_sel": ["1"],
            "comp_presentation_mode": ["fijo"],
            "comp_presentation_id": [""],
            "comp_flavor_mode": ["cliente_elige"],
            "comp_fixed_flavor_id": [""],
            "comp_allowed_flavor_ids": [allowed],
            "comp_allowed_presentation_ids": [""],
        }
        response = self.client.post("/proveedor/combos/nuevo", data=data)
        self.assertEqual(response.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo sabor").one()
        item = ComboItem.query.filter_by(combo_id=combo.id).one()
        self.assertEqual(
            {o.id for o in item.allowed_flavor_options},
            {self.sabor_clasica.id, self.sabor_bacon.id},
        )

    def test_combo_con_grupo_seleccion_crea_combogroup(self):
        self._login(self.operator)
        # Dos componentes en un grupo "Elige tu carne" tipo seleccion
        data = {
            "nombre": "Combo elige",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "combo_group_uid": ["grp1"],
            "combo_group_name": ["Elige tu carne"],
            "combo_group_type": ["sel"],
            "combo_group_max_sel": ["1"],
            "combo_group_order": ["0"],
            "comp_prod_id": [str(self.agua.id), str(self.papas.id)],
            "comp_cantidad": ["1", "1"],
            "comp_tipo": ["sel", "sel"],
            "comp_grupo": ["Elige tu carne", "Elige tu carne"],
            "comp_max_sel": ["1", "1"],
            "comp_group_uid": ["grp1", "grp1"],
            "comp_presentation_mode": ["fijo", "fijo"],
            "comp_presentation_id": ["", ""],
            "comp_flavor_mode": ["sin_sabor", "sin_sabor"],
            "comp_fixed_flavor_id": ["", ""],
            "comp_allowed_flavor_ids": ["", ""],
            "comp_allowed_presentation_ids": ["", ""],
        }
        response = self.client.post("/proveedor/combos/nuevo", data=data)
        self.assertEqual(response.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo elige").one()
        grupos = ComboGroup.query.filter_by(
            combo_id=combo.id, tipo="seleccion"
        ).all()
        self.assertEqual(len(grupos), 1)
        grp = grupos[0]
        self.assertEqual(grp.min_selecciones, 1)
        self.assertEqual(grp.max_selecciones, 1)

    def test_componente_de_otro_socio_es_rechazado(self):
        self._login(self.operator)
        # El producto ajeno no está en productos_permitidos → parser
        # levanta ComboParseError y el combo no se persiste.
        data = {
            "nombre": "Combo cross",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [str(self.papas.id), str(self.ajeno.id)],
            "comp_cantidad": ["1", "1"],
            "comp_tipo": ["fijo", "fijo"],
            "comp_grupo": ["", ""],
            "comp_max_sel": ["1", "1"],
            "comp_presentation_mode": ["fijo", "fijo"],
            "comp_presentation_id": ["", ""],
            "comp_flavor_mode": ["sin_sabor", "sin_sabor"],
            "comp_fixed_flavor_id": ["", ""],
            "comp_allowed_flavor_ids": ["", ""],
            "comp_allowed_presentation_ids": ["", ""],
        }
        with patch("routes.proveedor.render_template", return_value="err"):
            response = self.client.post("/proveedor/combos/nuevo", data=data)
        # Se re-renderiza el form con flash de error (200) o
        # se pierde y hace redirect. En ningún caso persiste el combo.
        self.assertIsNone(Product.query.filter_by(nombre="Combo cross").first())

    def test_aprobacion_rechaza_con_presentacion_inactiva(self):
        self._login(self.operator)
        data = {
            "nombre": "Combo pres fija",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [str(self.coca.id)],
            "comp_cantidad": ["1"],
            "comp_tipo": ["fijo"],
            "comp_grupo": [""],
            "comp_max_sel": ["1"],
            "comp_presentation_mode": ["fijo"],
            "comp_presentation_id": [str(self.pres_33.id)],
            "comp_flavor_mode": ["sin_sabor"],
            "comp_fixed_flavor_id": [""],
            "comp_allowed_flavor_ids": [""],
            "comp_allowed_presentation_ids": [""],
        }
        response = self.client.post("/proveedor/combos/nuevo", data=data)
        self.assertEqual(response.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo pres fija").one()

        # Flask-Login cachea current_user en flask.g dentro del app_context.
        # Sin invalidarlo, el switch de operator → superadmin no toma efecto.
        from flask import g
        g.pop("_login_user", None)
        admin = self.app.test_client()
        with admin.session_transaction() as sess:
            sess["_user_id"] = str(self.superadmin.id)
            sess["_fresh"] = True
        # Aprobación con presentación activa: OK
        r = admin.post(
            f"/admin/productos/{combo.id}/revision-socio",
            data={"accion": "aprobar", "nota": "OK"},
        )
        self.assertEqual(r.status_code, 302)
        db.session.refresh(combo)
        self.assertEqual(combo.partner_submission_status, "approved")

        # Desactivar presentación y volver a poner combo en pending para
        # forzar nueva revisión (simulamos un reenvío del socio).
        self.pres_33.activo = False
        combo.partner_submission_status = "pending"
        combo.activo = False
        db.session.commit()

        g.pop("_login_user", None)
        r2 = admin.post(
            f"/admin/productos/{combo.id}/revision-socio",
            data={"accion": "aprobar", "nota": "reintento"},
        )
        self.assertEqual(r2.status_code, 302)
        db.session.refresh(combo)
        self.assertEqual(combo.partner_submission_status, "pending")
        with admin.session_transaction() as sess:
            flashes = [msg for _cat, msg in sess.get("_flashes", [])]
        self.assertTrue(
            any("tamaño" in msg.lower() for msg in flashes),
            f"Se esperaba mensaje sobre 'tamaño'; flashes={flashes}",
        )

    def test_edicion_reemplaza_componentes(self):
        self._login(self.operator)
        # Crear con 2 componentes
        data_create = {
            "nombre": "Combo mutable",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [str(self.papas.id), str(self.agua.id)],
            "comp_cantidad": ["1", "1"],
            "comp_tipo": ["fijo", "fijo"],
            "comp_grupo": ["", ""],
            "comp_max_sel": ["1", "1"],
            "comp_presentation_mode": ["fijo", "fijo"],
            "comp_presentation_id": ["", ""],
            "comp_flavor_mode": ["sin_sabor", "sin_sabor"],
            "comp_fixed_flavor_id": ["", ""],
            "comp_allowed_flavor_ids": ["", ""],
            "comp_allowed_presentation_ids": ["", ""],
        }
        r = self.client.post("/proveedor/combos/nuevo", data=data_create)
        self.assertEqual(r.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo mutable").one()
        self.assertEqual(
            ComboItem.query.filter_by(combo_id=combo.id).count(), 2
        )

        # Editar dejando solo 1 componente
        data_edit = {
            "nombre": "Combo mutable",
            "precio": "10.00",
            "categoria_id": str(self.category.id),
            "modalidad_entrega": "ambas",
            "comp_prod_id": [str(self.papas.id)],
            "comp_cantidad": ["1"],
            "comp_tipo": ["fijo"],
            "comp_grupo": [""],
            "comp_max_sel": ["1"],
            "comp_presentation_mode": ["fijo"],
            "comp_presentation_id": [""],
            "comp_flavor_mode": ["sin_sabor"],
            "comp_fixed_flavor_id": [""],
            "comp_allowed_flavor_ids": [""],
            "comp_allowed_presentation_ids": [""],
        }
        r2 = self.client.post(
            f"/proveedor/combos/{combo.id}/editar", data=data_edit,
        )
        self.assertEqual(r2.status_code, 302)
        items = ComboItem.query.filter_by(combo_id=combo.id).all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].producto_id, self.papas.id)


if __name__ == "__main__":
    unittest.main()
