from openai import OpenAI


API_KEY = "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"
BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "cohere/north-mini-code:free"


client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL
)


def generar_respuesta(prompt_sistema: str, prompt_usuario: str) -> str:
    try:
        respuesta = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": prompt_sistema
                },
                {
                    "role": "user",
                    "content": prompt_usuario
                }
            ],
            temperature=0.2
        )

        return respuesta.choices[0].message.content

    except Exception as e:
        return f"No se pudo generar la respuesta.\n\nDetalle:\n{str(e)}"