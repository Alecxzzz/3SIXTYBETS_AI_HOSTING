import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL_CONFIGS = {
    "groq": {
        "name": "3SIXTYBETS AI",
        "api_key": os.getenv("GROQ_API_KEY"),
        "base_url": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        "model": os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
    },
    "you": {
        "name": "You.com",
        "api_key": os.getenv("YOU_API_KEY"),
        "base_url": os.getenv("YOU_BASE_URL", "https://api.you.com/v1"),
        "model": os.getenv("YOU_MODEL", "you"),
    },
}


def normalizar_modelo(modelo: str) -> str:
    modelo = (modelo or "groq").strip().lower()
    return modelo if modelo in MODEL_CONFIGS else "groq"


def modelos_disponibles():
    return [
        {
            "id": key,
            "name": config["name"],
            "configured": bool(config["api_key"]),
        }
        for key, config in MODEL_CONFIGS.items()
    ]


def generar_respuesta(prompt_sistema: str, prompt_usuario: str, modelo: str = "groq") -> str:
    modelo = normalizar_modelo(modelo)
    config = MODEL_CONFIGS[modelo]

    if not config["api_key"]:
        return f"ERROR: Falta la API key para {config['name']} en el backend."

    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])

    respuesta = client.chat.completions.create(
        model=config["model"],
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario}
        ],
        temperature=0.2
    )

    return respuesta.choices[0].message.content
