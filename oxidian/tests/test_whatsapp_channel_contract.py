"""El transporte WhatsApp no reabre consultas ni campañas para clientes."""
import unittest
from unittest.mock import patch

from services import _send_whatsapp_message, enviar_whatsapp_generico, encolar_whatsapp_generico


class WhatsAppChannelContractTest(unittest.TestCase):
    @patch('services._bot_http_post', return_value=True)
    @patch('models.SiteConfig.get', return_value='0')
    def test_only_transactional_purposes_reach_transport(self, config, transport):
        for purpose in ('order_confirmation', 'delivery_code', 'points_otp', 'canje_codigo', 'web_chat_handoff'):
            self.assertTrue(_send_whatsapp_message('+34610000001', 'QA', purpose=purpose))
            self.assertEqual(transport.call_args.args[1]['purpose'], purpose)
        self.assertEqual(transport.call_count, 5)

    @patch('services._bot_http_post')
    @patch('models.SiteConfig.get', return_value='1')
    def test_legacy_outbox_purposes_are_rejected_even_in_simulation(self, config, transport):
        for purpose in ('', 'manual', 'marketing', 'review_request', 'points_balance', 'pedido_estado', 'pago_confirmado'):
            self.assertFalse(_send_whatsapp_message('+34610000001', 'QA', purpose=purpose))
            self.assertFalse(enviar_whatsapp_generico('+34610000001', 'QA', evento=purpose))
            self.assertIsNone(encolar_whatsapp_generico('+34610000001', 'QA', evento=purpose))
        transport.assert_not_called()
        config.assert_not_called()
