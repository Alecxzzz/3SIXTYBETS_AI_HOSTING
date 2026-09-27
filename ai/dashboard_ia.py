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

CONFIGURACION DE BUSQUEDA WEB

El dashboard genera picks, asi que necesita informacion FRESCA (lesiones,
cuotas, forma reciente). Antes este motor era Demian/You.com, que hacia
research web con include_domains. Al darle modelo Groq propio se perdio esa
capacidad, asi que ahora el modulo busca en la web ANTES de generar el pick y
le pasa los resultados al modelo como contexto.

Busqueda: ai.ia36.buscar_web (DuckDuckGo, ya anade mes y anio a la consulta).
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


# Consultas que se lanzan antes de pedir el pick. Se centran en lo que mas
# cambia entre partidos y no se puede deducir de la tabla de posiciones.
CONSULTAS_BUSQUEDA = [
    "{partido} lesiones y bajas hoy",
    "{partido} cuotas y pronosticos",
    "{partido} forma reciente ultimos partidos",
]
RESULTADOS_POR_BUSQUEDA = 3
MAX_CHARS_BUSQUEDA = int(os.getenv("DASHBOARD_AI_MAX_CHARS_BUSQUEDA", "2500"))

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


def buscar_contexto_web(mensaje: str) -> str:
    """Busca en la web lo que un pick necesita y devuelve el contexto.

    Reutiliza ai.ia36.buscar_web (DuckDuckGo) porque ya anade mes y anio a la
    consulta, que es lo que evita que el modelo se apoye en datos de temporadas
    pasadas. Se degrada a cadena vacia si la busqueda falla: es preferible
    generar el pick con menos contexto que no generar nada.
    """
    if os.getenv("DASHBOARD_AI_WEB", "true").lower() != "true":
        return ""

    try:
        from ai.ia36 import buscar_web
    except Exception as exc:
        print(f"[Dashboard-AI] busqueda web no disponible: {exc}", flush=True)
        return ""

    # El "mensaje" del dashboard suele traer el partido; se recorta para no
    # lanzar consultas gigantes que devuelven nada util.
    partido = (mensaje or "").strip()[:120]
    if not partido:
        return ""

    bloques = []
    for plantilla in CONSULTAS_BUSQUEDA:
        consulta = plantilla.format(partido=partido)
        try:
            resultado = buscar_web(consulta, max_resultados=RESULTADOS_POR_BUSQUEDA)
        except Exception as exc:
            resultado = f"(error: {exc})"
        if resultado and "Error" not in resultado[:20]:
            bloques.append(f"=== {consulta} ===\n{resultado}")

    if not bloques:
        return ""

    contexto = "\n\n".join(bloques)
    if len(contexto) > MAX_CHARS_BUSQUEDA:
        contexto = contexto[:MAX_CHARS_BUSQUEDA] + "\n[contexto recortado]"
    return contexto


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

    Antes de preguntar al modelo se hace una busqueda web (lesiones, cuotas,
    forma) y el resultado se inyecta como contexto, para que el pick se apoye
    en datos frescos y no en memoria del modelo. Si la busqueda falla, se sigue
    adelante sin ella: es preferible un pick con menos contexto que ninguno.

    Recorre modelo principal -> fallback, reintentando en 429/5xx. Devuelve
    (texto, nombre_modelo) o (None, None) si no se pudo.
    """
    if not API_KEY:
        print("[Dashboard-AI] Sin API key (define DASHBOARD_AI_API_KEY).")
        return None, None

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    ultimo_error = ""

    # Contexto web (una sola vez, compartido por modelo principal y fallback).
    contexto_web = buscar_contexto_web(mensaje)
    if DEBUG:
        print(f"[Dashboard-AI] contexto web: {len(contexto_web)} chars", flush=True)

    system_con_contexto = system_prompt
    if contexto_web:
        system_con_contexto = (
            system_prompt
            + "\n\nCONTEXTO DE BUSQUEDA WEB (datos frescos, es la fuente de "
            "verdad; no inventes datos que no aparezcan aqui):\n"
            + contexto_web
        )

    for modelo in (MODELO, MODELO_FALLBACK):
        if not modelo:
            continue
        payload = _construir_payload(modelo, system_con_contexto, mensaje)

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
                # content vacio aqui SI es fallo: este modulo no usa tools, asi
                # que no puede ser una peticion de herramienta. Es el razonamiento
                # del gpt-oss consumiendo el presupuesto de max_tokens.
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
