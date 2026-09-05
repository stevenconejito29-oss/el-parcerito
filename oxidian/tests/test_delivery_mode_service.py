"""Contrato puro del selector modular de delivery."""
import unittest

from delivery_mode_service import (
    ErrorPlanDelivery,
    MODE_CONFIG,
    ModoDelivery,
    contexto_operativo_delivery,
    modos_delivery_activos,
    panel_operativo_por_rol,
    permite_nuevo_pedido_inmediato,
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

    def test_operation_context_distinguishes_new_work_from_pending_work(self):
        config = {"delivery_inmediato_activo": "1", "delivery_franjas_activo": "1"}
        operation = contexto_operativo_delivery(
            config_reader=lambda key, default: config.get(key, default),
            feature_reader=lambda: {"delivery": False},
        )
        self.assertEqual(operation["modo"], "pausado")
        self.assertFalse(operation["acepta_nuevo_trabajo"])
        self.assertTrue(operation["inmediato"])
        self.assertTrue(operation["franjas"])

    def test_operation_context_reports_each_enabled_mode(self):
        for immediate, slots, expected in (
            (True, False, "inmediato"),
            (False, True, "franjas"),
            (True, True, "mixto"),
        ):
            operation = contexto_operativo_delivery(
                config_reader=lambda key, _default, values={
                    "delivery_inmediato_activo": "1" if immediate else "0",
                    "delivery_franjas_activo": "1" if slots else "0",
                }: values[key],
                feature_reader=lambda: {"delivery": True},
            )
            self.assertEqual(operation["modo"], expected)
            self.assertTrue(operation["acepta_nuevo_trabajo"])

    def test_operational_panels_change_with_delivery_mode(self):
        immediate = {"inmediato": True, "franjas": False}
        slots = {"inmediato": False, "franjas": True}
        mixed = {"inmediato": True, "franjas": True}
        self.assertEqual(panel_operativo_por_rol("repartidor", modes=immediate), "repartidor.ruta")
        self.assertEqual(panel_operativo_por_rol("repartidor", modes=slots), "repartidor.franjas_panel")
        self.assertEqual(panel_operativo_por_rol("cocina", modes=slots), "preparador.franjas_operacion")
        self.assertEqual(panel_operativo_por_rol("cocina", modes=mixed), "preparador.franjas_operacion")

    def test_immediate_work_is_closed_when_only_slots_are_enabled(self):
        config = {"delivery_inmediato_activo": "0", "delivery_franjas_activo": "1"}
        self.assertFalse(permite_nuevo_pedido_inmediato(
            config_reader=lambda key, default: config.get(key, default),
            feature_reader=lambda: {"delivery": True},
        ))


if __name__ == "__main__":
    unittest.main()
