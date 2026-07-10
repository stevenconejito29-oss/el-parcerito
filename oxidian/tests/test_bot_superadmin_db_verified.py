"""Tests de la política defense-in-depth para el actor admin del bot.

Regla: el env (`OWNER_NUMBER` / `SUPERADMINS`) NUNCA otorga rol super_admin
por sí solo. La BD es la fuente autoritativa. Si el teléfono está en env pero
no hay un `User` activo con rol admin/super_admin en BD → deny + warning.
"""
import os
import unittest
from unittest.mock import patch

from app import create_app
from extensions import db
from models import User


class BotSuperadminDBVerifiedAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("BOT_API_KEY", "test-bot-key")
        os.environ.setdefault("SECRET_KEY", "test-secret")
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        cls.app_ctx = cls.app.app_context()
        cls.app_ctx.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_ctx.pop()

    def setUp(self):
        # Limpiar users entre tests para independencia.
        User.query.delete()
        db.session.commit()

    def _mk_super(self, tel="+34600111222"):
        u = User(
            nombre="Super",
            email=f"sa-{tel[-4:]}@test.invalid",
            telefono=tel,
            telefono_normalizado=tel,
            rol="super_admin",
            activo=True,
        )
        u.set_password("test-password")
        db.session.add(u)
        db.session.commit()
        return u

    def test_env_only_sin_user_deny(self):
        """Env tiene 34600111222 pero no hay usuario en BD → deny + warning."""
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600111222", "SUPERADMINS": ""}):
            from routes.api_bot import _resolver_actor_admin_bot
            actor = _resolver_actor_admin_bot("+34600111222")
            self.assertIsNone(actor)

    def test_env_mas_user_super_admin_permite(self):
        """Env y BD ambos autorizan → actor super_admin."""
        self._mk_super("+34600111222")
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600111222", "SUPERADMINS": ""}):
            from routes.api_bot import _resolver_actor_admin_bot
            actor = _resolver_actor_admin_bot("+34600111222")
            self.assertIsNotNone(actor)
            self.assertEqual(actor.rol, "super_admin")
            self.assertTrue(actor.privileged_by_env)

    def test_user_admin_sin_env_permite_admin(self):
        """Sin env pero con usuario admin en BD → actor admin (no super_admin)."""
        adm = User(
            nombre="Admin",
            email="admin@test.invalid",
            telefono="+34600333444",
            telefono_normalizado="+34600333444",
            rol="admin",
            activo=True,
        )
        adm.set_password("test-password")
        db.session.add(adm)
        db.session.commit()
        with patch.dict(os.environ, {"OWNER_NUMBER": "", "SUPERADMINS": ""}):
            from routes.api_bot import _resolver_actor_admin_bot
            actor = _resolver_actor_admin_bot("+34600333444")
            self.assertIsNotNone(actor)
            self.assertEqual(actor.rol, "admin")
            self.assertFalse(actor.privileged_by_env)

    def test_user_inactivo_deny(self):
        """Usuario existe pero inactivo → deny aunque env autorice."""
        u = self._mk_super("+34600555666")
        u.activo = False
        db.session.commit()
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600555666", "SUPERADMINS": ""}):
            from routes.api_bot import _resolver_actor_admin_bot
            actor = _resolver_actor_admin_bot("+34600555666")
            self.assertIsNone(actor)

    def test_telefono_desconocido_deny(self):
        """Teléfono sin match en BD ni en env → deny."""
        self._mk_super("+34600111222")
        with patch.dict(os.environ, {"OWNER_NUMBER": "34600111222", "SUPERADMINS": ""}):
            from routes.api_bot import _resolver_actor_admin_bot
            actor = _resolver_actor_admin_bot("+34699999999")
            self.assertIsNone(actor)


if __name__ == "__main__":
    unittest.main()
