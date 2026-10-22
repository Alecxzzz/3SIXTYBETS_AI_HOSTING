# -*- coding: utf-8 -*-
"""
Test de ROTACION DE CLAVES Y PROVEEDOR del generador de picks del dashboard.

Escenario real que lo motivo: OpenRouter en plan gratuito da 50 peticiones
diarias a modelos :free. Al agotarse, TODOS los modelos responden
429 "free-models-per-day" (el limite es de la CUENTA, no del modelo), asi que
rotar modelos no servia y el dashboard se quedaba sin picks hasta el dia
siguiente. Ahora:
  1. se rota a otra CLAVE de la misma plataforma (otra cuenta = otra cuota),
  2. si todas las claves de la cuenta estan agotadas, se salta al PROVEEDOR
     de respaldo (Groq), cuya cuota es independiente.

Tambien se fija el comportamiento que NO debe cambiar: un 429 "por minuto"
(marca de rate limit, no de saldo) pausa el MODELO y sigue con el siguiente,
sin quemar la clave.
"""
import importlib
import json
import os
import unittest
from unittest import mock

import ai.dashboard_ia as dashboard_ia

CLAVE_A = "clave-principal-0001"
CLAVE_B = "clave-respaldo-0002"
CLAVE_GROQ = "clave-groq-0003"
URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"


class _Respuesta:
    """Respuesta minima con la misma interfaz que usa requests."""

    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""

    def json(self):
        return self._payload


def _ok(texto='{"pick": "local"}'):
    return _Respuesta(
        200,
        {
            "choices": [{"message": {"role": "assistant", "content": texto}}],
            "usage": {"total_tokens": 12},
        },
    )


