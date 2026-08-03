"""Contrato del horario semanal usado por web, checkout y chatbot."""
import json
import unittest
from datetime import datetime

from schedule_service import (
    day_schedule_text,
    next_opening_text,
    normalize_weekly_schedule,
    schedule_is_open,
    weekly_schedule_text,
)
from models import Proveedor


class WeeklyScheduleTest(unittest.TestCase):
    def setUp(self):
        self.schedule = {
            "0": [["09:00", "14:00"], ["18:00", "22:00"]],
            "1": [["09:00", "14:00"]],
            "2": [],
            "3": [["20:00", "02:00"]],
            "4": [],
            "5": [["10:00", "16:00"]],
            "6": [],
        }

    def test_multiple_windows_and_closed_day(self):
        self.assertTrue(schedule_is_open(
            self.schedule, datetime(2026, 7, 27, 10, 30)
        ))
        self.assertFalse(schedule_is_open(
            self.schedule, datetime(2026, 7, 27, 16, 0)
        ))
        self.assertTrue(schedule_is_open(
            self.schedule, datetime(2026, 7, 27, 19, 0)
        ))
        self.assertFalse(schedule_is_open(
            self.schedule, datetime(2026, 7, 29, 12, 0)
        ))

    def test_window_can_cross_midnight(self):
        self.assertTrue(schedule_is_open(
            self.schedule, datetime(2026, 7, 30, 23, 30)
        ))
        self.assertTrue(schedule_is_open(
            self.schedule, datetime(2026, 7, 31, 1, 30)
        ))
        self.assertFalse(schedule_is_open(
            self.schedule, datetime(2026, 7, 31, 2, 30)
        ))

    def test_rejects_overlap_with_previous_day(self):
        schedule = dict(self.schedule)
        schedule["4"] = [["01:00", "06:00"]]
        with self.assertRaisesRegex(ValueError, "día anterior"):
            normalize_weekly_schedule(schedule)

    def test_normalizes_json_and_builds_customer_text(self):
        normalized = normalize_weekly_schedule(json.dumps(self.schedule))
        self.assertEqual(normalized["2"], [])
        self.assertEqual(
            day_schedule_text(normalized, 0),
            "Lunes: 09:00–14:00 y 18:00–22:00",
        )
        self.assertIn("Miércoles: cerrado", weekly_schedule_text(normalized))

    def test_next_opening_uses_next_band_not_legacy_fixed_time(self):
        self.assertEqual(
            next_opening_text(self.schedule, datetime(2026, 7, 27, 15, 0)),
            "hoy a las 18:00",
        )

    def test_partner_uses_weekly_schedule_instead_of_fixed_hours(self):
        partner = Proveedor(
            nombre="Socio horario",
            horario_semanal_json=json.dumps({
                str(day): [["00:00", "23:59"]] for day in range(7)
            }),
        )
        self.assertTrue(partner.esta_abierto_ahora)
        partner.horario_semanal_json = json.dumps({
            str(day): [] for day in range(7)
        })
        self.assertFalse(partner.esta_abierto_ahora)


if __name__ == "__main__":
    unittest.main()
