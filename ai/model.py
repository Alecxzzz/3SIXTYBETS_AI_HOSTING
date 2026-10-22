"""Configuracion y despacho de las IAs del ecosistema 3SIXTYBETS.

Demian tipster corre sobre OpenRouter (``DEMIAN_AI_*``) y 365AI delega en
``ai.ia36`` (OpenRouter o Groq). No hay proveedores adicionales.
"""

import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

from ai.claves import claves_openrouter, es_respaldo

DEMIAN_API_KEY = os.getenv("DEMIAN_AI_API_KEY") or ""
# Demian corre SIEMPRE sobre OpenRouter, asi que hereda la clave de respaldo
# incorporada: si la cuenta principal agota su cupo diario, el chat sigue vivo
# con la otra cuenta sin tocar configuracion.
DEMIAN_API_KEYS = claves_openrouter(
    DEMIAN_API_KEY, os.getenv("DEMIAN_AI_API_KEY_FALLBACK")
)
DEMIAN_MODEL = os.getenv("DEMIAN_AI_MODEL", "nvidia/nemotron-3.5-super-vl:free")
DEMIAN_FALLBACK = os.getenv(
    "DEMIAN_AI_FALLBACK", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
)
DEMIAN_REASONING = os.getenv("DEMIAN_AI_REASONING", "low")
DEMIAN_MAX_TOKENS = int(os.getenv("DEMIAN_AI_MAX_TOKENS", "1800"))
DEMIAN_TEMPERATURE = float(os.getenv("DEMIAN_AI_TEMPERATURE", "0.6"))

# 365AI puede correr sobre OpenRouter (recomendado) o sobre Groq. Si existe
# AI36_OPENROUTER_API_KEY manda OpenRouter; si no, se conserva la config de Groq
# para no romper despliegues viejos.
AI36_API_KEY = (
    os.getenv("AI36_OPENROUTER_API_KEY")
    or os.getenv("AI36_GROQ_API_KEY")
    or os.getenv("GROQ_API_KEY")
    or ""
)
AI36_BASE_URL = os.getenv(
    "AI36_OPENROUTER_URL",
    os.getenv("AI36_GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"),
)
AI36_MODEL = os.getenv(
    "AI36_OPENROUTER_MODEL",
    os.getenv("AI36_GROQ_MODEL", "openai/gpt-oss-120b"),
)

# El id "you" se conserva porque el frontend lo manda en /chat desde siempre;
# apunta a OpenRouter.
MODEL_CONFIGS = {
    "you": {
        "name": "Demian tipster",
        "api_key": DEMIAN_API_KEY,
        "base_url": os.getenv(
            "DEMIAN_AI_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
        ),
        "model": DEMIAN_MODEL,
    },
    "36ai": {
        "name": "365AI",
        "api_key": AI36_API_KEY,
        "base_url": AI36_BASE_URL,
        "model": AI36_MODEL,
    },
}


def normalizar_modelo(modelo: str) -> str:
    modelo_id = str(modelo or "").strip().lower()
    if modelo_id in ("36ai", "36", "ia36", "groq"):
        return "36ai"
    return "you"


def modelos_disponibles():
    return [
        {
            "id": key,
            "name": config["name"],
            "configured": bool(config["api_key"]),
        }
        for key, config in MODEL_CONFIGS.items()
    ]


def env_diagnostics():
    return {
        "demian_configured": bool(DEMIAN_API_KEYS),
        "demian_key_prefix": DEMIAN_API_KEY[:10] if DEMIAN_API_KEY else "",
        "demian_claves": len(DEMIAN_API_KEYS),
        "demian_usa_respaldo": any(es_respaldo(k) for k in DEMIAN_API_KEYS),
        "demian_model": DEMIAN_MODEL,
        "demian_fallback": DEMIAN_FALLBACK or None,
        "ai36_configured": bool(AI36_API_KEY),
        "ai36_model": AI36_MODEL,
        "ai36_provider": "openrouter" if os.getenv("AI36_OPENROUTER_API_KEY") else "groq",
        # Compat: clientes viejos leen groq_configured.
        "groq_configured": bool(AI36_API_KEY),
    }


def generar_respuesta_demian(prompt_sistema: str, prompt_usuario: str):
    """Demian tipster sobre OpenRouter. None si no pudo (el caller hace relevo)."""
    if not DEMIAN_API_KEYS:
        return None

    from ai.openrouter import generar

    texto, _modelo = generar(
        prompt_sistema,
        prompt_usuario,
        api_key=DEMIAN_API_KEYS,
        model=DEMIAN_MODEL,
        fallback=DEMIAN_FALLBACK,
        max_tokens=DEMIAN_MAX_TOKENS,
        temperature=DEMIAN_TEMPERATURE,
        reasoning=DEMIAN_REASONING,
    )
    return texto or None


def generar_respuesta(prompt_sistema: str, prompt_usuario: str, modelo: str = "you") -> str:
    """Despacho sincrono. Devuelve str ('' si el proveedor no respondio)."""
    if normalizar_modelo(modelo) == "36ai":
        from ai.ia36 import generar_respuesta_36ai

        return generar_respuesta_36ai(prompt_sistema, prompt_usuario)

    return generar_respuesta_demian(prompt_sistema, prompt_usuario) or ""
