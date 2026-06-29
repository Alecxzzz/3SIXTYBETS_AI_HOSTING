from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from engine.brain import Brain


app = FastAPI(
    title="3SIXTYBETS AI WORKSPOT",
    description="IA deportiva con motor de decisión, búsqueda web y análisis de apuestas.",
    version="3.0"
)


brain = Brain()


class Chat(BaseModel):
    mensaje: str
    buscar: bool = True


@app.get("/", response_class=PlainTextResponse)
def inicio():
    return "3SIXTYBETS AI WORKSPOT funcionando. Entra a /docs para probar."


@app.post("/chat", response_class=PlainTextResponse)
def chat(data: Chat):

    if not data.buscar:
        return "La búsqueda web está desactivada. Activa buscar=true para usar el motor de decisión."

    respuesta = brain.procesar(data.mensaje)

    return respuesta