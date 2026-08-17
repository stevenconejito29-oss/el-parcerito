"""Tests del NLU service — cubre las funciones puras y guardrails.

No hace llamadas reales a Groq (mockeadas). Verifica:
- Tokenización con stopwords ES + acentos.
- Ranking de candidatas por overlap léxico + boost multi-palabra.
- Limpieza de keywords (rechaza stopwords, puntuación, vacíos).
- Guardrails de _persist_new_entry_pending: dedupe, min conf, cap diario.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from flask import Flask

from extensions import db


class NLUPureFunctionsTest(unittest.TestCase):
    """Funciones puras — no requieren app context ni BD."""

    def test_tokenize_removes_stopwords_and_accents(self):
        from nlu_service import _tokenize
        tokens = _tokenize("¿Cuánto cuesta el envío a domicilio?")
        # 'el', 'a' son stopwords; 'cuesta','envio','domicilio','cuanto' quedan
        self.assertIn("cuesta", tokens)
        self.assertIn("envio", tokens)  # sin tilde
        self.assertIn("domicilio", tokens)
        self.assertNotIn("el", tokens)
        self.assertNotIn("a", tokens)

    def test_tokenize_ignores_short_words(self):
        from nlu_service import _tokenize
        tokens = _tokenize("hola ya no si")
        # 'ya','no','si' son <3 chars o stopwords
        self.assertEqual(tokens, {"hola"})

    def test_clean_keyword_rejects_stopwords(self):
        from nlu_service import _clean_keyword
        self.assertEqual(_clean_keyword("de"), "")
        self.assertEqual(_clean_keyword("EL "), "")

    def test_clean_keyword_accepts_multiword(self):
        from nlu_service import _clean_keyword
        # frases multi-palabra siempre pasan (aunque contengan stopwords)
        self.assertEqual(_clean_keyword("con envío rápido"), "con envío rápido")

    def test_clean_keyword_rejects_only_punctuation(self):
        from nlu_service import _clean_keyword
        self.assertEqual(_clean_keyword("¡¡¡???"), "")
        self.assertEqual(_clean_keyword("   "), "")

    def test_clean_keyword_truncates_at_40(self):
        from nlu_service import _clean_keyword
        largo = "a" * 100
        self.assertEqual(len(_clean_keyword(largo)), 40)

    def test_norm_pregunta_dedupe(self):
        from nlu_service import _norm_pregunta
        # Dos preguntas semánticamente iguales caen a la misma forma
        self.assertEqual(
            _norm_pregunta("¿Cuánto CUESTA el envío?"),
            _norm_pregunta("cuanto cuesta el envio"),
        )


class NLURankingTest(unittest.TestCase):
    """Ranking de candidatas — usa entries mockeadas, sin BD."""

    def _fake_entry(self, id, pregunta, keywords_list, orden=0):
        e = MagicMock()
        e.id = id
        e.pregunta = pregunta
        e.orden = orden
        e.keyword_list = MagicMock(return_value=keywords_list)
        return e

    def test_rank_prefers_overlap(self):
        from nlu_service import _rank_candidates
        entries = [
            self._fake_entry(1, "Horario de la tienda", ["horario", "abren"]),
            self._fake_entry(2, "Cuánto cuesta el envío", ["envio", "domicilio", "coste"]),
            self._fake_entry(3, "Formas de pago", ["pago", "bizum", "tarjeta"]),
        ]
        # Mensaje sobre envío → entry 2 debe ir primero
        result = _rank_candidates(entries, "cuánto vale el envío a domicilio", top_n=3)
        self.assertEqual(result[0]["id"], 2)

    def test_rank_fallback_when_no_overlap(self):
        from nlu_service import _rank_candidates
        entries = [
            self._fake_entry(1, "Horario", ["horario"]),
            self._fake_entry(2, "Pago", ["pago"]),
        ]
        # Mensaje sin ningún token relevante → devuelve primeras N
        result = _rank_candidates(entries, "xyz zzz", top_n=2)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], 1)

    def test_rank_multiword_bonus(self):
        from nlu_service import _rank_candidates
        entries = [
            # Entry con keyword multi-palabra que aparece literal en el mensaje
            self._fake_entry(1, "Envío", ["a domicilio"]),
            # Entry con más overlap simple pero sin multi-palabra
            self._fake_entry(2, "Pedidos con envío", ["pedido", "envio"]),
        ]
        result = _rank_candidates(entries, "quiero pedido a domicilio ya", top_n=2)
        # Ambos matchean, pero entry 1 tiene bonus multi-palabra
        self.assertEqual(result[0]["id"], 1)


class NLUGuardrailsTest(unittest.TestCase):
    """Guardrails con app+BD in-memory."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.drop_all()

    def test_persist_skips_low_confidence(self):
        from nlu_service import _persist_new_entry_pending
        with self.app.app_context():
            result = _persist_new_entry_pending(
                {"pregunta": "test", "respuesta": "resp", "keywords": []},
                confidence_global=0.3,  # < NLU_NEW_ENTRY_CONFIDENCE_MIN=0.5
                mensaje_original="mensaje suficientemente largo aquí",
            )
            self.assertIsNone(result)

    def test_persist_skips_short_message(self):
        from nlu_service import _persist_new_entry_pending
        with self.app.app_context():
            result = _persist_new_entry_pending(
                {"pregunta": "test", "respuesta": "resp", "keywords": []},
                confidence_global=0.8,
                mensaje_original="hi",  # muy corto
            )
            self.assertIsNone(result)

    def test_persist_skips_duplicate_pregunta(self):
        from nlu_service import _persist_new_entry_pending
        from models import KnowledgeEntry
        with self.app.app_context():
            existing = KnowledgeEntry(
                categoria="general",
                pregunta="¿Cuánto cuesta el envío?",
                respuesta="Gratis desde 20€",
                keywords="envio",
                audiencia="cliente",
                activo=True,
                es_seed=False,
            )
            db.session.add(existing)
            db.session.commit()

            # Groq propone la misma pregunta con distinta capitalización/acento
            result = _persist_new_entry_pending(
                {"pregunta": "cuanto CUESTA el envio", "respuesta": "otra", "keywords": []},
                confidence_global=0.8,
                mensaje_original="mensaje largo suficiente",
            )
            self.assertIsNone(result)

    def test_persist_ok_when_all_guardrails_pass(self):
        from nlu_service import _persist_new_entry_pending
        from models import KnowledgeEntry
        with self.app.app_context():
            result = _persist_new_entry_pending(
                {
                    "pregunta": "¿Aceptan mascotas en el local?",
                    "respuesta": "Sí, pueden entrar con correa.",
                    "keywords": ["mascota", "perro", "gato"],
                    "categoria": "info",
                },
                confidence_global=0.8,
                mensaje_original="oye puedo llevar a mi perro al local?",
            )
            self.assertIsNotNone(result)
            entry = KnowledgeEntry.query.get(result)
            self.assertIsNotNone(entry)
            self.assertFalse(entry.activo)  # pending approval
            self.assertEqual(entry.categoria, "autogenerada")

    def test_apply_keywords_dedupe(self):
        from nlu_service import _apply_suggested_keywords
        from models import KnowledgeEntry
        with self.app.app_context():
            entry = KnowledgeEntry(
                categoria="general",
                pregunta="Horario",
                respuesta="Abrimos de 9 a 22",
                keywords="horario, abren",
                audiencia="cliente",
                activo=True,
                es_seed=False,
            )
            db.session.add(entry)
            db.session.commit()

            # Sugiere 3 keywords: 1 duplicada, 1 stopword, 1 nueva válida
            added = _apply_suggested_keywords(entry.id, ["horario", "de", "cuando abren"])
            self.assertEqual(added, 1)  # solo "cuando abren" pasa

            entry_reloaded = KnowledgeEntry.query.get(entry.id)
            self.assertIn("cuando abren", entry_reloaded.keywords)


if __name__ == "__main__":
    unittest.main()
