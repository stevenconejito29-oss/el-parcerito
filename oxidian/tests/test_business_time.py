"""Conversión de días comerciales a timestamps UTC persistidos."""
import unittest
from datetime import date, datetime

from business_time import business_date_for_utc_naive, utc_naive_bounds


class BusinessTimeTest(unittest.TestCase):
    def test_invierno_madrid_empieza_a_23_utc_del_dia_anterior(self):
        start, end = utc_naive_bounds(date(2026, 1, 15))
        self.assertEqual(start.isoformat(), "2026-01-14T23:00:00")
        self.assertEqual(end.isoformat(), "2026-01-15T23:00:00")

    def test_verano_madrid_empieza_a_22_utc_del_dia_anterior(self):
        start, end = utc_naive_bounds(date(2026, 8, 3))
        self.assertEqual(start.isoformat(), "2026-08-02T22:00:00")
        self.assertEqual(end.isoformat(), "2026-08-03T22:00:00")

    def test_movimiento_utc_se_agrupa_en_el_dia_civil_del_negocio(self):
        self.assertEqual(
            business_date_for_utc_naive(datetime(2026, 8, 2, 22, 30)),
            date(2026, 8, 3),
        )


if __name__ == "__main__":
    unittest.main()
