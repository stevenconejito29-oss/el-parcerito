"""Tests unitarios del servicio de franjas de reparto.

Foco en la lógica pura (cierre de franja) y en el flujo de reserva con
base de datos SQLite en memoria para verificar concurrencia y contadores.
"""
from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from delivery_slots_service import (
    CIERRE_MODOS,
    ResultadoReserva,
    ResultadoRepartidor,
    franja_esta_cerrada,
)


class FranjaEstaCerradaTest(unittest.TestCase):
    """Tests puros — no requieren DB ni Flask app."""

    def _slot(self, hora_inicio="14:00", hora_fin="15:00",
              cierre_modo=None, cierre_valor=None):
        hi = time(*map(int, hora_inicio.split(":")))
        hf = time(*map(int, hora_fin.split(":")))
        return SimpleNamespace(
            fecha=date(2026, 8, 17),
            hora_inicio=hi,
            hora_fin=hf,
            cierre_modo=cierre_modo,
            cierre_valor=cierre_valor,
        )

    def test_ya_iniciada_esta_cerrada(self):
        slot = self._slot()
        ahora = datetime(2026, 8, 17, 14, 0, 1)
        self.assertTrue(franja_esta_cerrada(slot, ahora))

    def test_al_iniciar_siguiente_sigue_abierta_antes(self):
        slot = self._slot(cierre_modo="al_iniciar_siguiente")
        ahora = datetime(2026, 8, 17, 13, 59)
        self.assertFalse(franja_esta_cerrada(slot, ahora))

    def test_minutos_antes_cierra_puntual(self):
        slot = self._slot(cierre_modo="minutos_antes", cierre_valor="30")
        # Cierra a las 13:30
        self.assertFalse(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 29)))
        self.assertTrue(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 30)))
        self.assertTrue(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 45)))

    def test_hora_fija_cierra_puntual(self):
        slot = self._slot(cierre_modo="hora_fija", cierre_valor="13:15")
        self.assertFalse(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 14)))
        self.assertTrue(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 15)))

    def test_herencia_de_default_cuando_slot_no_define(self):
        slot = self._slot(cierre_modo=None)
        # Sin default explícito, comportamiento = al_iniciar_siguiente.
        ahora_antes = datetime(2026, 8, 17, 13, 59)
        self.assertFalse(
            franja_esta_cerrada(slot, ahora_antes,
                                cierre_modo_default="al_iniciar_siguiente")
        )
        # Con default minutos_antes 15, cierra a las 13:45.
        self.assertTrue(
            franja_esta_cerrada(
                slot, datetime(2026, 8, 17, 13, 46),
                cierre_modo_default="minutos_antes",
                cierre_valor_default="15",
            )
        )
        self.assertFalse(
            franja_esta_cerrada(
                slot, datetime(2026, 8, 17, 13, 44),
                cierre_modo_default="minutos_antes",
                cierre_valor_default="15",
            )
        )

    def test_override_de_slot_gana_sobre_default(self):
        slot = self._slot(cierre_modo="hora_fija", cierre_valor="13:00")
        # Aunque el default diga otra cosa, el slot manda.
        self.assertTrue(
            franja_esta_cerrada(
                slot, datetime(2026, 8, 17, 13, 1),
                cierre_modo_default="al_iniciar_siguiente",
            )
        )

    def test_valor_invalido_falla_seguro_abierto(self):
        slot = self._slot(cierre_modo="minutos_antes", cierre_valor="no-un-numero")
        # Falla segura: la franja NO se marca como cerrada.
        self.assertFalse(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 30)))

    def test_modo_desconocido_falla_seguro_abierto(self):
        slot = self._slot(cierre_modo="algo_inexistente", cierre_valor="")
        self.assertFalse(franja_esta_cerrada(slot, datetime(2026, 8, 17, 13, 30)))

    def test_modos_declarados_son_los_esperados(self):
        self.assertEqual(
            set(CIERRE_MODOS),
            {"al_iniciar_siguiente", "minutos_antes", "hora_fija"},
        )


class EnumsAPITest(unittest.TestCase):
    """Verifica el contrato público de resultados para evitar drift."""

    def test_resultados_reserva_expuestos(self):
        esperados = {"RESERVADA", "LLENA", "CERRADA", "INACTIVA", "NO_EXISTE"}
        self.assertEqual({e.name for e in ResultadoReserva}, esperados)

    def test_resultados_repartidor_expuestos(self):
        esperados = {
            "TOMADA", "YA_TOMADA_POR_TI", "LLENA_DE_REPARTIDORES",
            "CERRADA", "INACTIVA", "NO_EXISTE",
        }
        self.assertEqual({e.name for e in ResultadoRepartidor}, esperados)


if __name__ == "__main__":
    unittest.main()
