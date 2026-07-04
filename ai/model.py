import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "llama-3.1-8b-instant"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def generar_respuesta(prompt_sistema: str, prompt_usuario: str) -> str:
    if not API_KEY:
        return "ERROR: Falta GROQ_API_KEY en Northflank."

    respuesta = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario}
        ],
        temperature=0.2
    )

    return respuesta.choices[0].message.content