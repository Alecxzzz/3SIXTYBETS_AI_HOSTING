import os

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL_CONFIGS = {
    "groq": {
        "name": "Walter tipster",
        "api_key": os.getenv("GROQ_API_KEY"),
        "base_url": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        "model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
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
    api_key = os.getenv("YOU_API_KEY")
    if not api_key:
        return "ERROR: Falta la API key para You.com en el backend."

    url = os.getenv("YOU_BASE_URL", "https://api.you.com/v1/research")
    context = buscar_contexto_you(prompt_usuario)
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
            )
            if text.strip().lower().startswith("error"):
                raise RuntimeError(text)
        except Exception:
            text = (
                "Demian tipster no pudo completar la busqueda en vivo ahora. "
                "Revisa que las API keys del backend esten correctas o intenta de nuevo en unos segundos."
            )

    return clean_text(text)


def generar_respuesta_con_groq(prompt_sistema: str, prompt_usuario: str) -> str:
    config = MODEL_CONFIGS["groq"]

    if not config["api_key"]:
        return (
            "Demian tipster no pudo terminar la busqueda en vivo y Walter tipster "
            "no esta configurado para responder como respaldo."
        )

    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
    respuesta = client.chat.completions.create(
        model=config["model"],
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ],
        temperature=0.2,
    )
    return respuesta.choices[0].message.content


def generar_respuesta(prompt_sistema: str, prompt_usuario: str, modelo: str = "groq") -> str:
    modelo = normalizar_modelo(modelo)

    if modelo == "you":
        return generar_respuesta_you(prompt_sistema, prompt_usuario)

    config = MODEL_CONFIGS[modelo]

    if not config["api_key"]:
        return f"ERROR: Falta la API key para {config['name']} en el backend."

    try:
        return generar_respuesta_con_groq(prompt_sistema, prompt_usuario)
    except Exception:
        return (
            f"{config['name']} no pudo responder ahora. "
            "Revisa que su API key del backend este correcta o intenta de nuevo en unos segundos."
        )
