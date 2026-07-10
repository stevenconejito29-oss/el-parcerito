"""Tests de la matriz de permisos (permissions.py).

Verifica la política pura sin tocar DB. Se inyecta un `models` falso en
`sys.modules` antes de importar `permissions`, para que el import diferido
`from models import AdminFeature` dentro de `allow()` no arrastre Flask.
"""
import sys
import types
import unittest


# ── Fake `models` con `AdminFeature` configurable ───────────────────────
_grants: dict[tuple[int, str], bool] = {}


class _FakeAdminFeature:
    @staticmethod
    def tiene_acceso(user_id: int, slug: str) -> bool:
        return _grants.get((user_id, slug), False)


_fake_models = types.ModuleType("models")
_fake_models.AdminFeature = _FakeAdminFeature
sys.modules.setdefault("models", _fake_models)

# Importar DESPUÉS del stub para que el deferred import funcione.
from permissions import ACTIONS, Actor, allow  # noqa: E402


def _grant(user_id: int, slug: str) -> None:
    _grants[(user_id, slug)] = True


def _reset_grants() -> None:
    _grants.clear()


class PermissionsPureLogicTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_grants()

    # ── deny by default ─────────────────────────────────────────────
    def test_null_actor_denied(self):
        for name, action in vars(ACTIONS).items():
            if not isinstance(action, str) or name.startswith("_"):
                continue
            self.assertFalse(allow(None, action), f"None debería denegar {action}")

    def test_unknown_action_denied_for_admin(self):
        admin = Actor(rol="admin", user_id=1)
        self.assertFalse(allow(admin, "action.that.does.not.exist"))

    def test_unknown_action_allowed_for_super_admin(self):
        # super_admin bypassea antes de mirar policy — es aceptable por diseño.
        sa = Actor(rol="super_admin", user_id=9)
        self.assertTrue(allow(sa, "action.that.does.not.exist"))

    # ── super_admin bypass ──────────────────────────────────────────
    def test_super_admin_allowed_everywhere(self):
        sa = Actor(rol="super_admin", user_id=9)
        for action in [
            ACTIONS.CATALOG_WRITE_VERTICAL,
            ACTIONS.STORE_MODE_TOGGLE,
            ACTIONS.CONFIG_WRITE,
            ACTIONS.FINANCE_EXPORT,
            ACTIONS.ZONE_WRITE,
        ]:
            self.assertTrue(allow(sa, action), f"super_admin denegado en {action}")

    def test_privileged_by_env_bypass(self):
        env_actor = Actor(rol="", user_id=None, privileged_by_env=True)
        self.assertTrue(allow(env_actor, ACTIONS.CONFIG_WRITE))
        self.assertTrue(allow(env_actor, ACTIONS.STORE_MODE_TOGGLE))

    # ── super_only ──────────────────────────────────────────────────
    def test_admin_denied_on_super_only_actions(self):
        admin = Actor(rol="admin", user_id=1)
        for action in [
            ACTIONS.CATALOG_WRITE_VERTICAL,
            ACTIONS.STORE_WRITE,
            ACTIONS.STORE_MODE_TOGGLE,
            ACTIONS.STORE_MODULES_TOGGLE,
            ACTIONS.CONFIG_WRITE,
            ACTIONS.FINANCE_EXPORT,
            ACTIONS.ZONE_WRITE,
        ]:
            self.assertFalse(allow(admin, action),
                             f"admin autorizado en super_only {action}")

    def test_other_roles_denied_on_super_only(self):
        for rol in ("repartidor", "preparacion", "proveedor", "cliente", ""):
            actor = Actor(rol=rol, user_id=2)
            self.assertFalse(allow(actor, ACTIONS.CONFIG_WRITE))
            self.assertFalse(allow(actor, ACTIONS.STORE_MODE_TOGGLE))

    # ── admin_read ─────────────────────────────────────────────────
    def test_admin_read_allows_admin_only(self):
        for action in [
            ACTIONS.CATALOG_READ,
            ACTIONS.STORE_READ,
            ACTIONS.ZONE_READ,
            ACTIONS.ZONE_TOGGLE,
        ]:
            self.assertTrue(allow(Actor(rol="admin", user_id=1), action))
            self.assertFalse(allow(Actor(rol="preparacion", user_id=2), action))
            self.assertFalse(allow(Actor(rol="repartidor", user_id=3), action))

    # ── feature:X (con stub AdminFeature) ──────────────────────────
    def test_feature_gate_allows_when_granted(self):
        admin = Actor(rol="admin", user_id=42)
        _grant(42, "productos")
        self.assertTrue(allow(admin, ACTIONS.CATALOG_WRITE))
        self.assertTrue(allow(admin, ACTIONS.STOCK_WRITE))
        self.assertFalse(allow(admin, ACTIONS.MARKETING_WRITE))
        self.assertFalse(allow(admin, ACTIONS.WHATSAPP_SEND))

    def test_feature_gate_denies_when_not_granted(self):
        admin = Actor(rol="admin", user_id=42)
        for action in (ACTIONS.CATALOG_WRITE, ACTIONS.MARKETING_WRITE,
                       ACTIONS.WHATSAPP_SEND, ACTIONS.REPORTS_READ):
            self.assertFalse(allow(admin, action))

    def test_feature_gate_denies_non_admin_even_with_feature_granted(self):
        actor = Actor(rol="preparacion", user_id=42)
        _grant(42, "productos")
        self.assertFalse(allow(actor, ACTIONS.CATALOG_WRITE))

    def test_feature_gate_requires_user_id(self):
        admin = Actor(rol="admin", user_id=None)
        # Aunque otorgáramos features "anónimas", sin user_id no puede comprobarlas.
        self.assertFalse(allow(admin, ACTIONS.CATALOG_WRITE))


if __name__ == "__main__":
    unittest.main()
