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


class EstadoPartidoTests(unittest.TestCase):
    """Nunca recomendar un partido ya empezado o terminado.

    Regresion real: ESPN devuelve tambien juegos 'post'/'in' y el sistema los
    aceptaba, generando un pick sobre un partido que ya se habia jugado.
    """

    def _game(self, state):
        return {
            "name": "Los Angeles Dodgers at San Francisco Giants",
            "state": state,
            "status": "Final" if state == "post" else "1:05 PM",
            "home": {"name": "San Francisco Giants", "abbr": "SFG"},
            "away": {"name": "Los Angeles Dodgers", "abbr": "LAD"},
        }

    def _pasa(self, state):
        """Replica la regla: solo 'pre' (o sin estado) genera pick."""
        e = (state or "").strip().lower()
        return (not e) or e == "pre"

    def test_partido_por_jugar_si_pasa(self):
        self.assertTrue(self._pasa("pre"))
        self.assertTrue(self._pasa("PRE"))

    def test_partido_terminado_no_pasa(self):
        self.assertFalse(self._pasa("post"))
        self.assertFalse(self._pasa("POST"))

    def test_partido_en_vivo_no_pasa(self):
        self.assertFalse(self._pasa("in"))

    def test_contexto_tiene_el_filtro_de_estado(self):
        import inspect
        import main

        fuente = inspect.getsource(main.contexto_espn)
        self.assertIn("YA NO ESTA POR JUGARSE", fuente)
        self.assertIn('!= "pre"', fuente)


class MatchingPartidoTests(unittest.TestCase):
    """El partido analizado debe ser EXACTAMENTE el que pidio el usuario.

    Regresion grave: al pedir "dodgers vs orioles" se aceptaba el juego
    Orioles-Yankees (coincidia un solo token, "orioles") y la IA entregaba un
    pick de un partido inexistente, llega a justificarlo como "equivalente".
    """

    GAME = {
        "home": {"name": "New York Yankees", "short_name": "Yankees", "abbr": "NYY"},
        "away": {"name": "Baltimore Orioles", "short_name": "Orioles", "abbr": "BAL"},
    }

    def test_rechaza_si_solo_coincide_un_equipo(self):
        from main import _coinciden_ambos_equipos

        # "dodgers orioles": solo coincide Orioles -> NO debe pasar.
        self.assertFalse(
            _coinciden_ambos_equipos(self.GAME, {"dodgers", "orioles"})
        )

    def test_acepta_si_coinciden_los_dos(self):
        from main import _coinciden_ambos_equipos

        self.assertTrue(
            _coinciden_ambos_equipos(self.GAME, {"yankees", "orioles"})
        )

    def test_acepta_por_nombre_corto_y_abbr(self):
        from main import _coinciden_ambos_equipos

        self.assertTrue(_coinciden_ambos_equipos(self.GAME, {"yankees", "bal"}))
        self.assertTrue(_coinciden_ambos_equipos(self.GAME, {"nyy", "orioles"}))

    def test_contexto_avisa_cuando_no_encuentra_el_partido(self):
        import main

        fuente_ctx = main.contexto_espn("dodgers vs orioles")
        if fuente_ctx:
            self.assertTrue(
                fuente_ctx.startswith("NO SE ENCONTRO"),
                "debe avisar que no son esos los equipos",
            )


class ContextoEspnTests(unittest.TestCase):
    """El contexto de ESPN debe entregarse SIN ambiguedad.

    Regresion real: se pasaba 'Rays 6 @ Yankees 1' y el modelo tomaba el
    marcador del rival como propio, inventando promedios (dijo 5.8 cuando
    el real era 4.8) y sumando promedios en vez de usar el total real.
    """

    def test_contexto_incluye_totales_por_partido(self):
        import inspect
        import main

        fuente = inspect.getsource(main.contexto_espn)
        # Debe indicar el total de cada partido, no solo el marcador suelto.
        self.assertIn("total del partido", fuente)
        self.assertIn("PROMEDIO", fuente)
        # Y no debe volver al formato ambiguo "@".
        self.assertNotIn("} @ {h.get('name')", fuente)

    def test_promedio_se_calcula_sobre_totales(self):
        """Aritmetica: el total de un partido es la suma de ambos marcadores."""
        partidos = [(1, 7), (9, 11), (6, 10), (2, 12), (6, 9)]
        prom_anot = sum(p[0] for p in partidos) / len(partidos)
        prom_total = sum(p[1] for p in partidos) / len(partidos)
        self.assertAlmostEqual(prom_anot, 4.8)
        self.assertAlmostEqual(prom_total, 9.8)
        # El total NUNCA es menor que lo que anota el equipo.
        for anot, total in partidos:
            self.assertLessEqual(anot, total)


