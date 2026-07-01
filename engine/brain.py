from engine.decision_engine import DecisionEngine
from engine.prompt_builder import construir_prompt_sistema, construir_prompt_usuario
from ai.model import generar_respuesta
from engine.query_classifier import clasificar_consulta

class Brain:
    def __init__(self):
        self.decision_engine = DecisionEngine()

    def respuesta_invalida(self, texto: str) -> bool:
        if not texto:
            return True

        t = texto.lower()

        frases_malas = [
            "no hay datos suficientes",
            "no se dispone de estadísticas",
            "no puedo recomendar",
            "no se puede recomendar",
            "falta de datos",
            "no encontré datos",
            "no se encontraron datos"
        ]

        return any(frase in t for frase in frases_malas)

    def reforzar_decision(self, data_engine: dict, mensaje_usuario: str) -> str:
        prompt_sistema = construir_prompt_sistema()

        prompt_usuario = construir_prompt_usuario(data_engine, mensaje_usuario)

        prompt_usuario += """

REGLA EXTRA DEL BRAIN:

Ya se hizo búsqueda web.
Ya se recolectó información.
Ahora debes tomar una decisión.

Si el nivel de evidencia es MEDIA o ALTA:
- NO puedes responder que no hay datos suficientes.
- Debes elegir el mercado más lógico.
- Si no hay cuota exacta, escribe: cuota no confirmada.
- Si no hay mercado exacto, recomienda el tipo de mercado más razonable.
- Mantén el formato obligatorio.

Si el nivel de evidencia es BAJA:
- Puedes bajar la confianza.
- Pero intenta dar una oportunidad conservadora si hay alguna tendencia útil.
"""

        return generar_respuesta(prompt_sistema, prompt_usuario)

def procesar(self, mensaje_usuario: str) -> str:
    tipo = clasificar_consulta(mensaje_usuario)

    if tipo != "SPORTS_MATCH":
        return (
            "Escribe un partido o evento deportivo para analizar.\n\n"
            "Ejemplos:\n"
            "- Yankees vs Red Sox\n"
            "- Lakers vs Celtics\n"
            "- Real Madrid vs PSG\n"
            "- Djokovic vs Sinner"
        )

    data_engine = self.decision_engine.construir_contexto(mensaje_usuario)

    prompt_sistema = construir_prompt_sistema()
    prompt_usuario = construir_prompt_usuario(data_engine, mensaje_usuario)

    respuesta = generar_respuesta(prompt_sistema, prompt_usuario)

    if self.respuesta_invalida(respuesta):
        respuesta = self.reforzar_decision(data_engine, mensaje_usuario)

    return respuesta