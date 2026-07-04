from ai.model import generar_respuesta


def clasificar_consulta(mensaje: str) -> str:
    texto = mensaje.strip()

    if len(texto) < 4:
        return "GENERAL_CHAT"

    prompt_sistema = """
Clasifica el mensaje del usuario.

Responde SOLO una etiqueta:
SPORTS_MATCH
SPORTS_QUESTION
GENERAL_CHAT
INVALID

SPORTS_MATCH = contiene un partido, pelea, juego o enfrentamiento entre dos equipos/jugadores.
GENERAL_CHAT = saludo o conversación normal.
SPORTS_QUESTION = pregunta deportiva general.
INVALID = texto sin sentido.

Si el mensaje es "hola", "hey", "buenas", "ok", "gracias" o parecido, responde GENERAL_CHAT.
"""

    prompt_usuario = f"Mensaje: {mensaje}\nEtiqueta:"

    respuesta = generar_respuesta(prompt_sistema, prompt_usuario).strip().upper()

    if respuesta not in ["SPORTS_MATCH", "SPORTS_QUESTION", "GENERAL_CHAT", "INVALID"]:
        return "INVALID"

    return respuesta