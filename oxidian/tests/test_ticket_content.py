import unittest
from types import SimpleNamespace
from unittest.mock import patch
from escpos_renderer import render_ticket
from ticket_presentation import combo_ticket_lines


class TicketContentTest(unittest.TestCase):
    def item(self):
        return SimpleNamespace(display_nombre='Combo original', producto=SimpleNamespace(nombre='Nombre cambiado'), cantidad=2, subtotal=20, precio_unit=10, notas='Sin salsa\x1b@', display_alergenos=['gluten'], get_metadata=lambda: {'combo':{'componentes':[{'nombre':'Arepa','cantidad':2,'unidades_cliente':[{'unidad':1,'presentacion':{'label':'Grande'},'sabor':{'nombre':'Queso'}}]}], 'selecciones':[{'grupo':'Bebida','opciones':[{'nombre':'Cola','cantidad':1}]}]}})

    def test_combo_details_are_frozen_and_shared(self):
        lines = combo_ticket_lines(self.item())
        self.assertTrue(any('Arepa' in line for line in lines))
        self.assertTrue(any('Grande' in line and 'Queso' in line for line in lines))
        self.assertTrue(any('Bebida' in line and 'Cola' in line for line in lines))

    def test_escpos_snapshot_custom_points_and_control_characters(self):
        order = SimpleNamespace(items=[self.item()], numero_pedido='WEB-123456789', estado='listo', tipo_entrega_cliente='recogida', creado_en=None, cliente=None, subtotal=20, total=20, metodo_pago='efectivo', puntos_ganados=20, puntos_usados=3, notas='Hola\x1bEVIL')
        result = render_ticket(order, brand={'nombre':'QA','puntos':True}, ui={'loyalty_unit_plural':'estrellas'})
        for value in (b'Combo original', b'Arepa', b'Grande', b'Queso', b'Cola', b'gluten', b'estrellas', b'Al entregar', b'Canje: 3'):
            self.assertIn(value, result)
        self.assertNotIn(b'Nombre cambiado', result)
        self.assertNotIn(b'\x1bEVIL', result)
        self.assertNotIn(b'Acumulados', result)
