"""Contrato puro del selector modular de delivery."""
import unittest

from delivery_mode_service import (
    ErrorPlanDelivery,
    MODE_CONFIG,
    ModoDelivery,
    modos_delivery_activos,
    resolver_plan_delivery,
)


class DeliveryModeServiceTest(unittest.TestCase):
    def test_switch_options_never_disable_both_delivery_paths(self):
        self.assertEqual(set(MODE_CONFIG), {"inmediato", "franjas", "mixto"})
        self.assertTrue(all(option["inmediato"] or option["franjas"] for option in MODE_CONFIG.values()))

    def test_default_is_immediate_only(self):
        modes = modos_delivery_activos(lambda _key, default: default)
        self.assertEqual(modes, {"inmediato": True, "franjas": False})
        self.assertEqual(resolver_plan_delivery(modos=modes).modo, ModoDelivery.INMEDIATO)

    def test_slots_only_requires_a_slot(self):
        modes = {"inmediato": False, "franjas": True}
        with self.assertRaisesRegex(ErrorPlanDelivery, "Elige una franja"):
            resolver_plan_delivery(modos=modes)
        plan = resolver_plan_delivery("42", modos=modes)
        self.assertEqual((plan.modo, plan.slot_id), (ModoDelivery.FRANJA, 42))

    def test_rejects_slot_when_slots_module_is_off(self):
        with self.assertRaisesRegex(ErrorPlanDelivery, "franjas no está disponible"):
            resolver_plan_delivery("9", modos={"inmediato": True, "franjas": False})


if __name__ == "__main__":
    unittest.main()
