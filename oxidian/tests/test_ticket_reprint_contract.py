"""Contrato visual mínimo de reimpresión en todas las superficies operativas."""
from pathlib import Path
import unittest


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


class TicketReprintTemplateContractTest(unittest.TestCase):
    def test_reusable_action_is_present_in_required_roles(self):
        for relative in (
            "admin/pedidos.html",
            "admin/pedido_detalle.html",
            "preparador/pedidos.html",
        ):
            source = (TEMPLATES / relative).read_text(encoding="utf-8")
            self.assertIn("_order_ticket_action.html", source, relative)
            self.assertIn("ticket_action(", source, relative)

    def test_ticket_marks_reprints_and_terminal_states(self):
        source = (TEMPLATES / "pos/ticket.html").read_text(encoding="utf-8")
        self.assertIn("REIMPRESIÓN · COPIA", source)
        self.assertIn("CANCELADO · NO PREPARAR", source)
        self.assertIn("ENTREGADO · COPIA", source)

    def test_reprint_action_uses_single_canonical_query_contract(self):
        source = (TEMPLATES / "_order_ticket_action.html").read_text(encoding="utf-8")
        self.assertIn("reprint=1", source)
        self.assertIn("auto_print=1", source)
        self.assertIn("rel=\"noopener\"", source)


if __name__ == "__main__":
    unittest.main()
