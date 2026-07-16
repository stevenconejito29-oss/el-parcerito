"""Tests de la matriz de permisos (permissions.py).

Verifica la política pura sin tocar DB. Usamos `unittest.mock.patch` sobre
`models.AdminFeature.tiene_acceso` para las políticas `feature:X` — así el
suite se aísla incluso cuando el módulo `models` real ya está importado por
otros tests en el mismo proceso (CI ejecuta la batería completa).
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from permissions import ACTIONS, Actor, allow, can_read_order_ticket


class _FeatureStub:
    """Registro thread-local de features concedidas para el suite."""
    def __init__(self):
        self._grants: dict[tuple[int, str], bool] = {}

    def reset(self):
        self._grants.clear()

    def grant(self, user_id: int, slug: str):
        self._grants[(user_id, slug)] = True

    def check(self, user_id, slug):
        return self._grants.get((user_id, slug), False)


_stub = _FeatureStub()


def _patched_tiene_acceso(user_id, slug):
    return _stub.check(user_id, slug)


class PermissionsPureLogicTest(unittest.TestCase):
    def setUp(self):
        _stub.reset()
        # Parchea la comprobación real de features durante toda la clase.
        self._patch = patch(
            "models.AdminFeature.tiene_acceso",
            side_effect=_patched_tiene_acceso,
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

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
            ACTIONS.STORE_WRITE,
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
        _stub.grant(42, "productos")
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
        _stub.grant(42, "productos")
        self.assertFalse(allow(actor, ACTIONS.CATALOG_WRITE))

    def test_feature_gate_requires_user_id(self):
        admin = Actor(rol="admin", user_id=None)
        self.assertFalse(allow(admin, ACTIONS.CATALOG_WRITE))

    # ── ticket operativo por rol y recurso ─────────────────────────
    def test_ticket_action_is_limited_to_operational_roles(self):
        for rol in ("admin", "super_admin", "cocina", "preparacion", "repartidor"):
            self.assertTrue(allow(Actor(rol=rol, user_id=1), ACTIONS.ORDER_TICKET_READ))
        for rol in ("cliente", "proveedor", ""):
            self.assertFalse(allow(Actor(rol=rol, user_id=1), ACTIONS.ORDER_TICKET_READ))

    @patch("services.es_pedido_solo_bar", return_value=False)
    def test_unassigned_ticket_follows_kitchen_or_scheduled_queue(self, _bar):
        cocina = SimpleNamespace(rol="cocina", id=10)
        preparacion = SimpleNamespace(rol="preparacion", id=20)
        inmediato = SimpleNamespace(preparador_id=None, repartidor_id=None, es_programado=False)
        programado = SimpleNamespace(preparador_id=None, repartidor_id=None, es_programado=True)

        self.assertTrue(can_read_order_ticket(cocina, inmediato))
        self.assertFalse(can_read_order_ticket(cocina, programado))
        self.assertTrue(can_read_order_ticket(preparacion, programado))
        self.assertFalse(can_read_order_ticket(preparacion, inmediato))

    def test_assigned_ticket_is_private_to_assigned_operator_or_driver(self):
        pedido = SimpleNamespace(
            preparador_id=10,
            repartidor_id=30,
            es_programado=False,
        )
        self.assertTrue(can_read_order_ticket(SimpleNamespace(rol="cocina", id=10), pedido))
        self.assertFalse(can_read_order_ticket(SimpleNamespace(rol="cocina", id=11), pedido))
        self.assertTrue(can_read_order_ticket(SimpleNamespace(rol="repartidor", id=30), pedido))
        self.assertFalse(can_read_order_ticket(SimpleNamespace(rol="repartidor", id=31), pedido))
        self.assertTrue(can_read_order_ticket(SimpleNamespace(rol="admin", id=40), pedido))
        self.assertTrue(can_read_order_ticket(SimpleNamespace(rol="super_admin", id=50), pedido))


if __name__ == "__main__":
    unittest.main()