class PromptMercadosPorDeporteTests(unittest.TestCase):
    """Regresion real: a un partido de MLB se le ofrecio 'Doble oportunidad 1X',
    que es un mercado de futbol. El beisbol no tiene empate.
    """

    def test_prompt_exige_identificar_el_deporte(self):
        from engine.prompt_builder import construir_prompt_sistema_36ai

        prompt = construir_prompt_sistema_36ai()
        self.assertIn("IDENTIFICA EL DEPORTE", prompt)
        self.assertIn("EXCLUSIVAMENTE la lista de ese deporte", prompt)

    def test_prompt_prohibe_mercados_de_otro_deporte(self):
        from engine.prompt_builder import construir_prompt_sistema_36ai

        prompt = construir_prompt_sistema_36ai()
        # La advertencia debe nombrar los errores tipicos.
        self.assertIn("NO tiene empate", prompt)
        self.assertIn("doble oportunidad", prompt)
        # Y debe existir la seccion de MLB con mercados validos.
        self.assertIn("MLB:", prompt)
        for valido in ("Ganador", "totales", "handicap", "hits", "strikeouts"):
            self.assertIn(valido, prompt)

    def test_instruccion_de_rebusqueda_no_ofrece_ajenos(self):
        """La instruccion de re-busqueda no debe sugerir solo mercados de futbol."""
        import inspect
        import ai.ia36 as m

        fuente = inspect.getsource(m.analizar_36ai)
        # Si aparece "doble oportunidad" debe ser para PROHIBIRLO, no para
        # ofrecerlo como alternativa.
        if "doble oportunidad" in fuente:
            self.assertIn("NO hay doble oportunidad", fuente)
        self.assertIn("ganador, handicap, totales, hits", fuente)


