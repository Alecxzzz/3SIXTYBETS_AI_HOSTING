import os
import unittest
from unittest.mock import patch

from ai.model import generar_respuesta_you, normalizar_modelo
from engine.search_engine import SearchEngine, normalizar_research_effort


class ChatConvTokenTests(unittest.TestCase):
    """El token de /chat debe ser stateless (varias instancias en Northflank)."""

    def test_token_firmado_se_valida(self):
        from main import _chat_conv_crear, _chat_conv_validar

        token = _chat_conv_crear("usuario-1")
        self.assertTrue(_chat_conv_validar(token, "usuario-1"))

    def test_token_anonimo_sin_usuario(self):
        from main import _chat_conv_crear, _chat_conv_validar

        token = _chat_conv_crear(None)
        self.assertTrue(_chat_conv_validar(token, None))
        self.assertTrue(_chat_conv_validar(token, "otro-usuario"))

    def test_token_de_otro_usuario_se_rechaza(self):
        from main import _chat_conv_crear, _chat_conv_validar

        token = _chat_conv_crear("usuario-1")
        self.assertFalse(_chat_conv_validar(token, "usuario-2"))

    def test_token_sobre_un_topo(self):
        """Debe validar aunque la firma HMAC contenga el byte '.'.

        Un digest crudo puede contener 0x2E y romper el split; por eso la firma
        va en base64url. Este test usa la funcion real, sin stubs.
        """
        from main import _chat_conv_crear, _chat_conv_validar

        for i in range(60):
            token = _chat_conv_crear(f"user-{i}")
            self.assertTrue(
                _chat_conv_validar(token, f"user-{i}"), f"token {i} no valido"
            )

    def test_token_manipulado_se_rechaza(self):
        from main import _chat_conv_crear, _chat_conv_validar

        token = _chat_conv_crear("usuario-1")
        adulterado = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        self.assertFalse(_chat_conv_validar(adulterado, "usuario-1"))

    def test_token_invalido_se_rechaza(self):
        from main import _chat_conv_validar

        self.assertFalse(_chat_conv_validar("", None))
        self.assertFalse(_chat_conv_validar(None, None))
        self.assertFalse(_chat_conv_validar("no-es-un-token", None))

    def test_token_expirado_se_rechaza(self):
        import main
        from main import _chat_conv_crear, _chat_conv_validar

        original = main.CHAT_CONV_TTL_S
        try:
            main.CHAT_CONV_TTL_S = -1  # cualquier token queda "viejo"
            self.assertFalse(_chat_conv_validar(_chat_conv_crear("u"), "u"))
        finally:
            main.CHAT_CONV_TTL_S = original


class GroqRobustezTests(unittest.TestCase):
    """Groq devuelve 200 con content vacio si max_tokens lo consume el
    razonamiento del gpt-oss. Eso no puede devolverse como respuesta."""

    def test_content_vacio_se_reintenta(self):
        from unittest.mock import patch, MagicMock
        import ai.ia36 as m

        vacio = MagicMock(status_code=200)
        vacio.json.return_value = {"choices": [{"message": {"content": ""}}]}
        bueno = MagicMock(status_code=200)
        bueno.json.return_value = {"choices": [{"message": {"content": "hola"}}]}

        original_key = m.GROQ_API_KEY
        m.GROQ_API_KEY = "test"
        try:
            with patch("ai.ia36.requests.post", side_effect=[vacio, bueno]) as mock_post:
                data, modelo = m.llamar_modelo(
                    [{"role": "user", "content": "hola"}],
                    usar_tools=False,
                    max_tokens=20,
                    max_reintentos=3,
                )
        finally:
            m.GROQ_API_KEY = original_key

        self.assertEqual(
            data["choices"][0]["message"]["content"], "hola"
        )
        # El segundo intento debe haber subido el presupuesto de tokens.
        self.assertEqual(mock_post.call_count, 2)
        self.assertGreaterEqual(
            mock_post.call_args_list[1].kwargs["data"].count("1024"), 1
        )

    def test_compound_mini_no_esta_en_la_cadena(self):
        import ai.ia36 as m

        self.assertNotIn("groq/compound-mini", m.MODELOS_FALLBACK_CADENA)
        self.assertNotIn("groq/compound-mini", m.MODELOS_PREFERIDOS)


