import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from flask import Flask, session
from werkzeug.datastructures import MultiDict

from extensions import db
from models import (
    ExtraCatalogItem,
    ComboItem,
    Product,
    ProductExtraGroup,
    ProductExtraOption,
    ProductPresentation,
    Proveedor,
    ProveedorProducto,
    OrderItem,
    SiteConfig,
    Stock,
    User,
    ZonaEntrega,
    metadata_item_pedido,
    metadata_componente_combo,
)
from routes.admin import _sync_catalog_extras, _sync_catalog_flavors, _sync_presentaciones
from routes.admin import _parsear_campos_producto
from routes.public import (
    _build_items_from_carrito,
    _cart_compatibility,
    _parse_product_extras,
    _product_extras_payload,
    public_bp,
)
from routes.api_bot import api_bot_bp
from routes.pos import _pos_product_option_config
from product_options_service import (
    product_option_catalog_payload,
    validate_combo_component_flavors,
    validate_product_option_selection,
)
from product_presentations_service import (
    product_presentation_catalog_payload,
    validate_product_presentation_selection,
)


class ProductExtrasWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            CART_MAX_QTY=20,
            SKIP_DELIVERY_VALIDATION=True,
            BOT_API_KEY="test-bot-key",
        )
        db.init_app(self.app)
        self.app.register_blueprint(public_bp)
        self.app.register_blueprint(api_bot_bp, url_prefix="/api/bot")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.product = Product(
            nombre="Hamburguesa extras QA",
            precio=Decimal("5.00"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canal_preparacion="cocina",
        )
        db.session.add(self.product)
        db.session.flush()
        db.session.add(Stock(producto_id=self.product.id, cantidad=20))
        self.group = ProductExtraGroup(
            producto_id=self.product.id,
            nombre="Ingredientes",
            min_selecciones=0,
            max_selecciones=3,
        )
        db.session.add(self.group)
        db.session.flush()
        self.cheese = ProductExtraOption(
            grupo_id=self.group.id, nombre="Queso", precio=Decimal("1.50"), max_cantidad=2
        )
        self.sauce = ProductExtraOption(
            grupo_id=self.group.id, nombre="Salsa", precio=Decimal("0.00"), max_cantidad=1
        )
        db.session.add_all([self.cheese, self.sauce])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_extra_quantities_are_validated_and_priced_per_product_unit(self):
        selected, error = _parse_product_extras(
            self.product,
            MultiDict({f"extra_qty_{self.cheese.id}": "2", f"extra_qty_{self.sauce.id}": "1"}),
        )
        self.assertIsNone(error)
        rows, extra_total = _product_extras_payload(self.product, selected)
        self.assertEqual(extra_total, 3.0)
        self.assertEqual({row["nombre"] for row in rows}, {"Queso", "Salsa"})

        with self.app.test_request_context():
            session["carrito"] = {str(self.product.id): 2}
            session["carrito_origen"] = "propio"
            session["extras_selecciones"] = {str(self.product.id): selected}
            items, subtotal = _build_items_from_carrito(session["carrito"])
        self.assertEqual(items[0]["precio_unit"], 8.0)
        self.assertEqual(items[0]["subtotal"], 16.0)
        self.assertEqual(subtotal, 16.0)

    def test_store_cart_can_mix_own_and_capital_partner_inventory(self):
        partner = Proveedor(
            nombre="Socio capital QA",
            activo=True,
            modelo_acuerdo="socio_porcentaje",
            comision_pct=20,
        )
        partner_product = Product(
            nombre="Producto financiado",
            precio=Decimal("4.00"),
            activo=True,
            tipo_entrega="inmediato",
            modalidad_entrega="ambas",
            canal_preparacion="almacen",
            proveedor_despachador=partner,
            stock_mostrar_en_web=True,
        )
        db.session.add_all([partner, partner_product])
        db.session.flush()
        db.session.add(ProveedorProducto(
            proveedor_id=partner.id,
            producto_id=partner_product.id,
            stock=8,
            activo=True,
        ))
        db.session.commit()

        own_response = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        partner_response = self.client.post(
            f"/carrito/agregar/{partner_product.id}",
            data={"cantidad": "1", "origen": f"proveedor:{partner.id}"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(own_response.get_json()["ok"])
        self.assertTrue(partner_response.get_json()["ok"])
        with self.client.session_transaction() as browser_session:
            cart = dict(browser_session["carrito"])
            self.assertEqual(browser_session["carrito_origen"], "propio")

        with self.app.test_request_context():
            session["carrito"] = cart
            session["carrito_origen"] = "propio"
            items, subtotal = _build_items_from_carrito(cart)
        self.assertEqual(len(items), 2)
        self.assertEqual(
            {item["origen"] for item in items},
            {"propio", f"proveedor:{partner.id}"},
        )
        self.assertEqual(subtotal, 9.0)

    def test_group_and_option_limits_reject_invalid_quantities(self):
        _, error = _parse_product_extras(
            self.product,
            MultiDict({f"extra_qty_{self.cheese.id}": "3"}),
        )
        self.assertIn("Cantidad inválida", error)

        self.group.min_selecciones = 1
        self.group.max_selecciones = 1
        db.session.commit()
        _, error = _parse_product_extras(self.product, MultiDict())
        self.assertIn("al menos 1", error)
        self.product.canjeable_con_puntos = True
        self.product.puntos_para_canje = 500
        db.session.commit()
        self.assertFalse(self.product.canje_directo_disponible())

    def test_combo_requires_compatible_flavor_for_every_offered_size(self):
        self.group.tipo = "sabor"
        self.group.min_selecciones = 1
        self.group.max_selecciones = 1
        small = ProductPresentation(
            producto_id=self.product.id,
            tamaño="pequeño",
            activo=True,
            flavor_rules_enabled=True,
            flavor_min_selections=1,
            flavor_max_selections=1,
        )
        large = ProductPresentation(
            producto_id=self.product.id,
            tamaño="grande",
            activo=True,
            flavor_rules_enabled=True,
            flavor_min_selections=1,
            flavor_max_selections=1,
        )
        db.session.add_all([small, large])
        db.session.flush()
        small.allowed_flavor_options = [self.cheese]
        large.allowed_flavor_options = [self.sauce]
        db.session.commit()

        error = validate_combo_component_flavors(
            self.product,
            [small, large],
            flavor_mode="cliente_elige",
            allowed_flavor_ids=[self.cheese.id],
        )
        self.assertIn("Grande", error)

        self.assertIsNone(validate_combo_component_flavors(
            self.product,
            [small, large],
            flavor_mode="cliente_elige",
            allowed_flavor_ids=[self.cheese.id, self.sauce.id],
        ))
        self.assertIn("exige sabor", validate_combo_component_flavors(
            self.product,
            [small],
            flavor_mode="sin_sabor",
        ))

    def test_same_product_keeps_each_configuration_as_a_separate_line(self):
        first = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio", f"extra_qty_{self.cheese.id}": "1"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(first.get_json()["ok"])
        second = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio", f"extra_qty_{self.sauce.id}": "1"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(second.get_json()["ok"])
        with self.client.session_transaction() as sess:
            self.assertEqual(len(sess["carrito"]), 2)
            self.assertEqual(len(sess["extras_selecciones"]), 2)

    def test_catalog_selection_is_reused_by_products_and_keeps_snapshot_fields(self):
        bacon = ExtraCatalogItem(
            nombre="Bacon crujiente", precio=Decimal("2.25"), max_cantidad=2, activo=True
        )
        db.session.add(bacon)
        db.session.commit()
        form = MultiDict([
            ("extras_catalog_present", "1"),
            ("extra_catalog_ids", str(bacon.id)),
            ("extras_max_selecciones", "2"),
        ])
        self.assertIsNone(_sync_catalog_extras(self.product, form))
        db.session.commit()

        linked = ProductExtraOption.query.filter_by(catalog_item_id=bacon.id).one()
        self.assertEqual(linked.grupo.producto_id, self.product.id)
        self.assertEqual(linked.nombre, "Bacon crujiente")
        self.assertEqual(float(linked.precio), 2.25)
        self.assertEqual(linked.grupo.max_selecciones, 2)

    def test_flavor_is_reusable_required_and_snapshotted_separately(self):
        mango = ExtraCatalogItem(
            nombre="Mango", tipo="sabor", precio=Decimal("0"), max_cantidad=1, activo=True
        )
        lulo = ExtraCatalogItem(
            nombre="Lulo", tipo="sabor", precio=Decimal("0"), max_cantidad=1, activo=True
        )
        db.session.add_all([mango, lulo])
        db.session.commit()
        form = MultiDict([
            ("flavors_catalog_present", "1"),
            ("flavor_catalog_ids", str(mango.id)),
            ("flavor_catalog_ids", str(lulo.id)),
            ("flavors_required", "1"),
        ])
        self.assertIsNone(_sync_catalog_flavors(self.product, form))
        db.session.commit()

        flavor_group = ProductExtraGroup.query.filter_by(
            producto_id=self.product.id, tipo="sabor"
        ).one()
        self.assertEqual((flavor_group.min_selecciones, flavor_group.max_selecciones), (1, 1))
        mango_option = ProductExtraOption.query.filter_by(
            grupo_id=flavor_group.id, catalog_item_id=mango.id
        ).one()
        self.assertEqual(float(mango_option.precio), 0.0)

        selected, error = _parse_product_extras(self.product, MultiDict())
        self.assertEqual(selected, {})
        self.assertIn("Elige un sabor", error)

        selected, error = _parse_product_extras(
            self.product,
            MultiDict({f"flavor_group_{flavor_group.id}": str(mango_option.id)}),
        )
        self.assertIsNone(error)
        rows, total = _product_extras_payload(self.product, selected)
        self.assertEqual(total, 0.0)
        self.assertEqual(rows[0]["tipo"], "sabor")

        with self.app.test_request_context():
            session["carrito"] = {str(self.product.id): 1}
            session["carrito_origen"] = "propio"
            session["extras_selecciones"] = {str(self.product.id): selected}
            items, _ = _build_items_from_carrito(session["carrito"])
        self.assertEqual(items[0]["sabores"][0]["nombre"], "Mango")
        self.assertEqual(
            items[0]["metadata"]["sabores"]["opciones"][0]["nombre"], "Mango"
        )
        snapshot = metadata_item_pedido(self.product, items[0]["metadata"])
        order_item = OrderItem(
            producto_id=self.product.id,
            cantidad=1,
            precio_unit=Decimal("5"),
            subtotal=Decimal("5"),
            metadata_json=json.dumps(snapshot),
        )
        self.assertEqual(order_item.selected_flavor_names, ["Mango"])
        self.assertTrue(order_item.display_has_selectable_flavors)
        self.assertTrue(order_item.display_flavor_required)

        catalog = product_option_catalog_payload(self.product)
        flavor_payload = next(group for group in catalog if group["tipo"] == "sabor")
        self.assertEqual(flavor_payload["min"], 1)
        self.assertEqual({option["nombre"] for option in flavor_payload["opciones"]}, {"Mango", "Lulo"})

        bot_response = self.client.get(
            f"/api/bot/producto/{self.product.id}",
            headers={"X-Bot-Key": "test-bot-key"},
        )
        self.assertEqual(bot_response.status_code, 200)
        bot_product = bot_response.get_json()["producto"]
        self.assertTrue(bot_product["requiere_sabor"])
        self.assertEqual(
            {option["nombre"] for option in bot_product["sabores"]},
            {"Mango", "Lulo"},
        )

    def test_flavor_selection_cannot_reference_another_product(self):
        other = Product(nombre="Otro", precio=Decimal("2"), activo=True)
        db.session.add(other)
        db.session.flush()
        group = ProductExtraGroup(
            producto_id=other.id, nombre="Sabor", tipo="sabor",
            min_selecciones=1, max_selecciones=1,
        )
        db.session.add(group)
        db.session.flush()
        option = ProductExtraOption(
            grupo_id=group.id, nombre="Guayaba", precio=Decimal("0"), max_cantidad=1
        )
        db.session.add(option)
        db.session.commit()

        selected, _, _, error = validate_product_option_selection(
            self.product, {str(option.id): 1}
        )
        self.assertEqual(selected, {})
        self.assertIn("no está disponible", error)

    def test_flavor_distribution_and_availability_follow_selected_size(self):
        flavors = [
            ExtraCatalogItem(
                nombre=name, tipo="sabor", precio=Decimal("0"),
                max_cantidad=1, activo=True,
            )
            for name in ("Mango", "Lulo", "Guayaba", "Café", "Maracuyá")
        ]
        db.session.add_all(flavors)
        db.session.commit()
        flavor_form = MultiDict([
            ("flavors_catalog_present", "1"),
            *(("flavor_catalog_ids", str(flavor.id)) for flavor in flavors),
            ("flavors_required", "1"),
            ("flavors_max_selecciones", "5"),
        ])
        self.assertIsNone(_sync_catalog_flavors(self.product, flavor_form))
        db.session.flush()
        options = {
            option.catalog_item_id: option
            for option in ProductExtraOption.query.join(ProductExtraGroup).filter(
                ProductExtraGroup.producto_id == self.product.id,
                ProductExtraGroup.tipo == "sabor",
            ).all()
        }
        presentation_form = MultiDict([
            ("pres_pequeño_activo", "1"),
            ("pres_pequeño_extra", "0"),
            ("pres_pequeño_flavor_rules", "1"),
            ("pres_pequeño_flavor_min", "5"),
            ("pres_pequeño_flavor_max", "5"),
            *(("pres_pequeño_flavor_catalog_ids", str(flavor.id)) for flavor in flavors),
            ("pres_mediano_activo", "1"),
            ("pres_mediano_extra", "1"),
            ("pres_mediano_flavor_rules", "1"),
            ("pres_mediano_flavor_min", "4"),
            ("pres_mediano_flavor_max", "4"),
            *(("pres_mediano_flavor_catalog_ids", str(flavor.id)) for flavor in flavors[:4]),
            ("pres_grande_activo", "1"),
            ("pres_grande_extra", "2"),
            ("pres_grande_flavor_rules", "1"),
            ("pres_grande_flavor_min", "2"),
            ("pres_grande_flavor_max", "2"),
            *(("pres_grande_flavor_catalog_ids", str(flavor.id)) for flavor in flavors[:2]),
        ])
        self.assertIsNone(_sync_presentaciones(self.product, presentation_form))
        db.session.commit()

        small = ProductPresentation.query.filter_by(
            producto_id=self.product.id, tamaño="pequeño"
        ).one()
        large = ProductPresentation.query.filter_by(
            producto_id=self.product.id, tamaño="grande"
        ).one()
        selection = {
            str(options[flavors[0].id].id): 2,
            str(options[flavors[1].id].id): 2,
            str(options[flavors[2].id].id): 1,
        }
        normalized, rows, _, error = validate_product_option_selection(
            self.product, selection, small
        )
        self.assertIsNone(error)
        self.assertEqual(sum(row["cantidad"] for row in rows), 5)
        self.assertEqual(normalized, selection)

        _, _, _, error = validate_product_option_selection(
            self.product, selection, large
        )
        self.assertIn("no está disponible", error)
        _, _, _, error = validate_product_option_selection(
            self.product, {str(options[flavors[0].id].id): 1}, large
        )
        self.assertIn("Distribuye 2", error)

        catalog = product_presentation_catalog_payload(self.product)
        policies = {row["tamaño"]: row["flavor_policy"] for row in catalog}
        self.assertEqual(policies["pequeño"]["max"], 5)
        self.assertEqual(policies["mediano"]["max"], 4)
        self.assertEqual(policies["grande"]["max"], 2)
        self.assertEqual(len(policies["grande"]["allowed_option_ids"]), 2)

        self.product.es_combo = True
        self.product.tipo_producto = "combo"
        db.session.commit()
        _, combo_rows, _, combo_error = validate_product_option_selection(
            self.product,
            {
                str(options[flavors[0].id].id): 1,
                str(options[flavors[1].id].id): 1,
            },
            large,
        )
        self.assertIsNone(combo_error)
        self.assertEqual(sum(row["cantidad"] for row in combo_rows), 2)

    def test_required_flavor_cannot_be_saved_without_available_choices(self):
        product = Product(nombre="Sin sabores", precio=Decimal("3"), activo=True)
        db.session.add(product)
        db.session.flush()
        error = _sync_catalog_flavors(product, MultiDict([
            ("flavors_catalog_present", "1"),
            ("flavors_required", "1"),
        ]))
        self.assertIn("Selecciona al menos un sabor", error)

    def test_presentation_is_required_priced_snapshotted_and_exposed_to_bot(self):
        form = MultiDict([
            ("pres_pequeño_activo", "1"),
            ("pres_pequeño_extra", "-1.00"),
            ("pres_grande_activo", "1"),
            ("pres_grande_extra", "2.50"),
        ])
        self.assertIsNone(_sync_presentaciones(self.product, form))
        db.session.commit()
        large = ProductPresentation.query.filter_by(
            producto_id=self.product.id, tamaño="grande", activo=True
        ).one()

        selected, error = validate_product_presentation_selection(self.product, "")
        self.assertIsNone(selected)
        self.assertIn("Elige un tamaño", error)
        selected, error = validate_product_presentation_selection(self.product, large.id)
        self.assertIsNone(error)
        self.assertEqual(selected.tamaño, "grande")

        missing = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio"},
            headers={"X-Ajax": "1"},
        )
        self.assertFalse(missing.get_json()["ok"])
        added = self.client.post(
            f"/carrito/agregar/{self.product.id}",
            data={"cantidad": "1", "origen": "propio", "presentation_size": "GRANDE"},
            headers={"X-Ajax": "1"},
        )
        self.assertTrue(added.get_json()["ok"])

        with self.client.session_transaction() as sess:
            cart = dict(sess["carrito"])
        line_key = next(iter(cart))
        with self.app.test_request_context():
            session["carrito"] = cart
            session["carrito_origen"] = "propio"
            session["presentaciones_carrito"] = {line_key: "grande"}
            items, subtotal = _build_items_from_carrito(cart)
        self.assertEqual(items[0]["precio_unit"], 7.5)
        self.assertEqual(subtotal, 7.5)
        snapshot = metadata_item_pedido(self.product, items[0]["metadata"])
        order_item = OrderItem(
            producto_id=self.product.id,
            cantidad=1,
            precio_unit=Decimal("7.50"),
            subtotal=Decimal("7.50"),
            metadata_json=json.dumps(snapshot),
        )
        self.assertEqual(order_item.selected_presentation_label, "Grande")
        self.assertEqual(order_item.selected_presentation_extra, 2.5)

        catalog = product_presentation_catalog_payload(self.product)
        self.assertEqual(
            {row["tamaño"] for row in catalog},
            {"normal", "pequeño", "grande"},
        )
        pos_group = next(
            group for group in _pos_product_option_config(self.product)["grupos"]
            if group["tipo"] == "presentacion"
        )
        self.assertEqual((pos_group["min"], pos_group["max"]), (1, 1))
        self.assertEqual(
            {option["selection_kind"] for option in pos_group["opciones"]},
            {"presentation"},
        )
        bot_response = self.client.get(
            f"/api/bot/producto/{self.product.id}",
            headers={"X-Bot-Key": "test-bot-key"},
        )
        self.assertEqual(
            {row["tamaño"] for row in bot_response.get_json()["producto"]["presentaciones"]},
            {"normal", "pequeño", "grande"},
        )

    def test_presentation_rejects_negative_final_price_without_partial_changes(self):
        error = _sync_presentaciones(self.product, MultiDict([
            ("pres_pequeño_activo", "1"),
            ("pres_pequeño_extra", "-6.00"),
            ("pres_grande_activo", "1"),
            ("pres_grande_extra", "1.00"),
        ]))
        self.assertIn("no puede ser negativo", error)
        self.assertEqual(ProductPresentation.query.filter_by(producto_id=self.product.id).count(), 0)

    def test_combo_component_keeps_its_configured_presentation_in_snapshot_and_price(self):
        presentation = ProductPresentation(
            producto_id=self.product.id, tamaño="grande",
            precio_extra=Decimal("2.00"), activo=True,
        )
        combo = Product(
            nombre="Combo con tamaño", precio=Decimal("10.00"),
            combo_precio_base=Decimal("10.00"), combo_precio_modo="descuento_porcentaje",
            combo_descuento_pct=Decimal("10"), activo=True, es_combo=True,
            tipo_producto="combo",
        )
        db.session.add_all([presentation, combo])
        db.session.flush()
        item = ComboItem(
            combo_id=combo.id, producto_id=self.product.id, cantidad=1,
            presentation_id=presentation.id, es_seleccionable=False,
        )
        db.session.add(item)
        db.session.commit()

        self.assertEqual(float(combo.calcular_precio_base_componentes()), 7.0)
        self.assertEqual(float(combo.precio_combo_para_seleccion([])), 6.3)
        snapshot = metadata_item_pedido(combo, {
            "combo": {"componentes": [
                metadata_componente_combo(item)
            ], "selecciones": []}
        })
        component = snapshot["combo"]["componentes"][0]
        self.assertEqual(component["presentacion"]["label"], "Grande")
        self.assertEqual(component["presentacion"]["extra"], 2.0)

    def test_browser_coordinates_resolve_delivery_zone_without_trusting_an_address(self):
        db.session.add(ZonaEntrega(
            nombre="Centro QA", precio_envio=Decimal("2.50"), activo=True,
            centro_lat=37.3891, centro_lng=-5.9845, radio_km=5,
        ))
        db.session.commit()
        with patch("services.geocodificar_direccion", return_value=(37.39, -5.98)):
            inside = self.client.post("/api/check-address", json={
                "direccion": "Calle Mayor 5", "lat": 37.39, "lng": -5.98, "accuracy": 15,
            })
        self.assertEqual(inside.status_code, 200)
        self.assertTrue(inside.get_json()["ok"])
        self.assertEqual(inside.get_json()["zona"]["nombre"], "Centro QA")
        with patch("services.geocodificar_direccion", return_value=(40.4168, -3.7038)):
            outside = self.client.post("/api/check-address", json={
                "direccion": "Calle Mayor 5", "lat": 40.4168, "lng": -3.7038, "accuracy": 15,
            })
        self.assertFalse(outside.get_json()["ok"])

    def test_check_address_blocks_all_branches_when_delivery_is_disabled(self):
        SiteConfig.set("FEATURE_DELIVERY", "0")
        db.session.commit()

        by_coordinates = self.client.post("/api/check-address", json={
            "direccion": "Calle Mayor 5", "lat": 37.39, "lng": -5.98, "accuracy": 15,
        })
        by_text = self.client.post("/api/check-address", json={"direccion": "Calle Sierpes 1, Sevilla"})

        self.assertEqual(by_coordinates.status_code, 403)
        self.assertEqual(by_text.status_code, 403)

    def test_chatbot_coverage_uses_the_same_zone_resolver(self):
        zone = ZonaEntrega(
            nombre="Centro bot QA", precio_envio=Decimal("2.75"), activo=True,
            centro_lat=37.4736, centro_lng=-5.6438, radio_km=2,
        )
        db.session.add(zone)
        SiteConfig.set("VALIDAR_RADIO_ENTREGA", "1")
        db.session.commit()

        with patch("services.geocodificar_direccion", return_value=(37.474, -5.644)):
            response = self.client.get(
                "/api/bot/cobertura?direccion=Calle%20Real%201%2C%20Carmona",
                headers={"X-Bot-Key": "test-bot-key"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cobertura"]["zona_id"], zone.id)
        self.assertEqual(payload["cobertura"]["zona_nombre"], "Centro bot QA")
        self.assertEqual(payload["metodo_cobertura"], "radio")

    def test_chatbot_coverage_accepts_shared_whatsapp_location(self):
        zone = ZonaEntrega(
            nombre="Ubicación WhatsApp QA", precio_envio=Decimal("2.75"), activo=True,
            centro_lat=37.4736, centro_lng=-5.6438, radio_km=2,
        )
        db.session.add(zone)
        db.session.commit()

        response = self.client.get(
            "/api/bot/cobertura?lat=37.474&lon=-5.644&accuracy=25",
            headers={"X-Bot-Key": "test-bot-key"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cobertura"]["zona_id"], zone.id)
        self.assertEqual(
            payload["cobertura"]["metodo_cobertura"],
            "ubicacion_dispositivo",
        )

    def test_cart_does_not_offer_delivery_without_an_active_zone(self):
        SiteConfig.set("FEATURE_DELIVERY", "1")
        SiteConfig.set("FEATURE_RECOGIDA", "0")
        db.session.commit()

        compatibility = _cart_compatibility(
            [self.product], check_zone_availability=True,
        )

        self.assertFalse(compatibility["ok"])
        self.assertNotIn("delivery", compatibility["fulfillment_options"])
        self.assertEqual(
            compatibility["issues"][0]["code"],
            "delivery_no_active_zones",
        )

    def test_points_code_verification_does_not_enumerate_customers(self):
        customer = User(
            nombre="Cliente puntos",
            email="puntos@test.invalid",
            telefono="+34610009999",
            rol="cliente",
            activo=True,
            puntos=100,
        )
        customer.set_password("test-only-password")
        customer.generar_cod_puntos()
        db.session.add(customer)
        db.session.commit()

        unknown = self.client.post(
            "/puntos/verificar-codigo",
            json={"telefono": "+34619999999", "codigo": "000000"},
        ).get_json()
        wrong = self.client.post(
            "/puntos/verificar-codigo",
            json={"telefono": customer.telefono, "codigo": "000000"},
        ).get_json()

        self.assertFalse(unknown["ok"])
        self.assertFalse(wrong["ok"])
        self.assertEqual(unknown["msg"], wrong["msg"])
        self.assertNotIn("Cliente no encontrado", unknown["msg"])

    def test_consultar_saldo_bot_no_genera_ni_expone_otp(self):
        customer = User(
            nombre="Cliente saldo",
            email="saldo@test.invalid",
            telefono="+34610008888",
            rol="cliente",
            activo=True,
            puntos=250,
        )
        customer.set_password("test-only-password")
        db.session.add(customer)
        db.session.commit()

        response = self.client.get(
            "/api/bot/puntos?telefono=34610008888",
            headers={"X-Bot-Key": "test-bot-key"},
        )
        payload = response.get_json()
        db.session.refresh(customer)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["puntos"], 250)
        self.assertNotIn("codigo_verificacion", payload)
        self.assertIsNone(customer.cod_puntos)

    def test_retail_product_import_defaults_to_warehouse_channel(self):
        SiteConfig.set("TIPO_TIENDA", "producto")
        db.session.commit()

        fields, error = _parsear_campos_producto(MultiDict({
            "nombre": "Camiseta sin canal explícito",
            "precio": "19.90",
            "modalidad_entrega": "ambas",
            "tipo_entrega": "inmediato",
        }))

        self.assertIsNone(error)
        self.assertEqual(fields["canal_preparacion"], "almacen")


if __name__ == "__main__":
    unittest.main()
