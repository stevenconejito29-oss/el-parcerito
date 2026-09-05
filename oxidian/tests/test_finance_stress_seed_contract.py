"""Contrato estático del fixture masivo: seguro, repetible y verificable."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FinanceStressSeedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "scripts" / "seed_finance_stress.py").read_text(encoding="utf-8")

    def test_never_runs_without_explicit_test_database_guard(self):
        self.assertIn('os.environ.get("ALLOW_QA_MASS_SEED") != "1"', self.source)
        self.assertIn('"test" not in uri', self.source)

    def test_expected_scale_and_reversible_commands_are_explicit(self):
        self.assertIn("CUSTOMERS = 100", self.source)
        self.assertIn("STAFF = 5", self.source)
        self.assertIn('command == "cleanup"', self.source)
        self.assertIn('command == "verify"', self.source)

    def test_financial_invariants_are_verified(self):
        for invariant in (
            "duplicate_income_entries", "cancelled_with_income",
            "order_income_total", "delivered_total", "delivery_commissions",
            "missing_zone_snapshots", "salary_payments", "salary_expenses",
            "zone_resolution_errors",
        ):
            self.assertIn(invariant, self.source)


if __name__ == "__main__":
    unittest.main()
