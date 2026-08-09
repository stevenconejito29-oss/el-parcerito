import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from flask import Flask

from extensions import db, login_manager
from models import Order, RiderLocation, SiteConfig, User
from routes.repartidor import repartidor_bp


class RiderLocationTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / "templates"))
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="rider-location-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SESSION_PROTECTION=None,
        )
        db.init_app(self.app)
        login_manager.init_app(self.app)
        self.app.register_blueprint(repartidor_bp, url_prefix="/repartidor")

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        @self.app.route("/")
        def index():
            return "ok"
        self.app.add_url_rule("/", endpoint="public.index", view_func=index)

        self.ctx = self.app.app_context(); self.ctx.push()
        db.create_all()
        SiteConfig.set("STORE_FEATURE_DELIVERY", "1", descripcion="test")
        self.rider = self._user("Rider", "rider@gps.invalid", "repartidor")
        self.customer = self._user("Cliente", "cliente@gps.invalid", "cliente")
        db.session.commit()
        self.client = self.app.test_client()
        self._login(self.rider)

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def _user(self, name, email, role):
        user = User(nombre=name, email=email, rol=role, activo=True)
        user.set_password("test")
        db.session.add(user); db.session.flush()
        return user

    def _login(self, user):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True

    def _active_order(self):
        order = Order(
            numero_pedido="GPS-0001", cliente_id=self.customer.id,
            subtotal=10, total=10, estado="en_ruta",
            tipo_entrega_cliente="delivery", repartidor_id=self.rider.id,
        )
        db.session.add(order); db.session.commit()
        return order

    def test_rejects_tracking_without_active_route(self):
        response = self.client.post("/repartidor/ubicacion", json={"lat": 37.47, "lng": -5.64})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(db.session.get(RiderLocation, self.rider.id))

    def test_stores_only_latest_precise_point(self):
        self._active_order()
        first = self.client.post("/repartidor/ubicacion", json={"lat": 37.47, "lng": -5.64, "accuracy_m": 18})
        second = self.client.post("/repartidor/ubicacion", json={"lat": 37.48, "lng": -5.63, "accuracy_m": 12})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(RiderLocation.query.count(), 1)
        self.assertAlmostEqual(db.session.get(RiderLocation, self.rider.id).lat, 37.48)

    def test_rejects_imprecise_or_invalid_points(self):
        self._active_order()
        self.assertEqual(self.client.post("/repartidor/ubicacion", json={"lat": 100, "lng": 0}).status_code, 400)
        self.assertEqual(self.client.post("/repartidor/ubicacion", json={"lat": 37.47, "lng": -5.64, "accuracy_m": 400}).status_code, 422)

    def test_delete_removes_last_point(self):
        self._active_order()
        self.client.post("/repartidor/ubicacion", json={"lat": 37.47, "lng": -5.64, "accuracy_m": 10})
        response = self.client.delete("/repartidor/ubicacion")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(db.session.get(RiderLocation, self.rider.id))

    def test_route_optimizer_requires_server_configuration(self):
        first = self._active_order()
        first.direccion_lat, first.direccion_lng = 37.47, -5.64
        second = Order(
            numero_pedido="GPS-0002", cliente_id=self.customer.id,
            subtotal=10, total=10, estado="en_ruta",
            tipo_entrega_cliente="delivery", repartidor_id=self.rider.id,
            direccion_lat=37.49, direccion_lng=-5.62,
        )
        db.session.add(second); db.session.commit()
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("GOOGLE_ROUTES_API_KEY", None)
            response = self.client.post("/repartidor/ruta/optimizar", json={
                "pedido_ids": [first.id, second.id],
                "origin": {"lat": 37.46, "lng": -5.65},
            })
        self.assertEqual(response.status_code, 503)

    @patch("requests.post")
    def test_route_optimizer_uses_road_order_and_keeps_farthest_destination(self, post):
        first = self._active_order()
        first.direccion_lat, first.direccion_lng = 37.47, -5.64
        second = Order(
            numero_pedido="GPS-0002", cliente_id=self.customer.id,
            subtotal=10, total=10, estado="en_ruta",
            tipo_entrega_cliente="delivery", repartidor_id=self.rider.id,
            direccion_lat=37.49, direccion_lng=-5.62,
        )
        third = Order(
            numero_pedido="GPS-0003", cliente_id=self.customer.id,
            subtotal=10, total=10, estado="en_ruta",
            tipo_entrega_cliente="delivery", repartidor_id=self.rider.id,
            direccion_lat=37.48, direccion_lng=-5.63,
        )
        db.session.add_all([second, third]); db.session.commit()
        upstream = Mock()
        upstream.raise_for_status.return_value = None
        upstream.json.return_value = {"routes": [{
            "optimizedIntermediateWaypointIndex": [1, 0],
            "distanceMeters": 4200,
            "duration": "900s",
        }]}
        post.return_value = upstream
        with patch.dict("os.environ", {"GOOGLE_ROUTES_API_KEY": "server-secret"}):
            response = self.client.post("/repartidor/ruta/optimizar", json={
                "pedido_ids": [second.id, first.id, third.id],
                "origin": {"lat": 37.46, "lng": -5.65},
            })
        self.assertEqual(response.status_code, 200)
        # La más lejana (second) queda como destino; Google reordena las otras.
        self.assertEqual(response.get_json()["pedido_ids"], [third.id, first.id, second.id])
        self.assertNotIn("server-secret", str(post.call_args.kwargs["json"]))


if __name__ == "__main__":
    unittest.main()
