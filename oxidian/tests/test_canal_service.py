"""Tests unitarios de canal_service.

Foco en la MATRIZ DE DECISIONES del gate anti-baneo Meta. Los helpers que
tocan BD (`_push_eligible`, `_web_chat_activo`) se mockean; los tests
verifican que la funcion pura `elegir_canal` cumple la especificacion
documentada en docs/CANAL_NOTIFICACIONES.md.
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch


def _cli(cli_id=42, telefono="+34111222333", last_inbound=None):
    return SimpleNamespace(
        id=cli_id, telefono=telefono, nombre="Ana",
        last_wa_inbound_at=last_inbound,
    )


class ElegirCanalTest(unittest.TestCase):

    # 1) Evento transaccional -> siempre WA (bypass total)
    def test_transactional_always_wa_bypasses_gate(self):
        from canal_service import elegir_canal, TRANSACTIONAL_ALWAYS_WA
        with patch("canal_service._get_config") as gc:
            gc.return_value = SimpleNamespace(get=lambda k, d="": "0")
            for ev in TRANSACTIONAL_ALWAYS_WA:
                d = elegir_canal(_cli(), ev)
                self.assertEqual(d.canal, "wa", f"{ev} debe ir por WA")
                self.assertEqual(d.razon, "transactional_always_wa")

    def test_transactional_nunca_cae_a_none(self):
        """Regla dura: ningun evento critico puede quedar sin canal."""
        from canal_service import elegir_canal, TRANSACTIONAL_ALWAYS_WA
        with patch("canal_service._get_config") as gc:
            gc.return_value = SimpleNamespace(get=lambda k, d="": "1")
            for ev in TRANSACTIONAL_ALWAYS_WA:
                d = elegir_canal(_cli(last_inbound=None), ev)
                self.assertNotEqual(d.canal, "none", f"{ev} nunca 'none'")

    # 2) Toggle apagado -> WA (escape hatch legacy)
    def test_gate_desactivado_devuelve_wa(self):
        from canal_service import elegir_canal
        with patch("canal_service._get_config") as gc:
            gc.return_value = SimpleNamespace(get=lambda k, d="": (
                "0" if k == "notif_gate_activo" else ""
            ))
            d = elegir_canal(_cli(), "delivery_en_camino")
            self.assertEqual(d.canal, "wa")
            self.assertEqual(d.razon, "gate_desactivado")

    # 5) push + web
    def test_push_y_web_activos_da_push_web(self):
        from canal_service import elegir_canal
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=True), \
             patch("canal_service._web_chat_activo", return_value=True):
            gc.return_value = SimpleNamespace(get=lambda k, d="": "1" if k == "notif_gate_activo" else "")
            d = elegir_canal(_cli(), "delivery_en_camino")
            self.assertEqual(d.canal, "push+web")
            self.assertTrue(d.metadata["push_ok"])
            self.assertTrue(d.metadata["web_ok"])

    # 6) push solo
    def test_push_sin_web_da_push(self):
        from canal_service import elegir_canal
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=True), \
             patch("canal_service._web_chat_activo", return_value=False):
            gc.return_value = SimpleNamespace(get=lambda k, d="": "1" if k == "notif_gate_activo" else "")
            d = elegir_canal(_cli(), "delivery_en_camino")
            self.assertEqual(d.canal, "push")

    # 7) web solo
    def test_web_sin_push_da_web(self):
        from canal_service import elegir_canal
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=False), \
             patch("canal_service._web_chat_activo", return_value=True):
            gc.return_value = SimpleNamespace(get=lambda k, d="": "1" if k == "notif_gate_activo" else "")
            d = elegir_canal(_cli(), "delivery_en_camino")
            self.assertEqual(d.canal, "web")

    # 8) sin push/web pero inbound WA reciente -> wa (dentro de ventana)
    def test_dentro_ventana_wa_da_wa(self):
        from canal_service import elegir_canal
        reciente = datetime.now(timezone.utc) - timedelta(hours=2)
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=False), \
             patch("canal_service._web_chat_activo", return_value=False):
            gc.return_value = SimpleNamespace(get=lambda k, d="": {
                "notif_gate_activo": "1",
                "notif_ventana_wa_horas": "24",
            }.get(k, d))
            d = elegir_canal(_cli(last_inbound=reciente), "delivery_en_camino")
            self.assertEqual(d.canal, "wa")
            self.assertEqual(d.razon, "fallback_ventana_wa")

    # 9) fallback duro -> none
    def test_sin_nada_da_none(self):
        from canal_service import elegir_canal
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=False), \
             patch("canal_service._web_chat_activo", return_value=False):
            gc.return_value = SimpleNamespace(get=lambda k, d="": {
                "notif_gate_activo": "1",
                "notif_ventana_wa_horas": "24",
            }.get(k, d))
            d = elegir_canal(_cli(last_inbound=None), "delivery_en_camino")
            self.assertEqual(d.canal, "none")

    # Ventana WA configurable respetada
    def test_ventana_wa_expira(self):
        from canal_service import elegir_canal
        viejo = datetime.now(timezone.utc) - timedelta(hours=30)
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=False), \
             patch("canal_service._web_chat_activo", return_value=False):
            gc.return_value = SimpleNamespace(get=lambda k, d="": {
                "notif_gate_activo": "1",
                "notif_ventana_wa_horas": "24",
            }.get(k, d))
            d = elegir_canal(_cli(last_inbound=viejo), "delivery_en_camino")
            self.assertEqual(d.canal, "none")

    # Override JSON por evento respetado (excluye WA)
    def test_override_json_por_evento_excluye_wa(self):
        from canal_service import elegir_canal
        reciente = datetime.now(timezone.utc) - timedelta(hours=2)
        # Aunque hay inbound reciente, si el evento no permite 'wa' cae a none
        with patch("canal_service._get_config") as gc, \
             patch("canal_service._push_eligible", return_value=False), \
             patch("canal_service._web_chat_activo", return_value=False):
            gc.return_value = SimpleNamespace(get=lambda k, d="": {
                "notif_gate_activo": "1",
                "notif_ventana_wa_horas": "24",
                "notif_canales_por_evento": '{"delivery_en_camino":["push","web"]}',
            }.get(k, d))
            d = elegir_canal(_cli(last_inbound=reciente), "delivery_en_camino")
            self.assertEqual(d.canal, "none")

    # Sin cliente -> wa fallback historico
    def test_cliente_none_devuelve_wa(self):
        from canal_service import elegir_canal
        with patch("canal_service._get_config") as gc:
            gc.return_value = SimpleNamespace(get=lambda k, d="": "1" if k == "notif_gate_activo" else "")
            d = elegir_canal(None, "delivery_en_camino")
            self.assertEqual(d.canal, "wa")
            self.assertEqual(d.razon, "cliente_desconocido")


if __name__ == "__main__":
    unittest.main()
