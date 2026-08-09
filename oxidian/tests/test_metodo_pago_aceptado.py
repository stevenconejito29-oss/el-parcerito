"""Guarda de habilitación en `models.metodo_pago_aceptado`.

Cubre el escenario que motivó el helper: retirar Bizum del catálogo
activo sin borrar los pedidos históricos que ya tienen
``metodo_pago='bizum'``. La normalización pura (para lectura) sigue
aceptando bizum; la variante ``aceptado`` (para escritura de pedidos
nuevos) rechaza según SiteConfig.
"""
import unittest
from unittest.mock import patch

from models import normalizar_metodo_pago, metodo_pago_aceptado


class NormalizarSiempreAceptaBizum(unittest.TestCase):
    """Documenta el contrato: la normalización NO consulta SiteConfig.
    Se necesita para renderizar pedidos históricos con bizum aunque el
    negocio lo haya retirado del catálogo activo."""

    def test_normaliza_bizum_como_valor_valido(self):
        self.assertEqual(normalizar_metodo_pago("bizum"), "bizum")
        self.assertEqual(normalizar_metodo_pago("BIZUM"), "bizum")

    def test_legacy_transferencia_sigue_mapeando_a_bizum(self):
        self.assertEqual(normalizar_metodo_pago("transferencia"), "bizum")

    def test_valores_invalidos_siguen_devolviendo_none(self):
        self.assertIsNone(normalizar_metodo_pago("paypal"))
        self.assertIsNone(normalizar_metodo_pago(""))
        self.assertIsNone(normalizar_metodo_pago(None))


class MetodoPagoAceptadoRespetaSiteConfig(unittest.TestCase):
    """Guarda de escritura: se rechaza si SiteConfig lo desactivó."""

    def _con_config(self, valores):
        return patch(
            "models.SiteConfig.get",
            side_effect=lambda k, d="": valores.get(k, d),
        )

    def test_efectivo_habilitado_por_defecto(self):
        with self._con_config({}):
            self.assertEqual(metodo_pago_aceptado("efectivo"), "efectivo")

    def test_tarjeta_habilitada_por_defecto(self):
        with self._con_config({}):
            self.assertEqual(metodo_pago_aceptado("tarjeta"), "tarjeta")

    def test_bizum_deshabilitado_por_defecto(self):
        # El default en config_defaults/store_config es "0" desde
        # 2026-08-09. Sin config explícita, bizum NO se acepta.
        with self._con_config({}):
            self.assertIsNone(metodo_pago_aceptado("bizum"))

    def test_bizum_aceptado_si_admin_lo_reactiva(self):
        with self._con_config({"BIZUM_HABILITADO": "1"}):
            self.assertEqual(metodo_pago_aceptado("bizum"), "bizum")

    def test_efectivo_rechazado_si_admin_lo_apaga(self):
        with self._con_config({"EFECTIVO_HABILITADO": "0"}):
            self.assertIsNone(metodo_pago_aceptado("efectivo"))

    def test_metodo_invalido_devuelve_none(self):
        with self._con_config({}):
            self.assertIsNone(metodo_pago_aceptado("paypal"))
            self.assertIsNone(metodo_pago_aceptado(""))
            self.assertIsNone(metodo_pago_aceptado(None))

    def test_transferencia_legacy_pasa_por_gate_como_bizum(self):
        # transferencia → bizum en normalizar; después la guarda decide.
        with self._con_config({"BIZUM_HABILITADO": "0"}):
            self.assertIsNone(metodo_pago_aceptado("transferencia"))
        with self._con_config({"BIZUM_HABILITADO": "1"}):
            self.assertEqual(metodo_pago_aceptado("transferencia"), "bizum")


if __name__ == "__main__":
    unittest.main()
