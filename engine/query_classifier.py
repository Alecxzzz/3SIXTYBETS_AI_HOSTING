import re

from ai.model import generar_respuesta


VALID_LABELS = ["SPORTS_MATCH", "SPORTS_QUESTION", "GENERAL_CHAT", "INVALID"]


def clasificar_consulta(mensaje: str, modelo: str = "groq") -> str:
    texto = mensaje.strip()
    texto_normalizado = texto.lower()

    if len(texto) < 4:
        return "GENERAL_CHAT"

    if re.search(r"\b(vs|v\.|versus|contra)\b", texto_normalizado):
        return "SPORTS_MATCH"

    prompt_sistema = """
Clasifica el mensaje del usuario para 3SIXTYBETS AI.

Responde SOLO una etiqueta:
SPORTS_MATCH
SPORTS_QUESTION
GENERAL_CHAT
INVALID

SPORTS_MATCH =
- contiene un partido, pelea, juego, carrera o enfrentamiento entre dos equipos/jugadores.
- ejemplos: "Yankees vs Red Sox", "Lakers Celtics", "UFC Makhachev vs Topuria".

SPORTS_QUESTION =
- pregunta deportiva o de apuestas sin partido exacto.
- pregunta sobre ligas, equipos, jugadores, lesiones, cuotas, mercados, estrategia, bankroll, picks, props, tendencias o como usar la IA.
- ejemplos: "que mercado es mejor en NBA", "explicame handicap asiatico", "que opinas de los Yankees hoy".

GENERAL_CHAT =
- saludo, conversacion normal o mensaje corto que no pide analisis.
- ejemplos: "hola", "hey", "buenas", "ok", "gracias", "como estas".

INVALID =
- texto sin sentido, spam, insultos sin pregunta clara, o algo imposible de responder.

Si dudas entre SPORTS_QUESTION y GENERAL_CHAT, elige SPORTS_QUESTION cuando tenga relacion con deportes o apuestas.
Si dudas entre SPORTS_MATCH y SPORTS_QUESTION, elige SPORTS_MATCH cuando haya dos lados compitiendo.
"""

    prompt_usuario = f"Mensaje: {mensaje}\nEtiqueta:"
    respuesta = generar_respuesta(prompt_sistema, prompt_usuario, modelo).strip().upper()

    for label in VALID_LABELS:
        if label in respuesta:
            return label

    return "SPORTS_QUESTION"
