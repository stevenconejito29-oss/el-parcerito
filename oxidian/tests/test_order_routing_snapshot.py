import unittest
from types import SimpleNamespace

from services import _coalesce_proveedor_id


class OrderRoutingSnapshotTest(unittest.TestCase):
    def test_explicit_own_snapshot_never_falls_back_to_live_product(self):
        item = SimpleNamespace(
            producto=SimpleNamespace(proveedor_despachador_id=42),
        )

        self.assertIsNone(
            _coalesce_proveedor_id(
                {"proveedor_despachador_id": None},
                item,
            )
        )

    def test_legacy_snapshot_without_origin_uses_live_product(self):
        item = SimpleNamespace(
            producto=SimpleNamespace(proveedor_despachador_id=42),
        )

        self.assertEqual(_coalesce_proveedor_id({}, item), 42)


if __name__ == "__main__":
    unittest.main()
