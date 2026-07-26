import os

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

MODEL_CONFIGS = {
    "groq": {
        "name": "Walter tipster",
        "api_key": os.getenv("GROQ_API_KEY"),
        "base_url": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
    },
    "you": {
        "name": "Demian tipster",
        "api_key": os.getenv("YOU_API_KEY"),
        "base_url": os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research"),
        "model": os.getenv("YOU_MODEL", "research"),
    },
}


def normalizar_modelo(modelo: str) -> str:
    modelo = (modelo or "you").strip().lower()
    return modelo if modelo in MODEL_CONFIGS else "you"


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
    groq_key = os.getenv("GROQ_API_KEY", "")
    you_key = os.getenv("YOU_API_KEY", "")
    you_search_key = os.getenv("YOU_SEARCH_API_KEY", "")
    return {
        "groq_configured": bool(groq_key),
        "groq_key_prefix": groq_key[:7] if groq_key else "",
        "groq_model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
        "groq_base_url": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        "you_configured": bool(you_key),
        "you_key_prefix": you_key[:7] if you_key else "",
        "you_search_configured": bool(you_search_key),
        "you_search_key_prefix": you_search_key[:7] if you_search_key else "",
        "you_use_research": os.getenv("YOU_USE_RESEARCH", "false"),
    }


def clean_text(text):
    return (text or "").strip()


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
        for item in data.get("hits", [])[:5]:
            title = item.get("title", "")
            snippet = item.get("snippet") or item.get("description") or ""
            context += f"{title}\n{snippet}\n\n"

        return context or "No se pudo obtener contexto externo."
    except Exception:
        return "No se pudo obtener contexto externo."


def generar_respuesta_you(prompt_sistema, prompt_usuario):
    context = buscar_contexto_you(prompt_usuario)
    prompt_con_contexto = f"""
Informacion web reciente encontrada por Demian tipster:
{context}

Solicitud del usuario:
{prompt_usuario}

Responde como Demian tipster. Usa la informacion web como apoyo, pero no inventes datos si el contexto no alcanza.
"""

    if os.getenv("YOU_USE_RESEARCH", "false").strip().lower() not in ("1", "true", "yes"):
        try:
            return clean_text(
                generar_respuesta_con_groq(
                    prompt_sistema,
                    prompt_con_contexto,
                    usar_busqueda=False,
                )
            )
        except Exception as error:
            return (
                "Demian tipster encontro contexto web, pero no pudo redactar la respuesta ahora. "
                f"Error del motor de redaccion: {error}"
            )

    api_key = os.getenv("YOU_API_KEY")
    if not api_key:
        return "ERROR: Falta la API key para You.com en el backend."

    url = os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research")
    full_prompt = f"""
{prompt_sistema}

Informacion reciente encontrada:
{context}

Solicitud del usuario:
{prompt_usuario}
"""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    payload = {
        "input": full_prompt,
        "research_effort": os.getenv("YOU_RESEARCH_EFFORT", "medium"),
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=35)
        if not response.ok:
            raise RuntimeError(f"You.com {response.status_code}: {response.text[:300]}")

        data = response.json()

        if "output" in data and "content" in data["output"]:
            text = data["output"]["content"]
        else:
            text = str(data)
    except Exception as error:
        try:
            text = generar_respuesta_con_groq(
                prompt_sistema,
                f"""
Demian tipster no pudo completar la llamada directa a You.com.
Motivo tecnico: {error}

Usa este contexto web obtenido por You Search y responde al usuario sin mencionar el error tecnico:
{context}

Solicitud original:
{prompt_usuario}
""",
                usar_busqueda=False,
            )
            if text.strip().lower().startswith("error"):
                raise RuntimeError(text)
        except Exception:
            text = (
                "Demian tipster no pudo completar la busqueda en vivo ahora. "
                "Revisa que las API keys del backend esten correctas o intenta de nuevo en unos segundos."
            )

    return clean_text(text)


def generar_respuesta_con_groq(
    prompt_sistema: str,
    prompt_usuario: str,
    usar_busqueda: bool = True,
) -> str:
    config = MODEL_CONFIGS["groq"]

    if not config["api_key"]:
        return (
            "Demian tipster no pudo terminar la busqueda en vivo y Walter tipster "
            "no esta configurado para responder como respaldo."
        )

    contexto = buscar_contexto_you(prompt_usuario) if usar_busqueda else ""
    prompt_con_contexto = prompt_usuario
    if contexto:
        prompt_con_contexto = f"""
Informacion web reciente encontrada:
{contexto}

Solicitud del usuario:
{prompt_usuario}

Usa la informacion web como apoyo, pero responde directo, claro y siempre enfocado en apuestas/deportes.
"""

    url = config["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_con_contexto},
        ],
        "temperature": float(os.getenv("GROQ_TEMPERATURE", "1")),
        "max_completion_tokens": int(os.getenv("GROQ_MAX_COMPLETION_TOKENS", "2048")),
        "top_p": float(os.getenv("GROQ_TOP_P", "1")),
        "reasoning_effort": os.getenv("GROQ_REASONING_EFFORT", "medium"),
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    if not response.ok:
        raise RuntimeError(f"Groq {response.status_code}: {response.text[:300]}")

    data = response.json()
    return (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )


def generar_respuesta(prompt_sistema: str, prompt_usuario: str, modelo: str = "groq") -> str:
    modelo = normalizar_modelo(modelo)

    if modelo == "you":
        return generar_respuesta_you(prompt_sistema, prompt_usuario)

    config = MODEL_CONFIGS[modelo]

    if not config["api_key"]:
        return f"ERROR: Falta la API key para {config['name']} en el backend."

    try:
        return generar_respuesta_con_groq(prompt_sistema, prompt_usuario)
    except Exception as error:
        return (
            f"{config['name']} no pudo responder ahora. "
            f"Error del proveedor: {error}"
        )
