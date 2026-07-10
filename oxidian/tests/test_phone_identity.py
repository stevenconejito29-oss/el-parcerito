import unittest

from phone_utils import normalizar_telefono_cliente, telefono_valido


class PhoneIdentityTest(unittest.TestCase):
    def test_e164_normalization_does_not_silently_truncate_identity(self):
        value = normalizar_telefono_cliente("+1234567890123456789")
        self.assertEqual(value, "+1234567890123456789")
        self.assertFalse(telefono_valido(value))

    def test_valid_phone_requires_country_code_and_eight_to_fifteen_digits(self):
        self.assertTrue(telefono_valido("+34612345678"))
        self.assertFalse(telefono_valido("+0123456789"))
        self.assertFalse(telefono_valido("+1234567"))


if __name__ == "__main__":
    unittest.main()
