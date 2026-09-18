import os
import unittest
from unittest.mock import patch

from ai.model import generar_respuesta_you, normalizar_modelo
from engine.search_engine import SearchEngine, normalizar_research_effort


class YouIntegrationTests(unittest.TestCase):
    @patch("engine.search_engine.requests.post")
    @patch("engine.search_engine.requests.get")
    def test_ask_you_uses_research_with_required_fields(self, mock_get, mock_post):
        os.environ["YOU_API_KEY"] = "test-key"
        mock_get.return_value.json.return_value = {
            "hits": [{"title": "Title 1", "snippet": "Snippet 1"}]
        }
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {"output": {"content": "respuesta lista"}}

        engine = SearchEngine()
        text = engine.ask_you("Analiza picks para mañana", system_prompt="Sistema de prueba")

        self.assertEqual(text, "respuesta lista")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_post.call_count, 1)

        payload = mock_post.call_args.kwargs["json"]
        self.assertIn("Analiza picks para mañana", payload["input"])
        self.assertIn("Sistema de prueba", payload["input"])
        self.assertEqual(payload["freshness"], "day")
        self.assertEqual(payload["safesearch"], "strict")
        self.assertEqual(payload["language"], "ES")
        self.assertEqual(payload["extraction"], {"extraction_mode": "full_page", "extraction_source": "fetch"})
        self.assertEqual(payload["include_domains"], ["sofascore.com", "flashscore.com"])
        self.assertIn("api.you.com/v1/research", mock_post.call_args.args[0])

    @patch("ai.model.SearchEngine.ask_you")
    def test_generar_respuesta_you_uses_search_engine(self, mock_ask_you):
        os.environ["YOU_API_KEY"] = "test-key"
        mock_ask_you.return_value = "respuesta desde engine"

        text = generar_respuesta_you("Sistema de prueba", "Pregunta deportiva")

        self.assertEqual(text, "respuesta desde engine")
        mock_ask_you.assert_called_once_with(
            "Pregunta deportiva",
            system_prompt="Sistema de prueba",
            research_effort="deep",
        )

    def test_normalizar_modelo_groq_uses_you(self):
        self.assertEqual(normalizar_modelo("groq"), "you")
        self.assertEqual(normalizar_modelo("GROQ"), "you")

    def test_normalizar_research_effort_uses_valid_enum(self):
        self.assertEqual(normalizar_research_effort("medium"), "standard")
        self.assertEqual(normalizar_research_effort("high"), "deep")
        self.assertEqual(normalizar_research_effort("deep"), "deep")
        self.assertEqual(normalizar_research_effort(""), "standard")


if __name__ == "__main__":
    unittest.main()