class RotacionClavesTest(unittest.TestCase):
    ENTORNO = {
        "DASHBOARD_AI_API_KEY": CLAVE_A,
        "DASHBOARD_AI_API_KEY_FALLBACK": CLAVE_B,
        "DASHBOARD_AI_BASE_URL": URL_OPENROUTER,
        "DASHBOARD_AI_MODEL": "modelo/free-a",
        "DASHBOARD_AI_FALLBACK": "modelo/free-b",
        "DASHBOARD_AI_MODELOS_EXTRA": "",
        "DASHBOARD_AI_RESPALDO": "true",
        "DASHBOARD_AI_RESPALDO_API_KEY": CLAVE_GROQ,
        "DASHBOARD_AI_RESPALDO_BASE_URL": URL_GROQ,
        "DASHBOARD_AI_RESPALDO_MODEL": "openai/gpt-oss-120b",
        "DASHBOARD_AI_RESPALDO_MODELOS_EXTRA": "",
        "DASHBOARD_AI_MAX_REINTENTOS": "1",
        "DASHBOARD_AI_TPD": "10000000",
        "DASHBOARD_AI_RESERVA_PUNTA": "1",
        "DASHBOARD_AI_HORA_PUNTA": "0",
        "DASHBOARD_AI_ITPM": "100000000",
        "DASHBOARD_AI_PAUSA_429": "60",
        "DASHBOARD_AI_PAUSA_KEY": "60",
        "DASHBOARD_AI_DEBUG": "false",
        "AI36_GROQ_API_KEY": "",
        "GROQ_API_KEY": "",
        # Hermeticidad: la clave de respaldo INCORPORADA se prueba aparte
        # (test_respaldo_incorporado_se_suma_a_la_rotacion); aqui solo cuentan
        # las claves del propio test.
        "IA_SIN_CLAVE_RESPALDO": "1",
    }

    def setUp(self):
        self._env = mock.patch.dict(os.environ, self.ENTORNO)
        self._env.start()
        importlib.reload(dashboard_ia)
        self.mod = dashboard_ia

    def tearDown(self):
        self._env.stop()
        importlib.reload(dashboard_ia)

    def _generar(self, post):
        with mock.patch.object(self.mod.requests, "post", side_effect=post):
            return self.mod.generar_picks(
                "system", "partido de prueba", buscar_web=False
            )

    def test_429_de_cuenta_agotada_rota_de_clave(self):
        """Cupo diario agotado: la segunda clave de la plataforma responde."""
        vistas = []

        def post(url, headers=None, json=None, timeout=None):
            vistas.append(headers["Authorization"])
            if headers["Authorization"].endswith(CLAVE_A):
                return _Respuesta(
                    429,
                    text="Rate limit exceeded: free-models-per-day. Add 10 credits",
                )
            return _ok()

        texto, modelo = self._generar(post)

        self.assertEqual(texto, '{"pick": "local"}')
        self.assertEqual(modelo, "modelo/free-a")
        self.assertEqual(vistas, [f"Bearer {CLAVE_A}", f"Bearer {CLAVE_B}"])
        estado = {k["clave"]: k["en_pausa"] for k in self.mod.estado_claves()}
        self.assertTrue(estado[f"{CLAVE_A[:10]}...{CLAVE_A[-4:]}"])
        self.assertFalse(estado[f"{CLAVE_B[:10]}...{CLAVE_B[-4:]}"])

    def test_402_salta_al_proveedor_de_respaldo(self):
        """Sin creditos en las dos claves: se usa Groq (otra cuota)."""
        urls = []

        def post(url, headers=None, json=None, timeout=None):
            urls.append(url)
            if url == URL_OPENROUTER:
                return _Respuesta(402, text="Insufficient credits")
            return _ok('{"pick": "visitante"}')

        texto, modelo = self._generar(post)

        self.assertEqual(texto, '{"pick": "visitante"}')
        self.assertEqual(modelo, "openai/gpt-oss-120b")
        self.assertEqual(urls, [URL_OPENROUTER, URL_OPENROUTER, URL_GROQ])
        self.assertEqual(self.mod.claves_disponibles(), 1)  # solo queda Groq

    def test_429_por_minuto_pausa_el_modelo_no_la_clave(self):
        """Rate limit del minuto: se cambia de MODELO y se conserva la clave."""
        modelos = []

        def post(url, headers=None, json=None, timeout=None):
            modelos.append(json["model"])
            if json["model"] == "modelo/free-a":
                return _Respuesta(
                    429, text="Rate limit reached for model: requests per minute"
                )
            return _ok()

        self._generar(post)

        self.assertEqual(modelos, ["modelo/free-a", "modelo/free-b"])
        self.assertFalse(self.mod._clave_en_pausa(CLAVE_A))
        self.assertTrue(self.mod._modelo_en_pausa("modelo/free-a"))

    def test_sin_cuota_en_todos_los_proveedores_devuelve_none(self):
        """Sin ninguna cuota: no se inventa un pick."""

        def post(url, headers=None, json=None, timeout=None):
            return _Respuesta(402, text="Insufficient credits")

        texto, modelo = self._generar(post)

        self.assertIsNone(texto)
        self.assertIsNone(modelo)
        self.assertEqual(self.mod.claves_disponibles(), 0)

    def test_estado_claves_nunca_expone_la_clave_completa(self):
        """El diagnostico de /dashboard/salud no puede filtrar credenciales."""
        volcado = json.dumps(self.mod.estado_claves(), ensure_ascii=False)
        for clave in (CLAVE_A, CLAVE_B, CLAVE_GROQ):
            self.assertNotIn(clave, volcado)

    def test_estado_claves_muestra_los_modelos_de_cada_proveedor(self):
        """Salud debe decir que modelos se probaran (no solo el endpoint)."""
        por_proveedor = {e["proveedor"]: e["modelos"] for e in self.mod.estado_claves()}
        self.assertEqual(por_proveedor["principal"], ["modelo/free-a", "modelo/free-b"])
        self.assertEqual(por_proveedor["respaldo"][0], "openai/gpt-oss-120b")

    def test_estado_consumo_reporta_el_ultimo_error(self):
        """Sin respuesta de ningun proveedor, el motivo queda visible en salud."""

        def post(url, headers=None, json=None, timeout=None):
            return _Respuesta(404, text='{"error": "model_not_found"}')

        self._generar(post)

        consumo = self.mod.estado_consumo()
        self.assertIn("404", consumo["ultimo_error"])
        self.assertIsNotNone(consumo["ultimo_error_hace_s"])

    def test_respaldo_incorporado_se_suma_a_la_rotacion(self):
        """Sin IA_SIN_CLAVE_RESPALDO, la clave incorporada cierra la rotacion
        (enmascarada: el estado NUNCA la muestra completa)."""
        from ai import claves as mod_claves

        with mock.patch.dict(os.environ, {"IA_SIN_CLAVE_RESPALDO": "0"}):
            importlib.reload(dashboard_ia)
            try:
                estado = dashboard_ia.estado_claves()
                principales = [e for e in estado if e["proveedor"] == "principal"]
                self.assertEqual(len(principales), 3)
                esperada = mod_claves.etiqueta(mod_claves._incorporada())
                self.assertEqual(principales[-1]["clave"], esperada)
                volcado = json.dumps(estado)
                self.assertNotIn(mod_claves._incorporada(), volcado)
            finally:
                importlib.reload(dashboard_ia)


if __name__ == "__main__":
    unittest.main(verbosity=2)
