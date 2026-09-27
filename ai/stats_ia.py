"""
Estadisticas AI - analisis de tendencias, jugador destacado y pronostico.

Modulo AUTOCONTENIDO, cuarto motor del ecosistema. Se separa de 365AI por el
mismo motivo que el dashboard: Groq aplica los limites POR MODELO, asi que
dandole modelo propio el chat puede saturarse sin tumbar las estadisticas
(que es justo lo que se nota cuando el usuario esta viendo un partido).

Configuracion (variables de entorno):
  STATS_AI_API_KEY   key del proveedor. Opcional: si falta usa
                     AI36_GROQ_API_KEY / GROQ_API_KEY.
  STATS_AI_MODEL     modelo dedicado de estadisticas.
  STATS_AI_FALLBACK  modelo de respaldo.
  STATS_AI_BASE_URL  endpoint de chat completions (formato OpenAI).
  STATS_AI_MAX_TOKENS / _TIMEOUT / _MAX_REINTENTOS / _REASONING_EFFORT
"""

import os
import time

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()


API_KEY = (
    os.getenv("STATS_AI_API_KEY")
    or os.getenv("AI36_GROQ_API_KEY")
    or os.getenv("GROQ_API_KEY")
    or ""
)
BASE_URL = os.getenv(
    "STATS_AI_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"
)
MODELO = os.getenv("STATS_AI_MODEL", "openai/gpt-oss-120b")
MODELO_FALLBACK = os.getenv("STATS_AI_FALLBACK", "openai/gpt-oss-20b")

# NOTA: 500 tokens era insuficiente. Los gpt-oss gastan el presupuesto en el
# razonamiento ANTES de emitir texto, y con 500 el content llegaba VACIO
# (por eso el endpoint devolvia "No se pudo generar el analisis"). 1500 da
# margen de sobra para razonamiento + las ~150 palabras de respuesta.
MAX_TOKENS = int(os.getenv("STATS_AI_MAX_TOKENS", "1500"))
TIMEOUT = int(os.getenv("STATS_AI_TIMEOUT", "45"))
MAX_REINTENTOS = int(os.getenv("STATS_AI_MAX_REINTENTOS", "3"))
REASONING_EFFORT = os.getenv("STATS_AI_REASONING_EFFORT", "low")

DEBUG = os.getenv("STATS_AI_DEBUG", "false").lower() == "true"


def stats_configurado() -> bool:
    return bool(API_KEY)


def _extraer_texto(data) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    return (message.get("content") or "").strip()


def analizar_partido(system_prompt: str, prompt_usuario: str):
    """Analiza el partido con el modelo DEDICADO de estadisticas.

    Devuelve el texto de la respuesta, o None si no se pudo. Recorre modelo
    principal -> fallback y sube max_tokens si llega content vacio.
    """
    if not API_KEY:
        print("[Stats-AI] Sin API key (define STATS_AI_API_KEY).")
        return None

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    ultimo_error = ""

    for modelo in (MODELO, MODELO_FALLBACK):
        if not modelo:
            continue
        payload = {
            "model": modelo,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_usuario},
            ],
            "max_tokens": MAX_TOKENS,
        }
        if modelo.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = REASONING_EFFORT

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
                        print(f"[Stats-AI] {modelo} respondio ({len(texto)} chars)")
                    return texto
                ultimo_error = f"{modelo} devolvio content vacio"
                if DEBUG:
                    print(f"[Stats-AI] {modelo}: vacio, subiendo max_tokens")
                payload["max_tokens"] = min(MAX_TOKENS * 2, 8000)
                time.sleep(1)
                continue

            ultimo_error = f"{modelo} {r.status_code}: {r.text[:200]}"
            if DEBUG:
                print(f"[Stats-AI] {ultimo_error}", flush=True)

            if r.status_code == 429:
                time.sleep(min(2 ** intento * 4, 12))
                continue
            if r.status_code in (500, 502, 503):
                time.sleep(min(2 ** intento * 2, 8))
                continue
            break

    print(f"[Stats-AI] sin respuesta: {ultimo_error}", flush=True)
    return None
