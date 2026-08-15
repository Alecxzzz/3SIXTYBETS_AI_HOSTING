import os
import unittest
from unittest.mock import patch

from ai.model import generar_respuesta_you, normalizar_modelo
from engine.search_engine import SearchEngine


class YouIntegrationTests(unittest.TestCase):
    @patch("engine.search_engine.requests.post")
    @patch("engine.search_engine.requests.get")
    def test_ask_you_uses_search_context_and_research_endpoint(self, mock_get, mock_post):
        os.environ["YOU_API_KEY"] = "test-key"
        mock_get.return_value.json.return_value = {
            "hits": [
                {"title": "Title 1", "snippet": "Snippet 1"},
                {"title": "Title 2", "snippet": "Snippet 2"},
            ]
        }
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"output": {"content": "respuesta lista"}}

        engine = SearchEngine()
        text = engine.ask_you("Analiza Uruguay vs España", system_prompt="Sistema de prueba")

        self.assertEqual(text, "respuesta lista")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_post.call_count, 1)

        search_args = mock_get.call_args
        self.assertEqual(search_args.kwargs["params"]["query"], "Analiza Uruguay vs España")

        post_kwargs = mock_post.call_args.kwargs
        self.assertIn("Sistema de prueba", post_kwargs["json"]["input"])
        self.assertIn("Analiza Uruguay vs España", post_kwargs["json"]["input"])

    @patch("ai.model.SearchEngine.ask_you")
    def test_generar_respuesta_you_uses_search_engine(self, mock_ask_you):
        os.environ["YOU_API_KEY"] = "test-key"
        mock_ask_you.return_value = "respuesta desde engine"

        text = generar_respuesta_you("Sistema de prueba", "Pregunta deportiva")

        self.assertEqual(text, "respuesta desde engine")
        mock_ask_you.assert_called_once_with(
            "Pregunta deportiva",
            system_prompt="Sistema de prueba",
            research_effort="medium",
        )

    def test_normalizar_modelo_groq_uses_you(self):
        self.assertEqual(normalizar_modelo("groq"), "you")
        self.assertEqual(normalizar_modelo("GROQ"), "you")


if __name__ == "__main__":
    unittest.main()
