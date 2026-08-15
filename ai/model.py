import os

import requests

from engine.search_engine import SearchEngine

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

MODEL_CONFIGS = {
    "you": {
        "name": "Demian tipster",
        "api_key": os.getenv("YOU_API_KEY"),
        "base_url": os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research"),
        "model": os.getenv("YOU_MODEL", "research"),
    },
}

YOU_CONTEXT_MAX_CHARS = int(os.getenv("YOU_CONTEXT_MAX_CHARS", "1200"))


def normalizar_modelo(modelo: str) -> str:
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
    you_key = os.getenv("YOU_API_KEY", "")
    you_search_key = os.getenv("YOU_SEARCH_API_KEY", "")
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
    api_key = os.getenv("YOU_SEARCH_API_KEY") or os.getenv("YOU_API_KEY")
    if not api_key:
        return "No se pudo obtener contexto externo."

    try:
        ydc_url = os.getenv("YOU_SEARCH_URL", "https://ydc-index.io/v1/search")
        querystring = {
            "query": question,
            "count": "5",
            "freshness": "day",
            "language": "ES",
            "safesearch": "off",
            "crawl_timeout": "10",
        }
        headers = {
            "X-API-KEY": api_key,
            "Accept": "application/json",
        }
        response = requests.get(
            ydc_url,
            headers=headers,
            params=querystring,
            timeout=15,
        )
        data = response.json()

        context = ""
        for item in data.get("hits", [])[:3]:
            title = item.get("title", "")
            snippet = item.get("snippet") or item.get("description") or ""
            context += f"{trim_text(title, 120)}\n{trim_text(snippet, 450)}\n\n"

        return trim_text(context, int(os.getenv("YOU_CONTEXT_MAX_CHARS", "1800"))) or "No se pudo obtener contexto externo."
    except Exception:
        return "No se pudo obtener contexto externo."


def generar_respuesta_you(prompt_sistema, prompt_usuario):
    search_engine = SearchEngine()
    research_effort = os.getenv("YOU_RESEARCH_EFFORT", "medium")

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

    api_key = os.getenv("YOU_API_KEY")
    if not api_key:
        return "ERROR: Falta la API key para You.com en el backend."

    context = buscar_contexto_you(prompt_usuario)
    url = os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research")
    full_prompt = f"""
{prompt_sistema}

Informacion reciente encontrada:
{trim_text(context, int(os.getenv("YOU_CONTEXT_MAX_CHARS", "1800")))}

Solicitud del usuario:
{trim_text(prompt_usuario, 1200)}
"""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    payloads = [
        {
            "query": full_prompt,
            "research_effort": os.getenv("YOU_RESEARCH_EFFORT", "medium"),
            "background": False,
        },
        {
            "input": full_prompt,
            "research_effort": os.getenv("YOU_RESEARCH_EFFORT", "medium"),
            "background": False,
        },
    ]

    last_error = None
    for payload in payloads:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=35)
            if not response.ok:
                if response.status_code != 422:
                    raise RuntimeError(f"You.com {response.status_code}: {response.text[:300]}")
                last_error = RuntimeError(f"You.com {response.status_code}: {response.text[:300]}")
                continue

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
            last_error = error
            break

    return (
        "Demian tipster no pudo completar la busqueda en vivo ahora. "
        f"Motivo tecnico: {last_error}"
    )


def generar_respuesta(prompt_sistema: str, prompt_usuario: str, modelo: str = "you") -> str:
    normalizar_modelo(modelo)
    return generar_respuesta_you(prompt_sistema, prompt_usuario)
