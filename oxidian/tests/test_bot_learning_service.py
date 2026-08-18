"""Tests unitarios del auto-aprendizaje del chatbot: normalización y hashing.

Estos tests NO tocan la BD — cubren la lógica pura de agrupamiento por
hash. Los tests de integración (registrar_signal contra la BD real,
endpoint POST) requieren Flask app + BD y se cubren en el suite
principal del contenedor.
"""
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Stub aditivo de models — bot_learning_service importa SiteConfig
# de forma diferida dentro de funciones, así que solo necesitamos que
# la importación no explote si el módulo `models` no está disponible.
_models = sys.modules.get("models") or types.ModuleType("models")
if not hasattr(_models, "SiteConfig"):
    class _SiteConfigStub:
        @staticmethod
        def get(key, default=""):
            return default
    _models.SiteConfig = _SiteConfigStub
sys.modules["models"] = _models


class NormalizacionMensajeTest(unittest.TestCase):
    def test_normalizar_agrupa_variantes_equivalentes(self):
        from bot_learning_service import normalizar_mensaje
        # Misma pregunta con distinto orden de palabras + puntuación +
        # acentos → misma forma normalizada.
        a = normalizar_mensaje("¿Cuánto cuesta el envío?")
        b = normalizar_mensaje("cuanto cuesta envio")
        c = normalizar_mensaje("Cuanto cuesta el envio!!")
        d = normalizar_mensaje("EL ENVIO cuanto cuesta")
        self.assertEqual(a, b)
        self.assertEqual(b, c)
        self.assertEqual(c, d)

    def test_normalizar_elimina_stopwords_cortas(self):
        from bot_learning_service import normalizar_mensaje
        # "la", "el", "de" desaparecen — no aportan al significado.
        r = normalizar_mensaje("la dirección de mi pedido")
        self.assertNotIn(" la ", " " + r + " ")
        self.assertNotIn(" el ", " " + r + " ")
        self.assertNotIn(" de ", " " + r + " ")
        # "dirección" (sin acento) y "pedido" y "mi" sí aparecen.
        # ("mi" NO está en la lista de stopwords porque puede ser
        # discriminante — "mi pedido" vs "un pedido").
        self.assertIn("direccion", r)
        self.assertIn("pedido", r)

    def test_normalizar_no_elimina_palabras_semantica(self):
        from bot_learning_service import normalizar_mensaje
        # "no", "sin", "para" son semánticamente importantes — se conservan.
        r = normalizar_mensaje("no llegó el pedido para mi casa")
        self.assertIn("no", r.split())
        self.assertIn("para", r.split())

    def test_normalizar_devuelve_palabras_ordenadas(self):
        from bot_learning_service import normalizar_mensaje
        r = normalizar_mensaje("zebra alfa mango")
        palabras = r.split()
        self.assertEqual(palabras, sorted(palabras))

    def test_normalizar_input_vacio_o_invalido(self):
        from bot_learning_service import normalizar_mensaje
        self.assertEqual(normalizar_mensaje(""), "")
        self.assertEqual(normalizar_mensaje(None), "")
        self.assertEqual(normalizar_mensaje("!!!"), "")

    def test_normalizar_trunca_payloads_gigantes(self):
        from bot_learning_service import normalizar_mensaje
        gigante = "hola " * 500
        r = normalizar_mensaje(gigante)
        self.assertLessEqual(len(r), 400)


class HashMensajeTest(unittest.TestCase):
    def test_hash_deterministico(self):
        from bot_learning_service import hash_mensaje, normalizar_mensaje
        h1 = hash_mensaje(normalizar_mensaje("cuanto tarda mi pedido"))
        h2 = hash_mensaje(normalizar_mensaje("cuanto tarda mi pedido"))
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 24)

    def test_hash_agrupa_variantes_por_forma_normalizada(self):
        from bot_learning_service import hash_mensaje, normalizar_mensaje
        h_a = hash_mensaje(normalizar_mensaje("¿Cuánto tarda MI pedido?"))
        h_b = hash_mensaje(normalizar_mensaje("cuanto tarda pedido mi"))
        self.assertEqual(h_a, h_b)

    def test_hash_diferente_para_preguntas_distintas(self):
        from bot_learning_service import hash_mensaje, normalizar_mensaje
        h_a = hash_mensaje(normalizar_mensaje("cuanto tarda mi pedido"))
        h_b = hash_mensaje(normalizar_mensaje("aceptan bizum"))
        self.assertNotEqual(h_a, h_b)

    def test_hash_vacio_devuelve_vacio(self):
        from bot_learning_service import hash_mensaje
        self.assertEqual(hash_mensaje(""), "")


class TelefonoHashTest(unittest.TestCase):
    def test_sin_key_devuelve_vacio(self):
        # Sin OXIDIAN_KEY el hash NO se genera (mejor sin dato que débil).
        import os
        from bot_learning_service import hash_telefono
        prev = os.environ.pop("OXIDIAN_KEY", None)
        try:
            self.assertEqual(hash_telefono("+34600000001"), "")
        finally:
            if prev is not None:
                os.environ["OXIDIAN_KEY"] = prev

    def test_con_key_produce_hash_deterministico(self):
        import os
        from bot_learning_service import hash_telefono
        os.environ["OXIDIAN_KEY"] = "test-key-value"
        try:
            h1 = hash_telefono("+34 600 000 001")
            h2 = hash_telefono("34600000001")
            # Misma clave con formatos distintos → mismo hash (limpia dígitos).
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 32)
        finally:
            del os.environ["OXIDIAN_KEY"]


if __name__ == "__main__":
    unittest.main()
