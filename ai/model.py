import os

import requests

from engine.search_engine import SearchEngine, normalizar_research_effort
import youkeys

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

MODEL_CONFIGS = {
    "you": {
        "name": "Demian tipster",
        "api_key": youkeys.get_you_key(),
        "base_url": os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research"),
        "model": os.getenv("YOU_MODEL", "research"),
    },
    "36ai": {
        "name": "365AI",
        "api_key": os.getenv("AI36_GROQ_API_KEY") or os.getenv("GROQ_API_KEY"),
        "base_url": os.getenv("AI36_GROQ_URL", "https://api.groq.com/openai/v1/chat/completions"),
        "model": os.getenv("AI36_GROQ_MODEL", "openai/gpt-oss-120b"),
    },
}

YOU_CONTEXT_MAX_CHARS = int(os.getenv("YOU_CONTEXT_MAX_CHARS", "1200"))


def normalizar_modelo(modelo: str) -> str:
    if modelo and str(modelo).strip().lower() in ("36ai", "36", "ia36"):
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
    you_key = youkeys.get_you_key()
    you_search_key = youkeys.get_you_search_key()
    return {
        "you_configured": bool(you_key),
        "you_key_prefix": you_key[:7] if you_key else "",
        "you_search_configured": bool(you_search_key),
        "you_search_key_prefix": you_search_key[:7] if you_search_key else "",
        "you_use_research": os.getenv("YOU_USE_RESEARCH", "false"),
    }


def clean_text(text):
    return (text or "").strip()


def trim_text(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text

    return text[:limit].rsplit(" ", 1)[0] + "\n\n[Contexto recortado para evitar limite de tokens.]"


def buscar_contexto_you(question):
    """Contexto de estadisticas via You.com Smart/Answer (/v1/answer).

    Limitado a sofascore.com y flashscore.com (include_domains), en
    espanol, research_effort deep y safesearch strict.
    """
    try:
        texto = SearchEngine().answer(question, research_effort="deep")
        if not texto or texto.startswith(("ERROR", "Error de You.com", "Error leyendo")):
            return "No se pudo obtener contexto externo."
        return trim_text(texto, int(os.getenv("YOU_CONTEXT_MAX_CHARS", "1800")))
    except Exception:
        return "No se pudo obtener contexto externo."


def generar_respuesta_you(prompt_sistema, prompt_usuario):
    search_engine = SearchEngine()
    research_effort = normalizar_research_effort(os.getenv("YOU_RESEARCH_EFFORT", "deep"))

    try:
        respuesta = search_engine.ask_you(
            prompt_usuario,
            system_prompt=prompt_sistema,
            research_effort=research_effort,
        )
        if respuesta and not str(respuesta).startswith("ERROR:") and not str(respuesta).startswith("Error leyendo respuesta"):
            return clean_text(respuesta)
    except Exception:
        pass

    # Fallback: consulta directa a /v1/research con el prompt compuesto
    api_key = youkeys.get_you_key() or youkeys.get_you_search_key()
    if not api_key:
        return "ERROR: Falta la API key para You.com en el backend."

    full_prompt = f"""
{prompt_sistema}

Solicitud del usuario:
{trim_text(prompt_usuario, 1200)}
"""

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    payload = {
        "input": trim_text(full_prompt, 39000),
        "research_effort": research_effort,
        "background": False,
        "freshness": os.getenv("YOU_FRESHNESS", "day"),
        "safesearch": os.getenv("YOU_SAFESEARCH", "strict"),
        "language": os.getenv("YOU_LANGUAGE", "ES"),
        "extraction": {
            "extraction_mode": "full_page",
            "extraction_source": "fetch",
        },
        "include_domains": [
            d.strip() for d in os.getenv(
                "YOU_INCLUDE_DOMAINS", "sofascore.com,flashscore.com"
            ).split(",") if d.strip()
        ],
    }

    try:
        response = requests.post(
            os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research"),
            headers=headers,
            json=payload,
            timeout=90,
        )
        if not response.ok:
            raise RuntimeError(f"You.com {response.status_code}: {response.text[:300]}")

        data = response.json()

        if isinstance(data, dict):
            if "output" in data and isinstance(data["output"], dict) and "content" in data["output"]:
                return clean_text(data["output"]["content"])
            for key in ("answer", "content", "text", "result"):
                if key in data and isinstance(data[key], str):
                    return clean_text(data[key])
            if "output" in data and isinstance(data["output"], str):
                return clean_text(data["output"])

        return clean_text(str(data))
    except Exception as error:
        return (
            "Demian tipster no pudo completar la busqueda en vivo ahora. "
            f"Motivo tecnico: {error}"
        )


def generar_respuesta(prompt_sistema: str, prompt_usuario: str, modelo: str = "you") -> str:
    modelo = normalizar_modelo(modelo)
    if modelo == "36ai":
        from ai.ia36 import generar_respuesta_36ai
        return generar_respuesta_36ai(prompt_sistema, prompt_usuario)
    return generar_respuesta_you(prompt_sistema, prompt_usuario)
