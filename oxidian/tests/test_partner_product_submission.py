"""Flujo de alta de catálogo propiedad de un socio."""
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from extensions import db, login_manager
from models import (
    Categoria, ComboItem, Product, Proveedor, ProveedorProducto, SiteConfig, User,
)
from routes.admin import admin_bp
from routes.proveedor import proveedor_bp


class PartnerProductSubmissionTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__,
            template_folder=str(root / "templates"),
            static_folder=str(root / "static"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="partner-product-test",
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
        self.category = Categoria(nombre="Dulces", activo=True)
        self.partner = Proveedor(
            nombre="Socio cafetero",
            activo=True,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=18,
        )
        self.other_partner = Proveedor(
            nombre="Otro socio",
            activo=True,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=10,
        )
        db.session.add_all([self.category, self.partner, self.other_partner])
        db.session.flush()
        self.operator = self._user(
            "Operador socio",
            "partner@test.invalid",
            "socio_producto",
            proveedor_id=self.partner.id,
        )
        self.superadmin = self._user(
            "Super Admin",
            "super@test.invalid",
            "super_admin",
        )
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

    def _login(self, user):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True

    def _client_for(self, user):
        g.pop("_login_user", None)
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
        return client

    def _submit_product(self):
        return self.client.post(
            "/proveedor/productos/nuevo",
            data={
                "nombre": "Café del socio",
                "descripcion": "Café colombiano de origen.",
                "precio": "12.50",
                "stock": "7",
                "categoria_id": str(self.category.id),
                "origen_pais": "Colombia",
                "modalidad_entrega": "ambas",
                # El backend debe ignorar cualquier intento de cambiar dueño.
                "proveedor_despachador_id": str(self.other_partner.id),
            },
        )

    def test_submission_is_inactive_and_owned_by_authenticated_partner(self):
        self._login(self.operator)
        response = self._submit_product()
        self.assertEqual(response.status_code, 302)

        product = Product.query.filter_by(nombre="Café del socio").one()
        self.assertEqual(product.proveedor_despachador_id, self.partner.id)
        self.assertEqual(product.partner_submission_status, "pending")
        self.assertEqual(product.partner_submitted_by, self.operator.id)
        self.assertFalse(product.activo)
        self.assertEqual(product.vertical, "producto")
        self.assertIsNone(product.precio_costo)

        stock = ProveedorProducto.query.filter_by(
            proveedor_id=self.partner.id,
            producto_id=product.id,
        ).one()
        self.assertEqual(stock.stock, 7)
        self.assertIsNone(stock.precio_costo)

    def test_partner_cannot_mutate_stock_held_by_the_store(self):
        self._login(self.operator)
        self._submit_product()
        product = Product.query.filter_by(nombre="Café del socio").one()
        stock = ProveedorProducto.query.filter_by(producto_id=product.id).one()

        response = self.client.post(
            "/proveedor/inventario",
            data={"accion": "actualizar_sku", "fila_id": stock.id, "stock": "11"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.session.get(ProveedorProducto, stock.id).stock, 7)

    def test_superadmin_approval_is_the_only_publication_step(self):
        self._login(self.operator)
        self._submit_product()
        product = Product.query.filter_by(nombre="Café del socio").one()

        admin_client = self._client_for(self.superadmin)
        response = admin_client.post(
            f"/admin/productos/{product.id}/revision-socio",
            data={"accion": "aprobar", "nota": "Ficha verificada"},
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(product)
        self.assertEqual(product.partner_submission_status, "approved")
        self.assertEqual(product.partner_reviewed_by, self.superadmin.id)
        self.assertTrue(product.activo)

    def test_rejection_requires_a_useful_note(self):
        self._login(self.operator)
        self._submit_product()
        product = Product.query.filter_by(nombre="Café del socio").one()

        admin_client = self._client_for(self.superadmin)
        admin_client.post(
            f"/admin/productos/{product.id}/revision-socio",
            data={"accion": "rechazar", "nota": "no"},
        )
        db.session.refresh(product)
        self.assertEqual(product.partner_submission_status, "pending")
        self.assertFalse(product.activo)

    def test_rejected_product_must_be_resubmitted_before_approval(self):
        self._login(self.operator)
        self._submit_product()
        product = Product.query.filter_by(nombre="Café del socio").one()
        admin_client = self._client_for(self.superadmin)

        admin_client.post(
            f"/admin/productos/{product.id}/revision-socio",
            data={"accion": "rechazar", "nota": "Completa la ficha comercial"},
        )
        db.session.refresh(product)
        self.assertEqual(product.partner_submission_status, "rejected")

        admin_client.post(
            f"/admin/productos/{product.id}/revision-socio",
            data={"accion": "aprobar", "nota": "Intento directo"},
        )
        db.session.refresh(product)
        self.assertEqual(product.partner_submission_status, "rejected")
        self.assertFalse(product.activo)

    def _approved_component(self, name, stock=10):
        product = Product(
            nombre=name,
            descripcion="Componente del socio",
            precio=5,
            categoria_id=self.category.id,
            activo=True,
            es_combo=False,
            vertical="producto",
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canal_preparacion="almacen",
            proveedor_despachador_id=self.partner.id,
            partner_submission_status="approved",
        )
        db.session.add(product)
        db.session.flush()
        db.session.add(ProveedorProducto(
            proveedor_id=self.partner.id,
            producto_id=product.id,
            stock=stock,
            activo=True,
        ))
        return product

    def test_partner_can_propose_combo_only_with_owned_products(self):
        first = self._approved_component("Café")
        second = self._approved_component("Galletas")
        db.session.commit()
        self._login(self.operator)

        response = self.client.post(
            "/proveedor/combos/nuevo",
            data={
                "nombre": "Combo cafetero",
                "descripcion": "Dos productos del mismo socio.",
                "precio": "9.50",
                "categoria_id": str(self.category.id),
                "origen_pais": "Colombia",
                "modalidad_entrega": "ambas",
                "comp_prod_id": [str(first.id), str(second.id)],
                "comp_cantidad": ["1", "2"],
                "comp_tipo": ["fijo", "fijo"],
                "comp_grupo": ["", ""],
                "comp_max_sel": ["1", "1"],
                "comp_presentation_mode": ["fijo", "fijo"],
                "comp_presentation_id": ["", ""],
                "comp_flavor_mode": ["sin_sabor", "sin_sabor"],
                "comp_fixed_flavor_id": ["", ""],
                "comp_allowed_flavor_ids": ["", ""],
                "comp_allowed_presentation_ids": ["", ""],
            },
        )

        self.assertEqual(response.status_code, 302)
        combo = Product.query.filter_by(nombre="Combo cafetero").one()
        self.assertTrue(combo.es_combo)
        self.assertFalse(combo.activo)
        self.assertEqual(combo.partner_submission_status, "pending")
        self.assertEqual(combo.proveedor_despachador_id, self.partner.id)
        self.assertEqual(
            {item.producto_id for item in ComboItem.query.filter_by(combo_id=combo.id)},
            {first.id, second.id},
        )
        self.assertIsNone(ProveedorProducto.query.filter_by(producto_id=combo.id).first())

        # Super Admin puede completar la propuesta con el builder compartido
        # sin que el combo pierda propietario ni quede bloqueado por interpretar
        # sus propios componentes como stock externo.
        admin_client = self._client_for(self.superadmin)
        response = admin_client.post(
            f"/admin/combos/{combo.id}/editar",
            data={
                "nombre": "Combo cafetero revisado",
                "descripcion": "Ficha completada por Super Admin.",
                "precio": "10.00",
                "combo_precio_modo": "fijo",
                "categoria_id": str(self.category.id),
                "origen_pais": "Colombia",
                "vertical": "producto",
                "tipo_entrega": "inmediato",
                "modalidad_entrega": "ambas",
                "canal_preparacion": "almacen",
                "comp_prod_id": [str(first.id), str(second.id)],
                "comp_cantidad": ["1", "2"],
                "comp_tipo": ["fijo", "fijo"],
                "comp_grupo": ["", ""],
                "comp_max_sel": ["1", "1"],
                "comp_presentation_mode": ["fijo", "fijo"],
                "comp_presentation_id": ["", ""],
                "comp_flavor_mode": ["sin_sabor", "sin_sabor"],
                "comp_fixed_flavor_id": ["", ""],
                "comp_allowed_flavor_ids": ["", ""],
                "comp_allowed_presentation_ids": ["", ""],
            },
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(combo)
        self.assertEqual(combo.nombre, "Combo cafetero revisado")
        self.assertEqual(combo.proveedor_despachador_id, self.partner.id)
        self.assertEqual(combo.partner_submission_status, "pending")
        self.assertFalse(combo.activo)

    def test_partner_combo_rejects_component_owned_by_another_partner(self):
        own = self._approved_component("Producto propio")
        foreign = Product(
            nombre="Producto ajeno",
            precio=4,
            categoria_id=self.category.id,
            activo=True,
            es_combo=False,
            proveedor_despachador_id=self.other_partner.id,
        )
        db.session.add(foreign)
        db.session.commit()
        self._login(self.operator)

        with patch("routes.proveedor.render_template", return_value="invalid"):
            self.client.post(
                "/proveedor/combos/nuevo",
                data={
                    "nombre": "Combo inválido",
                    "precio": "8",
                    "categoria_id": str(self.category.id),
                    "modalidad_entrega": "ambas",
                    "comp_prod_id": [str(own.id), str(foreign.id)],
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
                },
            )
        self.assertIsNone(Product.query.filter_by(nombre="Combo inválido").first())


if __name__ == "__main__":
    unittest.main()
