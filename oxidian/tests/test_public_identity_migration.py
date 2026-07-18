import unittest

from flask import Flask

from extensions import db
from models import SiteConfig
from scripts.apply_schema_migrations import (
    _migrate_public_identity_canasta_granitos,
    _migrate_public_nostalgia_copy,
)


class PublicIdentityMigrationTest(unittest.TestCase):
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

    def test_updates_legacy_defaults_without_overwriting_custom_copy(self):
        SiteConfig.set("UI_CART_TITLE", "Tu carrito")
        SiteConfig.set("UI_LOYALTY_UNIT_PLURAL", "semillitas propias")
        db.session.commit()

        _migrate_public_identity_canasta_granitos()
        db.session.commit()

        self.assertEqual(SiteConfig.get("UI_CART_TITLE"), "Tu canasta")
        self.assertEqual(
            SiteConfig.get("UI_LOYALTY_UNIT_PLURAL"),
            "semillitas propias",
        )

    def test_updates_default_nostalgia_copy_and_preserves_custom_title(self):
        SiteConfig.set("UI_HEADER_MEMORY_LINE", "Sabor colombiano, cerquita de ti")
        SiteConfig.set("UI_HERO_TITLE", "Mi eslogan propio")
        db.session.commit()

        _migrate_public_nostalgia_copy()
        db.session.commit()

        self.assertEqual(
            SiteConfig.get("UI_HEADER_MEMORY_LINE"),
            "Colombia, cerquita de ti",
        )
        self.assertEqual(SiteConfig.get("UI_HERO_TITLE"), "Mi eslogan propio")


if __name__ == "__main__":
    unittest.main()
