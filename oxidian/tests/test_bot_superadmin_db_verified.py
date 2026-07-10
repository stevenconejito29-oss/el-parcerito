"""Tests de la política defense-in-depth para el actor admin del bot.

Regla: el env (`OWNER_NUMBER` / `SUPERADMINS`) NUNCA otorga rol super_admin
por sí solo. La BD es la fuente autoritativa. Si el teléfono está en env pero
no hay un `User` activo con rol admin/super_admin en BD → deny + warning.
"""
import os
import unittest
from unittest.mock import patch

from flask import Flask

from extensions import db
from models import User
from routes.api_bot import _resolver_actor_admin_bot, api_bot_bp


class BotSuperadminDBVerifiedAuthTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BOT_API_KEY="test-bot-key",
        )
        db.init_app(self.app)
        self.app.register_blueprint(api_bot_bp, url_prefix="/api/bot")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _mk(self, tel, rol="super_admin", activo=True):
        u = User(
            nombre=f"User {tel[-4:]}",
            email=f"u-{tel[-4:]}@test.invalid",
            telefono=tel,
            telefono_normalizado=tel,
            rol=rol,
            activo=activo,
        )
        u.set_password("test-password")
        db.session.add(u)
        db.session.commit()
        return u

    def test_env_only_sin_user_deny(self):
        """Env tiene 34600111222 pero no hay usuario en BD → deny."""
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600111222", "SUPERADMINS": ""}):
            self.assertIsNone(_resolver_actor_admin_bot("+34600111222"))

    def test_env_mas_user_super_admin_permite(self):
        """Env y BD ambos autorizan → actor super_admin con privileged_by_env."""
        self._mk("+34600111222", rol="super_admin")
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600111222", "SUPERADMINS": ""}):
            actor = _resolver_actor_admin_bot("+34600111222")
            self.assertIsNotNone(actor)
            self.assertEqual(actor.rol, "super_admin")
            self.assertTrue(actor.privileged_by_env)

    def test_user_admin_sin_env_permite_admin(self):
        """Sin env pero con usuario admin en BD → actor admin (no super_admin)."""
        self._mk("+34600333444", rol="admin")
        with patch.dict(os.environ, {"OWNER_NUMBER": "", "SUPERADMINS": ""}):
            actor = _resolver_actor_admin_bot("+34600333444")
            self.assertIsNotNone(actor)
            self.assertEqual(actor.rol, "admin")
            self.assertFalse(actor.privileged_by_env)

    def test_user_inactivo_deny(self):
        """Usuario existe pero inactivo → deny aunque env autorice."""
        self._mk("+34600555666", rol="super_admin", activo=False)
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600555666", "SUPERADMINS": ""}):
            self.assertIsNone(_resolver_actor_admin_bot("+34600555666"))

    def test_telefono_desconocido_deny(self):
        """Teléfono sin match en BD ni en env → deny."""
        self._mk("+34600111222", rol="super_admin")
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600111222", "SUPERADMINS": ""}):
            self.assertIsNone(_resolver_actor_admin_bot("+34699999999"))


if __name__ == "__main__":
    unittest.main()
