import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import Flask
from store_config import get_pickup_details
from push_service import notify_order_state


class PickupExperienceTest(unittest.TestCase):
    def test_maps_uses_local_address_not_delivery_coverage_center(self):
        config = {'DIRECCION_NEGOCIO': 'Calle QA 12', 'CIUDAD_NEGOCIO': 'Ciudad QA', 'PAIS_NEGOCIO': 'País QA'}
        with patch('store_config.get_store_value', side_effect=lambda key, *args: config.get(key, '')):
            details = get_pickup_details()
        parsed = urlsplit(details['maps_url'])
        self.assertEqual(parsed.netloc, 'www.google.com')
        self.assertEqual(parse_qs(parsed.query)['destination'], ['Calle QA 12, Ciudad QA, País QA'])

    def test_missing_address_never_invents_a_pickup_location(self):
        with patch('store_config.get_store_value', return_value=''):
            self.assertEqual(get_pickup_details(), {'address': '', 'maps_url': ''})

    def test_pickup_push_confirms_collection_and_opens_protected_order(self):
        order = SimpleNamespace(id=12, cliente_id=3, numero_pedido='QA-12', estado='listo', tipo_entrega_cliente='recogida', customer_device_hash='device-qa')
        with Flask(__name__).app_context(), patch('models.SiteConfig.get', return_value='comida'), patch('store_config.get_pickup_details', return_value={'address':'Calle QA 12'}), patch('push_service.notify_user') as send:
            notify_order_state(order)
        self.assertIn('Ya puedes recoger', send.call_args.args[1])
        self.assertIn('Calle QA 12', send.call_args.args[2])
        self.assertIn('Maps', send.call_args.args[2])
        self.assertEqual(send.call_args.kwargs['url'], '/pedido/12/confirmado')

    def test_delivery_never_invites_customer_to_collect(self):
        order = SimpleNamespace(id=12, cliente_id=3, numero_pedido='QA-12', estado='listo', tipo_entrega_cliente='delivery', customer_device_hash='device-qa')
        with Flask(__name__).app_context(), patch('models.SiteConfig.get', return_value='comida'), patch('push_service.notify_user') as send:
            notify_order_state(order)
        self.assertNotIn('recoger', send.call_args.args[1])
