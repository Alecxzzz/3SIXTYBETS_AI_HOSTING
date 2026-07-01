from ai.model import generar_respuesta


def clasificar_consulta(mensaje: str) -> str:
    prompt_sistema = """
Eres un clasificador de mensajes para una IA deportiva.

Debes responder SOLO una de estas etiquetas:

SPORTS_MATCH
SPORTS_QUESTION
GENERAL_CHAT
INVALID

Definiciones:

SPORTS_MATCH:
El usuario escribió un partido, evento o enfrentamiento deportivo que puede analizarse.
Ejemplos:
- Yankees vs Red Sox
- Lakers Celtics
- Real Madrid vs PSG
- Djokovic contra Sinner
- analiza Dodgers vs Mets
- Uruguay España

SPORTS_QUESTION:
El usuario hace una pregunta deportiva general, pero NO pide analizar un partido específico.
Ejemplos:
- qué es un RBI
- qué es handicap asiático
- cómo funciona el over 2.5

GENERAL_CHAT:
Saludo, conversación normal o mensaje no deportivo.
Ejemplos:
- hola
- qué haces
- gracias
- cómo estás

INVALID:
Texto vacío, basura, insultos sueltos o algo imposible de entender.
"""

    prompt_usuario = f"""
Mensaje:
{mensaje}

Etiqueta:
"""

    respuesta = generar_respuesta(prompt_sistema, prompt_usuario).strip().upper()

    etiquetas_validas = {
        "SPORTS_MATCH",
        "SPORTS_QUESTION",
        "GENERAL_CHAT",
        "INVALID"
    }

    if respuesta not in etiquetas_validas:
        return "INVALID"

    return respuesta