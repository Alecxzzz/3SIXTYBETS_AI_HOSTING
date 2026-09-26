import os
import unittest
from unittest.mock import MagicMock, patch

from ai.model import generar_respuesta_you, normalizar_modelo
from engine.search_engine import SearchEngine, normalizar_research_effort
from ai import gemini as gemini_mod


class GeminiTests(unittest.TestCase):
    @patch("ai.gemini.requests.post")
    def test_llamar_gemini_extrae_texto(self, mock_post):
        os.environ["GEMINI_API_KEY"] = "test-key"
        gemini_mod.GEMINI_API_KEY = "test-key"
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "Hola desde Gemini"}]}}
            ]
        }

        texto = gemini_mod.llamar_gemini("Sistema", "Hola")

        self.assertEqual(texto, "Hola desde Gemini")
        self.assertIn("generateContent", mock_post.call_args.args[0])
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "Sistema")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "Hola")

    @patch("ai.gemini.requests.post")
    def test_llamar_gemini_cambia_de_modelo_en_404(self, mock_post):
        os.environ["GEMINI_API_KEY"] = "test-key"
        gemini_mod.GEMINI_API_KEY = "test-key"
        mock_post.side_effect = [
            MagicMock(ok=False, status_code=404, text="model not found"),
            MagicMock(
                ok=True,
                json=lambda: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
            ),
        ]

        texto = gemini_mod.llamar_gemini("S", "H", max_reintentos=1)

        self.assertEqual(texto, "ok")
        self.assertEqual(mock_post.call_count, 2)

    def test_llamar_gemini_sin_key(self):
        original = gemini_mod.GEMINI_API_KEY
        gemini_mod.GEMINI_API_KEY = ""
        try:
            with patch.dict(os.environ, {}, clear=True):
                self.assertTrue(
                    gemini_mod.llamar_gemini("S", "H").startswith("ERROR:")
                )
        finally:
            gemini_mod.GEMINI_API_KEY = original

    def test_normalizar_modelo_gemini(self):
        self.assertEqual(normalizar_modelo("gemini"), "gemini")
        self.assertEqual(normalizar_modelo("Gemini"), "gemini")
        self.assertEqual(normalizar_modelo("google"), "gemini")

    def test_fallo_de_creditos_detecta_402(self):
        from ai.model import _you_fallo_de_creditos

        self.assertTrue(
            _you_fallo_de_creditos(
                "Error de You.com (402): {'error': 'payment_required'}"
            )
        )
        self.assertFalse(_you_fallo_de_creditos("respuesta normal del modelo"))

    @patch("ai.model.generar_respuesta_gemini")
    @patch("ai.model.generar_respuesta_you")
    @patch("ai.model.gemini_configurado")
    def test_fallback_a_gemini_si_you_agota_creditos(
        self, mock_config, mock_you, mock_gemini
    ):
        mock_config.return_value = True
        mock_you.return_value = "Error de You.com (402): payment_required depleted"
        mock_gemini.return_value = "Respuesta de Gemini"

        from ai.model import generar_respuesta

        self.assertEqual(generar_respuesta("S", "H", "you"), "Respuesta de Gemini")

    @patch("ai.model.generar_respuesta_gemini")
    @patch("ai.model.generar_respuesta_you")
    @patch("ai.model.gemini_configurado")
    def test_sin_fallback_si_you_responde_bien(
        self, mock_config, mock_you, mock_gemini
    ):
        mock_config.return_value = True
        mock_you.return_value = "Respuesta de You.com"

        from ai.model import generar_respuesta

        self.assertEqual(
            generar_respuesta("S", "H", "you"), "Respuesta de You.com"
        )
        mock_gemini.assert_not_called()


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