class SoportePromptTests(unittest.TestCase):
    """El bot de soporte no debe filtrar datos internos ni botar a WhatsApp
    ante cualquier duda. Regresion real: respondia 'plan ILIMITADO' y mandaba
    a WhatsApp un admin que solo preguntaba por el dashboard.
    """

    PROMPT_PARTES = (
        "COMO HABLAR DE ESOS DATOS",
        "CUANDO SI DERIVAR A WHATSAPP",
        "respondela SIEMPRE",
        "no lo mandes a WhatsApp por una consulta",
    )

    def _prompt(self):
        import inspect
        import extras

        return inspect.getsource(extras.soporte_chat)

    def test_prompt_prohibe_filtrar_el_formato_interno(self):
        fuente = self._prompt()
        self.assertIn("NUNCA copies el formato del bloque", fuente)
        self.assertIn("efectividad_historica_ia", fuente)

    def test_prompt_prohibe_tecnicismos(self):
        fuente = self._prompt()
        self.assertIn("ciclo del scheduler", fuente)
        self.assertIn("automaticamente", fuente)

    def test_prompt_acota_la_escalacion_a_whatsapp(self):
        """Debe decir que SOLO deriva por dinero/pagos/fallos, no por consultas."""
        fuente = self._prompt()
        self.assertIn("SOLO si hay dinero sin acreditar", fuente)
        self.assertIn("No derives nunca", fuente)

    def test_prompt_sigue_informando_el_whatsapp_real(self):
        """La regla de escalacion debe conservar el numero de contacto."""
        import extras

        self.assertTrue(extras.WHATSAPP)
        fuente = self._prompt()
        self.assertIn("wa.me/", fuente)


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

    def test_dashboard_tiene_busqueda_web(self):
        """El dashboard DEBE buscar en web: se perdio al darle motor Groq propio.

        Antes lo hacia You.com (research con include_domains). Si alguien quita
        la busqueda, los picks se apoyarian solo en la memoria del modelo.
        """
        import inspect
        import ai.dashboard_ia as d

        self.assertTrue(d.CONSULTAS_BUSQUEDA, "debe tener consultas de busqueda")
        self.assertTrue(hasattr(d, "buscar_contexto_web"))
        # La busqueda debe ejecutarse DENTRO de generar_picks, no solo existir.
        fuente = inspect.getsource(d.generar_picks)
        self.assertIn("buscar_contexto_web", fuente)
        self.assertIn("CONTEXTO DE BUSQUEDA WEB", fuente)

    def test_busqueda_web_se_puede_apagar(self):
        """Debe existir un interruptor para no gastar tiempo en busquedas."""
        import inspect
        import ai.dashboard_ia as d

        fuente = inspect.getsource(d.buscar_contexto_web)
        self.assertIn("DASHBOARD_AI_WEB", fuente)

    def test_365ai_tiene_busqueda_web(self):
        """El chat (365AI) debe conservar su herramienta de busqueda."""
        import ai.ia36 as ia36

        self.assertTrue(hasattr(ia36, "buscar_web"))
        nombres = [t["function"]["name"] for t in ia36.tools]
        self.assertIn("buscar_web", nombres)

    def test_tool_call_valida_no_se_trata_como_fallo(self):
        """REGRESION CRITICA: content=null con tool_calls es una respuesta VALIDA.

        El loop agéntico pide la herramienta buscar_web y Groq devuelve
        content=null + tool_calls poblado. Tratarlo como "content vacio" hacia
        que se reintentara hasta agotar y devolver None -> el chat caia al
        aviso de "365AI saturada" en cualquier analisis de partido (los
        saludos si funcionaban, por eso el bug pasava desapercibido).
        """
        from unittest.mock import MagicMock, patch
        import ai.ia36 as m

        # Respuesta de Groq pidiendo herramienta: content=null (None en JSON).
        con_tool = MagicMock(status_code=200)
        con_tool.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "buscar_web",
                                    "arguments": '{"query":"Dodgers Giants"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }

        original = m.GROQ_API_KEY
        m.GROQ_API_KEY = "test"
        try:
            with patch("ai.ia36.requests.post", return_value=con_tool) as mp:
                data, modelo = m.llamar_modelo(
                    [{"role": "user", "content": "analiza el partido"}],
                    usar_tools=True,
                    max_reintentos=3,
                )
        finally:
            m.GROQ_API_KEY = original

        self.assertIsNotNone(data, "una peticion de herramienta NO debe devolverse como None")
        # Y no debe haber consumido reintentos: debe devolver en la primera.
        self.assertEqual(mp.call_count, 1)

    def test_content_vacio_sin_tools_sigue_reintentando(self):
        """Sin tool_calls, el content vacio SI es un fallo a reintentar."""
        from unittest.mock import MagicMock, patch
        import ai.ia36 as m

        vacio = MagicMock(status_code=200)
        vacio.json.return_value = {"choices": [{"message": {"content": ""}}]}
        bueno = MagicMock(status_code=200)
        bueno.json.return_value = {"choices": [{"message": {"content": "hola"}}]}

        original = m.GROQ_API_KEY
        m.GROQ_API_KEY = "test"
        try:
            with patch("ai.ia36.requests.post", side_effect=[vacio, bueno]) as mp:
                data, _ = m.llamar_modelo(
                    [{"role": "user", "content": "hola"}],
                    usar_tools=False,
                    max_reintentos=3,
                )
        finally:
            m.GROQ_API_KEY = original

        self.assertEqual(data["choices"][0]["message"]["content"], "hola")
        self.assertEqual(mp.call_count, 2)

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