class MotoresDedicadosTests(unittest.TestCase):
    """Cada superficie debe tener su propio motor, no compartirlo con el chat.

    Groq aplica los limites POR MODELO, asi que separar los motores sube el
    margen real: el chat puede saturarse sin tumbar el dashboard ni las stats.
    """

    def test_cada_superficie_tiene_modulo_propio(self):
        import importlib.util

        for mod in ("ai.soporte", "ai.dashboard_ia", "ai.stats_ia", "ai.ia36"):
            self.assertIsNotNone(
                importlib.util.find_spec(mod), f"falta el modulo {mod}"
            )

    def test_modelos_de_apartado_no_comparten_el_de_365ai(self):
        import ai.dashboard_ia as dash
        import ai.stats_ia as stats
        import ai.soporte as soporte
        import ai.ia36 as ia36

        # Ninguno debe apuntar al modelo principal de 365AI.
        for nombre, modelo in [
            ("dashboard", dash.MODELO),
            ("stats", stats.MODELO),
            ("soporte", soporte.MODELO),
        ]:
            self.assertNotEqual(
                modelo, ia36.MODELO_DEFAULT,
                f"{nombre} comparte el modelo principal de 365AI",
            )

    def test_dashboard_y_stats_son_autonomos(self):
        """Cada motor dedicado define su propio modelo y fallback."""
        import ai.dashboard_ia as dash
        import ai.stats_ia as stats

        self.assertTrue(dash.MODELO and dash.MODELO_FALLBACK)
        self.assertTrue(stats.MODELO and stats.MODELO_FALLBACK)
        self.assertNotEqual(dash.MODELO, dash.MODELO_FALLBACK)
        self.assertNotEqual(stats.MODELO, stats.MODELO_FALLBACK)

    def test_stats_tiene_max_tokens_suficiente(self):
        """500 tokens dejaba el content vacio en los gpt-oss (razonamiento)."""
        import ai.stats_ia as stats

        self.assertGreaterEqual(stats.MAX_TOKENS, 1000)

    def test_content_vacio_sube_max_tokens(self):
        from unittest.mock import MagicMock, patch
        import ai.stats_ia as m

        vacio = MagicMock(status_code=200)
        vacio.json.return_value = {"choices": [{"message": {"content": ""}}]}
        bueno = MagicMock(status_code=200)
        bueno.json.return_value = {"choices": [{"message": {"content": "1) hola"}}]}

        original = m.API_KEY
        m.API_KEY = "test"
        try:
            with patch("ai.stats_ia.requests.post", side_effect=[vacio, bueno]) as mp:
                r = m.analizar_partido("s", "u")
        finally:
            m.API_KEY = original

        self.assertEqual(r, "1) hola")
        self.assertEqual(mp.call_count, 2)

    def test_dashboard_devuelve_texto_y_modelo(self):
        from unittest.mock import MagicMock, patch
        import ai.dashboard_ia as m

        ok = MagicMock(status_code=200)
        ok.json.return_value = {"choices": [{"message": {"content": '{"equipo":"x"}'}}]}

        original = m.API_KEY
        m.API_KEY = "test"
        try:
            with patch("ai.dashboard_ia.requests.post", return_value=ok):
                texto, modelo = m.generar_picks("s", "u")
        finally:
            m.API_KEY = original

        self.assertEqual(texto, '{"equipo":"x"}')
        self.assertTrue(modelo)


class ModelDispatchTests(unittest.TestCase):
    """Gemini fue eliminado: el dispatch debe devolver solo You.com o 365AI."""

    def test_normalizar_modelo_ya_no_devuelve_gemini(self):
        self.assertEqual(normalizar_modelo("gemini"), "you")
        self.assertEqual(normalizar_modelo("google"), "you")

    def test_normalizar_modelo_groq_es_365ai(self):
        self.assertEqual(normalizar_modelo("groq"), "36ai")
        self.assertEqual(normalizar_modelo("36ai"), "36ai")

    def test_modelos_disponibles_no_expone_gemini(self):
        from ai.model import modelos_disponibles

        ids = [m["id"] for m in modelos_disponibles()]
        self.assertNotIn("gemini", ids)
        self.assertIn("36ai", ids)

    def test_no_existe_el_modulo_ai_gemini(self):
        import importlib.util

        self.assertIsNone(
            importlib.util.find_spec("ai.gemini"),
            "ai/gemini.py deberia haberse eliminado del proyecto",
        )


class YouFallbackDetectionTests(unittest.TestCase):
    def test_deteccion_de_you_agotado_en_main(self):
        from main import _respuesta_you_agotada

        self.assertTrue(_respuesta_you_agotada(
            "Error de You.com (402): {'error': 'payment_required', "
            "'message': 'Your prepaid credit balance has been depleted.'}"
        ))
        self.assertTrue(_respuesta_you_agotada("Error de You.com (429): rate limit"))
        self.assertFalse(_respuesta_you_agotada("Respuesta normal de la IA"))
        self.assertFalse(_respuesta_you_agotada(""))

    def test_fallo_de_creditos_detecta_402(self):
        from ai.model import _you_fallo_de_creditos

        self.assertTrue(
            _you_fallo_de_creditos(
                "Error de You.com (402): {'error': 'payment_required'}"
            )
        )
        self.assertFalse(_you_fallo_de_creditos("respuesta normal del modelo"))

    @patch("ai.model.generar_respuesta_you")
    def test_you_sin_creditos_propaga_el_error(self, mock_you):
        """Sin Gemini no hay relevo: el error sube al caller (/chat)."""
        mock_you.return_value = "Error de You.com (402): payment_required depleted"

        from ai.model import generar_respuesta

        self.assertIn("402", generar_respuesta("S", "H", "you"))


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

    def test_normalizar_modelo_groq_usa_365ai(self):
        # "groq" es el id que manda el frontend; debe mapear a 365AI, no a You.
        self.assertEqual(normalizar_modelo("groq"), "36ai")
        self.assertEqual(normalizar_modelo("GROQ"), "36ai")

    def test_normalizar_research_effort_uses_valid_enum(self):
        self.assertEqual(normalizar_research_effort("medium"), "standard")
        self.assertEqual(normalizar_research_effort("high"), "deep")
        self.assertEqual(normalizar_research_effort("deep"), "deep")
        self.assertEqual(normalizar_research_effort(""), "standard")


if __name__ == "__main__":
    unittest.main()
