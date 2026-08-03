"""Product.visible_ahora respeta la zona Europe/Madrid."""
import unittest
from datetime import datetime, time
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import Categoria, Product


class ProductVisibilityTzTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(
            __name__, template_folder=str(root / "templates"),
        )
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="tz-test",
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.cat = Categoria(nombre="X", activo=True)
        db.session.add(self.cat)
        db.session.flush()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _prod(self, ini, fin):
        p = Product(
            nombre="P", precio=1, categoria_id=self.cat.id, activo=True,
            hora_inicio_visibilidad=ini, hora_fin_visibilidad=fin,
        )
        db.session.add(p)
        db.session.flush()
        return p

    def test_visible_usa_hora_de_madrid_no_del_servidor(self):
        p = self._prod(time(12, 0), time(16, 0))

        # Servidor cree que son las 23:00 UTC; en Madrid (verano UTC+2) son las
        # 01:00 → producto NO debe estar visible pese a la hora local del server.
        class FakeDT:
            @classmethod
            def now(cls, tz=None):
                # Verano en Madrid: UTC+2.
                if tz is not None:
                    return datetime(2026, 7, 1, 1, 0)  # 01:00 Madrid
                return datetime(2026, 7, 1, 23, 0)  # 23:00 servidor UTC

        with patch("models.datetime", FakeDT):
            self.assertFalse(p.visible_ahora)

    def test_visible_dentro_de_franja_madrid(self):
        p = self._prod(time(12, 0), time(16, 0))

        class FakeDT:
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return datetime(2026, 7, 1, 14, 30)  # 14:30 Madrid
                return datetime(2026, 7, 1, 12, 30)  # 12:30 UTC

        with patch("models.datetime", FakeDT):
            self.assertTrue(p.visible_ahora)

    def test_franja_nocturna_cruza_medianoche(self):
        p = self._prod(time(22, 0), time(2, 0))

        class FakeDT:
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return datetime(2026, 7, 1, 1, 15)  # 01:15 Madrid dentro
                return datetime(2026, 6, 30, 23, 15)

        with patch("models.datetime", FakeDT):
            self.assertTrue(p.visible_ahora)

    def test_sin_horario_siempre_visible(self):
        p = self._prod(None, None)
        self.assertTrue(p.visible_ahora)


if __name__ == "__main__":
    unittest.main()
