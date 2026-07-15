"""Tests del override `TIENDA_FORZAR_ABIERTA` — permite al admin abrir
la tienda desde WhatsApp aunque estemos fuera de horario comercial.

Antes: `tienda_abierta_en_horario` solo aceptaba `forzada_cerrada`. El
bot admin podía "cerrar" pero al pedir "abrir" fuera de franja horaria
el flag se limpiaba y la tienda seguía cerrada por horario. El fundador
veía "OK, abierta" en el bot pero los pedidos seguían rechazados.

Ahora hay dos flags simétricos con precedencia clara:
    1. FORZAR_CERRADA gana sobre todo (emergencia).
    2. FORZAR_ABIERTA ignora el horario (evento especial).
    3. Sin overrides → respeta HH:MM.
"""
import unittest

from services import tienda_abierta_en_horario


class ForzarAbiertaTest(unittest.TestCase):

    def test_horario_dentro_abre(self):
        self.assertTrue(tienda_abierta_en_horario("09:00", "22:00", ahora="12:00"))

    def test_horario_fuera_cierra(self):
        self.assertFalse(tienda_abierta_en_horario("09:00", "22:00", ahora="23:30"))

    def test_forzar_cerrada_prevalece_sobre_horario(self):
        # Dentro del horario pero cerrada por bot admin (emergencia).
        self.assertFalse(tienda_abierta_en_horario(
            "09:00", "22:00", ahora="12:00", forzada_cerrada=True,
        ))

    def test_forzar_abierta_ignora_horario_cerrado(self):
        """El caso que reportó el fundador: fuera de horario, admin
        pulsa 'abrir', la tienda debe aceptar pedidos."""
        self.assertTrue(tienda_abierta_en_horario(
            "09:00", "22:00", ahora="23:30", forzada_abierta=True,
        ))

    def test_forzar_cerrada_gana_sobre_forzar_abierta(self):
        """Precedencia: si por error el admin dejó ambos flags activos,
        la seguridad (cerrada) gana. Evita accidentes."""
        self.assertFalse(tienda_abierta_en_horario(
            "09:00", "22:00", ahora="12:00",
            forzada_cerrada=True, forzada_abierta=True,
        ))

    def test_ventana_nocturna_con_forzar_abierta(self):
        """Ventanas nocturnas (20:00-02:00) siguen funcionando y el
        override de apertura funciona en la banda intermedia."""
        # A las 03:00 la ventana 20:00-02:00 está cerrada. Forzada abre.
        self.assertFalse(tienda_abierta_en_horario("20:00", "02:00", ahora="03:00"))
        self.assertTrue(tienda_abierta_en_horario(
            "20:00", "02:00", ahora="03:00", forzada_abierta=True,
        ))

    def test_horario_dentro_no_necesita_forzar_abierta(self):
        """No es error tener FORZAR_ABIERTA=True dentro de horario;
        simplemente confirma."""
        self.assertTrue(tienda_abierta_en_horario(
            "09:00", "22:00", ahora="12:00", forzada_abierta=True,
        ))


if __name__ == "__main__":
    unittest.main()
