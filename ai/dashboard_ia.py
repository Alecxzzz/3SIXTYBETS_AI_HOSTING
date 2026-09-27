"""
Dashboard AI - generador de picks del dashboard.

Modulo AUTOCONTENIDO e independiente de las demas IAs del ecosistema. Existe
porque cada superficie necesita su propio motor: si el dashboard y el chat
compartieran modelo, una oleada de trafico en el chat dejaria el dashboard
mudo (y al reves). Groq aplica limites POR MODELO, asi que separarlos sube el
margen real de forma casi gratuita.

Configuracion (variables de entorno):
  DASHBOARD_AI_API_KEY   key del proveedor. Opcional: si falta usa
                         AI36_GROQ_API_KEY / GROQ_API_KEY.
  DASHBOARD_AI_MODEL     modelo dedicado del dashboard.
  DASHBOARD_AI_FALLBACK  modelo de respaldo si el principal falla o da 404.
  DASHBOARD_AI_BASE_URL  endpoint de chat completions (formato OpenAI).
  DASHBOARD_AI_MAX_TOKENS / _TIMEOUT / _MAX_REINTENTOS

Salida: devuelve TEXTO con el JSON del pick. No usa function-calling a
proposito: con tool-calling, el modelo puede emitir el JSON dentro del
razonamiento y la respuesta llega vacia, que es justo el fallo que se quiere
evitar aqui.
"""

import json
import os
import re
import time

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()


API_KEY = (
    os.getenv("DASHBOARD_AI_API_KEY")
    or os.getenv("AI36_GROQ_API_KEY")
    or os.getenv("GROQ_API_KEY")
    or ""
)
BASE_URL = os.getenv(
    "DASHBOARD_AI_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"
)
MODELO = os.getenv("DASHBOARD_AI_MODEL", "openai/gpt-oss-20b")
MODELO_FALLBACK = os.getenv("DASHBOARD_AI_FALLBACK", "openai/gpt-oss-120b")

MAX_TOKENS = int(os.getenv("DASHBOARD_AI_MAX_TOKENS", "2000"))
TIMEOUT = int(os.getenv("DASHBOARD_AI_TIMEOUT", "60"))
MAX_REINTENTOS = int(os.getenv("DASHBOARD_AI_MAX_REINTENTOS", "3"))
REASONING_EFFORT = os.getenv("DASHBOARD_AI_REASONING_EFFORT", "low")

DEBUG = os.getenv("DASHBOARD_AI_DEBUG", "false").lower() == "true"


def dashboard_configurado() -> bool:
    return bool(API_KEY)


def _extraer_texto(data) -> str:
    """Saca el content de la respuesta estilo OpenAI."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    return (message.get("content") or "").strip()


def _construir_payload(modelo: str, system_prompt: str, mensaje: str) -> dict:
    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensaje},
        ],
        "max_tokens": MAX_TOKENS,
    }
    if modelo.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = REASONING_EFFORT
    return payload


def generar_picks(system_prompt: str, mensaje: str):
    """Genera el texto del pick con el modelo DEDICADO del dashboard.

    Recorre modelo principal -> fallback, reintentando en 429/5xx. Devuelve
    (texto, nombre_modelo) o (None, None) si no se pudo.
    """
    if not API_KEY:
        print("[Dashboard-AI] Sin API key (define DASHBOARD_AI_API_KEY).")
        return None, None

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    ultimo_error = ""

    for modelo in (MODELO, MODELO_FALLBACK):
        if not modelo:
            continue
        payload = _construir_payload(modelo, system_prompt, mensaje)

        for intento in range(max(1, MAX_REINTENTOS)):
            try:
                r = requests.post(
                    BASE_URL, headers=headers, json=payload, timeout=TIMEOUT
                )
            except requests.exceptions.RequestException as exc:
                ultimo_error = f"conexion: {exc}"
                time.sleep(2)
                continue

            if r.status_code == 200:
                texto = _extraer_texto(r.json())
                if texto:
                    if DEBUG:
                        print(f"[Dashboard-AI] {modelo} respondio ({len(texto)} chars)")
                    return texto, modelo
                # 200 con content vacio: los gpt-oss gastan tokens en
                # razonamiento antes de emitir texto.
                ultimo_error = f"{modelo} devolvio content vacio"
                if DEBUG:
                    print(f"[Dashboard-AI] {modelo}: vacio, subiendo max_tokens")
                payload["max_tokens"] = min(MAX_TOKENS * 2, 8000)
                time.sleep(1)
                continue

            ultimo_error = f"{modelo} {r.status_code}: {r.text[:200]}"
            if DEBUG:
                print(f"[Dashboard-AI] {ultimo_error}", flush=True)

            if r.status_code == 429:
                time.sleep(min(2 ** intento * 4, 12))
                continue
            if r.status_code in (500, 502, 503):
                time.sleep(min(2 ** intento * 2, 8))
                continue
            # 404 (modelo retirado) u otro error: probar el siguiente modelo.
            break

    print(f"[Dashboard-AI] sin respuesta: {ultimo_error}", flush=True)
    return None, None
