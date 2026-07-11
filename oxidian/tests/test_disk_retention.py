"""Tests de la poda de tablas de crecimiento continuo.

Cierre del bug de disco: `notification_outbox` e `idempotency_keys` crecían
sin límite en producción (funciones existían pero nadie las llamaba).

`purgar_registros_antiguos` centraliza la política y respeta:
- Retención configurable via `SiteConfig.NOTIFICATION_OUTBOX_RETENTION_DAYS`
- Cap defensivo 7-365 días.
- Solo borra `estado in (sent, failed)`, NUNCA pendientes.
"""
import unittest
from datetime import datetime, timedelta

from flask import Flask

from extensions import db
from models import NotificationOutbox, SiteConfig, IdempotencyKey


class DiskRetentionTest(unittest.TestCase):
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

    def _mk_outbox(self, estado, enviado_en, canal="whatsapp", evento="test"):
        job = NotificationOutbox(
            canal=canal,
            evento=evento,
            destinatario="+34600000001",
            payload_json='{"telefono":"+34600000001","mensaje":"x"}',
            estado=estado,
            intentos=1,
            max_intentos=3,
            enviado_en=enviado_en,
        )
        db.session.add(job)
        db.session.commit()
        return job

    def test_poda_sent_viejos(self):
        from services import purgar_registros_antiguos
        ahora = datetime.utcnow()
        viejo = self._mk_outbox("sent", ahora - timedelta(days=40))
        reciente = self._mk_outbox("sent", ahora - timedelta(days=5))
        pendiente = self._mk_outbox("pending", None)

        res = purgar_registros_antiguos(now=ahora)

        # Solo el sent > 30d se borró.
        self.assertEqual(res["notification_outbox"], 1)
        self.assertIsNone(db.session.get(NotificationOutbox, viejo.id))
        self.assertIsNotNone(db.session.get(NotificationOutbox, reciente.id))
        self.assertIsNotNone(db.session.get(NotificationOutbox, pendiente.id))

    def test_poda_failed_viejos(self):
        from services import purgar_registros_antiguos
        ahora = datetime.utcnow()
        failed_viejo = self._mk_outbox("failed", ahora - timedelta(days=45))
        failed_reciente = self._mk_outbox("failed", ahora - timedelta(days=10))

        res = purgar_registros_antiguos(now=ahora)

        self.assertEqual(res["notification_outbox"], 1)
        self.assertIsNone(db.session.get(NotificationOutbox, failed_viejo.id))
        self.assertIsNotNone(db.session.get(NotificationOutbox, failed_reciente.id))

    def test_no_toca_pendientes_aunque_viejos(self):
        # Un pendiente/processing sin enviar sigue siendo trabajo válido —
        # NUNCA debe borrarse aunque su creado_en sea antiguo.
        from services import purgar_registros_antiguos
        ahora = datetime.utcnow()
        pendiente = self._mk_outbox("pending", None)
        pendiente.creado_en = ahora - timedelta(days=90)  # muy viejo
        db.session.commit()

        res = purgar_registros_antiguos(now=ahora)

        self.assertEqual(res["notification_outbox"], 0)
        self.assertIsNotNone(db.session.get(NotificationOutbox, pendiente.id))

    def test_retencion_configurable(self):
        from services import purgar_registros_antiguos
        ahora = datetime.utcnow()

        # Retención = 7 días.
        SiteConfig.set("NOTIFICATION_OUTBOX_RETENTION_DAYS", "7", descripcion="test")
        db.session.commit()

        borderline = self._mk_outbox("sent", ahora - timedelta(days=5))  # < 7d → safe
        antiguo = self._mk_outbox("sent", ahora - timedelta(days=10))    # > 7d → borra

        res = purgar_registros_antiguos(now=ahora)

        self.assertEqual(res["notification_outbox"], 1)
        self.assertIsNotNone(db.session.get(NotificationOutbox, borderline.id))
        self.assertIsNone(db.session.get(NotificationOutbox, antiguo.id))

    def test_cap_defensivo_retencion(self):
        from services import purgar_registros_antiguos
        ahora = datetime.utcnow()

        # Retención = 500 → capea a 365. Registros > 365d se borran.
        SiteConfig.set("NOTIFICATION_OUTBOX_RETENTION_DAYS", "500", descripcion="test")
        db.session.commit()

        muy_antiguo = self._mk_outbox("sent", ahora - timedelta(days=400))

        res = purgar_registros_antiguos(now=ahora)

        self.assertEqual(res["notification_outbox"], 1)
        self.assertIsNone(db.session.get(NotificationOutbox, muy_antiguo.id))

    def test_retencion_invalida_fallback_30(self):
        from services import purgar_registros_antiguos
        ahora = datetime.utcnow()

        SiteConfig.set("NOTIFICATION_OUTBOX_RETENTION_DAYS", "abc", descripcion="test")
        db.session.commit()

        antiguo_35 = self._mk_outbox("sent", ahora - timedelta(days=35))
        reciente_25 = self._mk_outbox("sent", ahora - timedelta(days=25))

        res = purgar_registros_antiguos(now=ahora)

        # Fallback default 30d.
        self.assertEqual(res["notification_outbox"], 1)
        self.assertIsNone(db.session.get(NotificationOutbox, antiguo_35.id))
        self.assertIsNotNone(db.session.get(NotificationOutbox, reciente_25.id))

    def test_idempotency_purge_deshabilitable(self):
        from services import purgar_registros_antiguos
        SiteConfig.set("IDEMPOTENCY_PURGE_ENABLED", "0", descripcion="test")
        db.session.commit()

        # Con purga deshabilitada, idempotency_keys queda en 0 en el resultado.
        res = purgar_registros_antiguos()
        self.assertEqual(res["idempotency_keys"], 0)


if __name__ == "__main__":
    unittest.main()
