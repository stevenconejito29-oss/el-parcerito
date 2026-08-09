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
            "repartidor/ruta.html",
        ):
            source = (TEMPLATES / relative).read_text(encoding="utf-8")
            self.assertIn("_order_ticket_action.html", source, relative)
            self.assertIn("ticket_action(", source, relative)

    def test_ticket_marks_reprints_and_terminal_states(self):
        source = (TEMPLATES / "pos/ticket.html").read_text(encoding="utf-8")
        self.assertIn("REIMPRESIÓN · COPIA", source)
        self.assertIn("CANCELADO · NO PREPARAR", source)
        self.assertIn("ENTREGADO · COPIA", source)

    def test_reprint_action_uses_secure_post_with_manual_fallback(self):
        source = (TEMPLATES / "_order_ticket_action.html").read_text(encoding="utf-8")
        self.assertIn('<form method="POST"', source)
        self.assertIn("pos.imprimir_ticket", source)
        self.assertIn('name="csrf_token"', source)
        self.assertIn("reprint=1", source)
        self.assertIn("auto_print=1", source)
        self.assertIn("data-fallback-url", source)

    def test_ticket_is_offline_safe_and_uses_58mm_printer_head_width(self):
        source = (TEMPLATES / "pos/ticket.html").read_text(encoding="utf-8")
        # El papel es de 58 mm, pero el cabezal imprimible real es 48 mm.
        # WeasyPrint 69 no admite altura `auto`, por eso se usa un lienzo fijo.
        self.assertIn("@page { size: 48mm 200mm", source)
        self.assertIn("width: 48mm", source)
        self.assertNotIn("fonts.googleapis.com", source)

    def test_ticket_never_represents_points_as_money(self):
        source = (TEMPLATES / "pos/ticket.html").read_text(encoding="utf-8")
        self.assertIn("Canje de producto", source)
        self.assertNotIn("pedido.puntos_usados /", source)


if __name__ == "__main__":
    unittest.main()
