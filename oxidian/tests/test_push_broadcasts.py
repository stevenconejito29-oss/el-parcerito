import json
import unittest
import uuid

from flask import Flask

from extensions import db
from models import NotificationOutbox, PushBroadcast, PushSubscription, User
from push_service import queue_push_broadcast


class PushBroadcastServiceTest(unittest.TestCase):
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
        self.admin = self._user("Admin", "admin")
        customer = self._user("Cliente", "cliente")
        staff = self._user("Cocina", "cocina")
        self._subscription(customer, "cliente", "customer")
        self._subscription(staff, "cocina", "staff")
        self._subscription(customer, "cliente", "inactive", active=False)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _user(self, name, role):
        user = User(
            nombre=name,
            email=f"{name.lower()}@test.invalid",
            rol=role,
            activo=True,
        )
        user.set_password("test")
        db.session.add(user)
        db.session.flush()
        return user

    def _subscription(self, user, role, suffix, active=True):
        db.session.add(PushSubscription(
            user_id=user.id,
            endpoint=f"https://push.example.test/{suffix}",
            p256dh="key_123",
            auth="auth_123",
            rol=role,
            activo=active,
        ))

    def _queue(self, **overrides):
        values = {
            "creator_id": self.admin.id,
            "idempotency_key": str(uuid.uuid4()),
            "title": "Ya abrimos",
            "body": "Tu pedido colombiano está a un toque.",
            "url": "/",
            "audience": "all",
        }
        values.update(overrides)
        return queue_push_broadcast(**values)

    def test_all_audience_creates_one_job_per_active_device(self):
        campaign, created = self._queue()

        self.assertTrue(created)
        self.assertEqual(campaign.destinatarios, 2)
        jobs = NotificationOutbox.query.filter_by(push_broadcast_id=campaign.id).all()
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job.evento == "pwa_broadcast" for job in jobs))
        payload = json.loads(jobs[0].payload_json)["payload"]
        self.assertEqual(payload["tag"], f"broadcast-{campaign.id}")

    def test_audience_is_applied_from_subscription_role_snapshot(self):
        customers, _ = self._queue(audience="customers")
        staff, _ = self._queue(audience="staff")

        self.assertEqual(customers.destinatarios, 1)
        self.assertEqual(staff.destinatarios, 1)

    def test_same_idempotency_key_never_duplicates_jobs(self):
        key = str(uuid.uuid4())
        first, created_first = self._queue(idempotency_key=key)
        second, created_second = self._queue(idempotency_key=key)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(PushBroadcast.query.count(), 1)
        self.assertEqual(NotificationOutbox.query.count(), 2)

    def test_rejects_external_destination_and_oversized_content(self):
        with self.assertRaises(ValueError):
            self._queue(url="https://attacker.invalid/")
        with self.assertRaises(ValueError):
            self._queue(body="x" * 181)
        self.assertEqual(PushBroadcast.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
