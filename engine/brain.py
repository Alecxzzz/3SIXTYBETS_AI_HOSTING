from engine.decision_engine import DecisionEngine
from engine.prompt_builder import construir_prompt_sistema, construir_prompt_usuario
from engine.query_classifier import clasificar_consulta
from ai.model import generar_respuesta


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

    def responder_conversacion(self, mensaje_usuario: str, tipo: str, modelo: str = "groq") -> str:
        prompt_sistema = """
Eres 3SIXTYBETS AI WORKSPOT.

Responde de forma natural, clara y util.
Mantente siempre dentro del tema deportivo, apuestas, analisis, mercados, estrategia o uso de la IA.
No inventes estadisticas, cuotas, lesiones ni noticias.
Si el usuario saluda, responde breve y ofrece ayudar con un partido, una pregunta deportiva o una estrategia de apuesta.
Si el usuario hace una pregunta deportiva general, explica con criterio practico y pide el partido/mercado si necesitas mas contexto.
No uses el formato EDGE DETECTADO salvo que el usuario haya dado un partido o enfrentamiento concreto.
"""

        prompt_usuario = f"""
Tipo detectado: {tipo}
Mensaje del usuario:
{mensaje_usuario}

Responde en espanol, directo y con tono de asistente deportivo.
"""

        return generar_respuesta(prompt_sistema, prompt_usuario, modelo)

    def reforzar_decision(self, data_engine: dict, mensaje_usuario: str, modelo: str = "groq") -> str:
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

REGLA: TODO TIENE QUE SER INFORMACION DEPORTIVA DEL MES QUE ESTAMOS EN EL AÑO 2026
"""

        return generar_respuesta(prompt_sistema, prompt_usuario, modelo)

    def procesar(self, mensaje_usuario: str, modelo: str = "groq") -> str:
        tipo = clasificar_consulta(mensaje_usuario, modelo)

        if tipo in ["GENERAL_CHAT", "SPORTS_QUESTION"]:
            return self.responder_conversacion(mensaje_usuario, tipo, modelo)

        if tipo == "INVALID":
            return (
                "No pude entender bien el mensaje. Mandame un partido, una liga, "
                "un jugador o una pregunta deportiva y lo analizamos."
            )

        data_engine = self.decision_engine.construir_contexto(mensaje_usuario, modelo)

        prompt_sistema = construir_prompt_sistema()
        prompt_usuario = construir_prompt_usuario(data_engine, mensaje_usuario)

        respuesta = generar_respuesta(prompt_sistema, prompt_usuario, modelo)

        if self.respuesta_invalida(respuesta):
            respuesta = self.reforzar_decision(data_engine, mensaje_usuario, modelo)

        return respuesta
