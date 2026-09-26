"""
Gemini (Google AI Studio) - proveedor LLM gratuito del ecosistema 3SIXTYBETS.

Por que existe: You.com funciona con creditos prepago y cuando se agotan
devuelve 402 a todos los usuarios del chat. Gemini usa la API REST
(generativelanguage.googleapis.com) con un free tier generoso, asi que sirve
como motor principal sin costo y como fallback automatico de Demian.

Se implementa con `requests` (ya en requirements.txt) en vez del SDK oficial
`google-generativeai` para no sumar dependencias al despliegue.

Configuracion por entorno:
  GEMINI_API_KEY / GOOGLE_API_KEY  (obligatoria; se toma de AI Studio, gratuita)
  GEMINI_MODEL                    (default: gemini-3.8-flash)
  GEMINI_BASE_URL                 (default: endpoint generativelanguage v1beta)
  GEMINI_TIMEOUT                  (default: 90s, el free tier puede encolar)
  GEMINI_MAX_TOKENS               (default: 2500)
  GEMINI_MAX_INPUT_CHARS          (default: 39000, por el limite de contexto)
  GEMINI_SEARCH_ENABLED           ("true" activa grounding con Google Search)
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


# ============================================================
# CONFIGURACION
# ============================================================

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or os.getenv("GOOGLE_AI_STUDIO_KEY")
    or ""
)

GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
)

# Modelos validados contra la API (2026). Google retira los flagships antiguos
# para cuentas nuevas: gemini-2.5-flash y gemini-2.0-flash devuelven 404
# "no longer available to new users", por eso la cadena empieza en 3.8-flash.
# La cadena se recorre sola: si un modelo da 404 se prueba el siguiente.
MODELO_DEFAULT = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

CADENA_MODELOS = [
    MODELO_DEFAULT,
    os.getenv("GEMINI_FALLBACK", "gemini-flash-latest"),
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]

TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "90"))
MAX_TOKENS = int(os.getenv("GEMINI_MAX_TOKENS", "2500"))
MAX_INPUT_CHARS = int(os.getenv("GEMINI_MAX_INPUT_CHARS", "39000"))
SEARCH_ENABLED = os.getenv("GEMINI_SEARCH_ENABLED", "false").lower() == "true"
DEBUG = os.getenv("GEMINI_DEBUG", "false").lower() == "true"

PREFIJO_FALTA_KEY = "ERROR: Falta la API key de Gemini en el backend."


# ============================================================
# HELPERS
# ============================================================

def get_gemini_key() -> str:
    """Lee la key en tiempo de ejecucion (permite rotarla sin reiniciar)."""
    return (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_AI_STUDIO_KEY")
        or GEMINI_API_KEY
        or ""
    )


def gemini_configurado() -> bool:
    """True si hay API key de Gemini disponible."""
    return bool(get_gemini_key())


def trim_text(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    return texto[:limite].rsplit(" ", 1)[0] + "\n\n[Contexto recortado para evitar limite de tokens.]"


def _modelos_a_probar() -> list:
    """Cadena de modelos sin duplicados, respetando el orden configurado."""
    vistos, orden = set(), []
    for modelo in CADENA_MODELOS:
        if modelo and modelo not in vistos:
            vistos.add(modelo)
            orden.append(modelo)
    return orden


def _endpoint(modelo: str) -> str:
    return f"{GEMINI_BASE_URL}/models/{modelo}:generateContent"


def _construir_payload(
    prompt_sistema: str,
    prompt_usuario: str,
    temperatura: float = 0.7,
) -> dict:
    """Arma el body de generateContent (la system instruction va aparte)."""
    system = trim_text(prompt_sistema, MAX_INPUT_CHARS // 2) if prompt_sistema else ""
    usuario = trim_text(prompt_usuario, MAX_INPUT_CHARS)

    payload = {
        "contents": [{"role": "user", "parts": [{"text": usuario}]}],
        "generationConfig": {
            "temperature": temperatura,
            "maxOutputTokens": MAX_TOKENS,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    if SEARCH_ENABLED:
        # Grounding con Google Search: lo mas cercano al research de You.com.
        payload["tools"] = [{"googleSearch": {}}]

    return payload


def _extraer_texto(data) -> str:
    """Saca el texto de la respuesta de Gemini tolerando variantes del schema."""
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return ""

    for candidato in data.get("candidates") or []:
        partes = ((candidato or {}).get("content") or {}).get("parts") or []
        textos = [p.get("text", "") for p in partes if isinstance(p, dict) and p.get("text")]
        if textos:
            return "".join(textos).strip()

    for clave in ("text", "output", "content", "answer", "result"):
        valor = data.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()

    return ""


def llamar_gemini(
    prompt_sistema: str,
    prompt_usuario: str,
    temperatura: float = 0.7,
    max_reintentos: int = 2,
) -> str:
    """Una llamada a Gemini. Devuelve texto, o "ERROR: ..." si falla.

    Recorre la cadena de modelos (el free tier va rotando disponibilidad) y
    reintenta en 429/503, que es la forma tipica de quota del free tier agotada.
    """
    key = get_gemini_key()
    if not key:
        return PREFIJO_FALTA_KEY

    payload = _construir_payload(prompt_sistema, prompt_usuario, temperatura)
    ultimo_error = ""

    for modelo in _modelos_a_probar():
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        }

        for intento in range(max(1, max_reintentos)):
            try:
                response = requests.post(
                    _endpoint(modelo),
                    headers=headers,
                    json=payload,
                    timeout=TIMEOUT,
                )
            except Exception as error:  # timeout / red / DNS
                ultimo_error = f"no se pudo conectar con Gemini ({error})"
                if DEBUG:
                    print(f"[gemini] {modelo} intento {intento + 1}: {error}", flush=True)
                continue

            if response.ok:
                texto = _extraer_texto(response.json())
                if texto:
                    return texto
                ultimo_error = "Gemini devolvio una respuesta vacia"
                if DEBUG:
                    print(f"[gemini] {modelo}: respuesta vacia", flush=True)
                break

            status = response.status_code
            detalle = response.text[:300]
            ultimo_error = f"Gemini {status}: {detalle}"

            if status == 400 and "API key" in detalle:
                # Key invalida: no tiene sentido reintentar ningun modelo.
                return f"ERROR: La API key de Gemini no es valida ({detalle})"

            if status == 429:
                # Cuota del free tier: esperar y reintentar el mismo modelo.
                ultimo_error = (
                    "Gemini 429: cuota gratuita agotada temporalmente, "
                    "reintentando en unos segundos."
                )
                time.sleep(min(2 ** intento, 8))
                continue

            if status in (500, 503):
                time.sleep(min(2 ** intento, 8))
                continue

            if status in (400, 404):
                # Modelo no disponible en la cuenta -> siguiente de la cadena.
                if DEBUG:
                    print(f"[gemini] modelo {modelo} no disponible: {detalle}", flush=True)
                break

            return f"ERROR: {ultimo_error}"

    return f"ERROR: {ultimo_error or 'Gemini no respondio.'}"


def generar_respuesta_gemini(prompt_sistema: str, prompt_usuario: str) -> str:
    """Respuesta de Gemini, con un reintento a temperatura 0 si el primer
    intento devuelve vacio o error recuperable.
    """
    respuesta = llamar_gemini(prompt_sistema, prompt_usuario, temperatura=0.7)
    if respuesta and not respuesta.startswith("ERROR:"):
        return respuesta

    segundo = llamar_gemini(prompt_sistema, prompt_usuario, temperatura=0.2)
    if segundo and not segundo.startswith("ERROR:"):
        return segundo

    return respuesta or segundo or "ERROR: Gemini no genero respuesta."
